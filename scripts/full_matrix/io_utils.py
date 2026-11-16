"""Small, dependency-free helpers shared by the full-matrix gates."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SECRET_PATTERNS = (
    re.compile(r"(?i)(password|passwd|pwd)\s*[=:]\s*([^\s,;]+)"),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)([^\s]+)"),
    re.compile(r"(?i)(set-cookie\s*:\s*)([^\r\n]+)"),
    re.compile(r"(?i)(access_token\s*[=:]\s*)([^\s,;]+)"),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def redact_text(text: str, extra_secrets: Iterable[str] = ()) -> str:
    safe = text
    for secret in extra_secrets:
        if secret:
            safe = safe.replace(secret, "[REDACTED]")
    for pattern in SECRET_PATTERNS:
        safe = pattern.sub(lambda m: m.group(1) + "[REDACTED]", safe)
    return safe


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(dict(value), ensure_ascii=False, default=str) + "\n")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


@dataclass
class CommandResult:
    command: list[str]
    cwd: str
    exit_code: int
    duration_ms: int
    log_path: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    log_path: Path,
    timeout_seconds: int = 3600,
    check: bool = False,
    extra_secrets: Iterable[str] = (),
) -> CommandResult:
    """Run one command, tee sanitized output to a log, and return its result.

    The caller decides whether a non-zero exit is a gate failure.  With
    ``check=True`` a non-zero result raises immediately, which is used by G0/G1
    so a later gate can never silently run on a broken prerequisite.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    timed_out = False
    output: list[str] = []
    try:
        completed = subprocess.run(
            list(command),
            cwd=str(cwd),
            env=dict(env) if env is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        exit_code = completed.returncode
        output.append(completed.stdout or "")
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = 124
        partial = exc.stdout or ""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", "replace")
        output.append(partial)
        output.append(f"\n[TIMEOUT] exceeded {timeout_seconds}s\n")
    duration_ms = int((time.monotonic() - started) * 1000)
    safe_output = redact_text("".join(output), extra_secrets=extra_secrets)
    log_path.write_text(safe_output, encoding="utf-8", newline="\n")
    result = CommandResult(
        command=list(command),
        cwd=str(cwd),
        exit_code=exit_code,
        duration_ms=duration_ms,
        log_path=str(log_path),
        timed_out=timed_out,
    )
    if check and not result.ok:
        raise RuntimeError(f"command failed ({exit_code}): {log_path}")
    return result


def case_row(
    *,
    case_id: str,
    phase: str,
    owner: str,
    role: str,
    mutability: str,
    precondition: str,
    depends_on: Sequence[str] = (),
    steps: Sequence[Mapping[str, Any]] = (),
    expected: Sequence[str] = (),
    actual: Mapping[str, Any] | None = None,
    status: str = "PASS",
    severity: str = "NONE",
    evidence: Sequence[Mapping[str, Any]] = (),
    repro: Mapping[str, Any] | None = None,
    attempt: int = 1,
    defect_id: str | None = None,
    fix_commit: str | None = None,
    duration_ms: int = 0,
) -> dict[str, Any]:
    if status not in {"PASS", "FAIL", "ERROR", "BLOCKED", "SKIP"}:
        raise ValueError(f"invalid case status: {status}")
    return {
        "id": case_id,
        "phase": phase,
        "owner": owner,
        "role": role,
        "mutability": mutability,
        "precondition": precondition,
        "depends_on": list(depends_on),
        "steps": list(steps),
        "expected": list(expected),
        "actual": dict(actual or {}),
        "status": status,
        "severity": severity,
        "evidence": list(evidence),
        "repro": dict(repro or {}),
        "attempt": attempt,
        "defect_id": defect_id,
        "fix_commit": fix_commit,
        "duration_ms": duration_ms,
    }


def summarize_cases(cases: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    summary = {key: 0 for key in ("total", "pass", "fail", "error", "blocked", "skipped")}
    summary["total"] = len(cases)
    mapping = {
        "PASS": "pass",
        "FAIL": "fail",
        "ERROR": "error",
        "BLOCKED": "blocked",
        "SKIP": "skipped",
    }
    for item in cases:
        key = mapping.get(str(item.get("status")), "error")
        summary[key] += 1
    return summary