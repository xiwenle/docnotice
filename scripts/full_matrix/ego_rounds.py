"""Runner for the single-TaskSpace ego-browser rounds."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .config import MatrixConfig
from .io_utils import append_jsonl, read_json, run_command, utc_now, write_json

PLACEHOLDER = "__FULL_MATRIX_EVIDENCE_ROOT__"
TEMPLATE_ROOT = Path(__file__).resolve().parent / "ego_templates"


def sync_round_templates(cfg: MatrixConfig) -> None:
    """Materialize versioned ego .mjs templates into this run's ignored evidence dir."""
    if not TEMPLATE_ROOT.is_dir():
        return
    target_root = cfg.evidence_root / "ego"
    for source in sorted(TEMPLATE_ROOT.rglob("*.mjs")):
        relative = source.relative_to(TEMPLATE_ROOT)
        target = target_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        text = source.read_text(encoding="utf-8")
        text = text.replace(PLACEHOLDER, cfg.evidence_root.as_posix())
        text = text.replace("__FULL_MATRIX_PORT__", str(cfg.port))
        text = text.replace("__FULL_MATRIX_RUN_ID__", cfg.run_id)
        text = text.replace("__FULL_MATRIX_TASK_SPACE__", cfg.task_space)
        target.write_text(text, encoding="utf-8", newline="\n")


ROUND_ORDER = [
    "round-00-smoke",
    "round-01-roles",
    "round-02-documents",
    "round-03-notices",
    "round-04-security",
    "round-05-destructive",
    "round-06-attachment-recycle",
    "round-07-notice-search",
    "round-08-admin-vocab",
    "round-09-validity-files",
    "round-10-notice-files",
    "round-11-backup-audit",
]
FINAL_ROUND = "round-99-final"


def _ego_exe() -> str:
    exe = shutil.which("ego-browser")
    if not exe:
        raise RuntimeError("ego-browser is not on PATH; install it before running ego rounds")
    return exe


def _round_path(cfg: MatrixConfig, round_id: str) -> Path:
    path = cfg.evidence_root / "ego" / "rounds" / f"{round_id}.mjs"
    if not path.is_file():
        raise FileNotFoundError(f"ego round script not found: {path}")
    return path


def _result_path(cfg: MatrixConfig, round_id: str) -> Path:
    return cfg.evidence_root / "ego" / "results" / round_id / "result.json"


def _write_error_result(cfg: MatrixConfig, round_id: str, message: str) -> None:
    payload = {
        "runId": cfg.run_id,
        "roundId": round_id,
        "taskSpace": cfg.task_space,
        "app": {"baseUrl": cfg.base_url},
        "cases": [{
            "id": f"EGO-{round_id}-RUNNER",
            "phase": round_id,
            "owner": "H",
            "role": "system",
            "mutability": "ENV_EXCLUSIVE",
            "precondition": "ego round executable",
            "depends_on": [],
            "steps": [],
            "expected": ["ego-browser round exits successfully and writes result.json"],
            "actual": {"error": message},
            "status": "ERROR",
            "severity": "P0",
            "evidence": [],
            "repro": {"command": f"python -m scripts.full_matrix.run ego {round_id}"},
            "attempt": 1,
            "defect_id": None,
            "fix_commit": None,
            "duration_ms": 0,
        }],
        "summary": {"total": 1, "pass": 0, "fail": 0, "error": 1, "blocked": 0, "skipped": 0},
    }
    write_json(_result_path(cfg, round_id), payload)


def _round_status(payload: dict[str, Any]) -> dict[str, int]:
    cases = payload.get("cases") or []
    status = {"total": len(cases), "pass": 0, "fail": 0, "error": 0, "blocked": 0, "skipped": 0}
    mapping = {"PASS": "pass", "FAIL": "fail", "ERROR": "error", "BLOCKED": "blocked", "SKIP": "skipped"}
    for item in cases:
        status[mapping.get(item.get("status"), "error")] += 1
    return status


def _recycle_pages(cfg: MatrixConfig, round_id: str) -> dict[str, Any]:
    """Close only the Pages a round declared disposable.

    The task space allows 8 managed Pages for the whole run, so a round that opens
    throw-away Pages must release them or later rounds fail with PageBudgetError.
    Cleanup is driven by `<evidence>/ego/results/<round>/disposable-pages.json`,
    written by the round itself; nothing is inferred here. An earlier version
    closed every Page outside a hard-coded keep-list, which closed Pages shared
    with later rounds (the same persona is named `user`, `user_a` and `user-a`
    across rounds) and broke them with "Inspected target navigated or closed".
    """
    script = cfg.evidence_root / "ego" / "lib" / "recycle_pages.mjs"
    if not script.is_file():
        return {"skipped": True, "reason": "recycle script not synced"}
    # ego-browser's `nodejs` subcommand does not forward environment variables, so
    # the round id has to be baked into a per-round copy.  Passing it via
    # cfg.child_env() looked right and did nothing: every run reported
    # "no round id supplied" and recycled zero Pages.
    scripted = script.with_name(f"recycle-pages-{round_id}.mjs")
    scripted.write_text(
        script.read_text(encoding="utf-8").replace("__FULL_MATRIX_ROUND_ID__", round_id),
        encoding="utf-8",
        newline="\n",
    )
    command = [_ego_exe(), "nodejs", "-e", f"await import('file:///{scripted.as_posix()}')"]
    result = run_command(
        command,
        cwd=cfg.source_root,
        env=cfg.child_env(),
        log_path=cfg.evidence_root / "ego" / "results" / round_id / "recycle-pages.log",
        timeout_seconds=120,
        check=False,
        extra_secrets=(cfg.initial_admin_password, cfg.admin_password, cfg.user_password),
    )
    return {
        "exit_code": result.exit_code,
        "ok": result.exit_code == 0,
        "log": result.log_path,
    }


def run_round(cfg: MatrixConfig, round_id: str) -> dict[str, Any]:
    cfg.ensure_layout()
    sync_round_templates(cfg)
    path = _round_path(cfg, round_id)
    result_path = _result_path(cfg, round_id)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    # A retry should start from a clean result; otherwise a stale ERROR case can
    # remain in the payload even after the browser round itself succeeds.
    if result_path.exists():
        result_path.unlink()
    command = [_ego_exe(), "nodejs", "-e", f"await import('file:///{path.as_posix()}')"]
    result = run_command(
        command,
        cwd=cfg.source_root,
        env=cfg.child_env(),
        log_path=result_path.parent / "ego.log",
        timeout_seconds=7200,
        check=False,
        extra_secrets=(cfg.initial_admin_password, cfg.admin_password, cfg.user_password),
    )
    if result.timed_out or result.exit_code != 0:
        message = f"ego round exited {result.exit_code}; see {result.log_path}"
        if not result_path.exists():
            _write_error_result(cfg, round_id, message)
        else:
            payload = read_json(result_path, default={}) or {}
            payload.setdefault("runner", {})["exit_code"] = result.exit_code
            payload.setdefault("runner", {})["timed_out"] = result.timed_out
            payload.setdefault("runner", {})["log"] = result.log_path
            write_json(result_path, payload)
        # A failed round also leaks its Pages; reclaim before reporting so the
        # retry starts from a clean budget.
        _recycle_pages(cfg, round_id)
        raise RuntimeError(message)
    payload = read_json(result_path, default=None)
    # A round's evidence must come from this run only.  An earlier version
    # silently fell back to a scaffold directory when the round wrote no
    # result, which let stale PASS rows masquerade as this run's evidence.
    if not isinstance(payload, dict):
        _write_error_result(cfg, round_id, "ego round did not write result.json")
        raise RuntimeError(f"ego round produced no result: {result_path}")
    if not payload.get("cases"):
        _write_error_result(cfg, round_id, "ego round wrote a result with no cases")
        raise RuntimeError(f"ego round produced an empty result: {result_path}")
    summary = _round_status(payload)
    payload["summary"] = summary
    write_json(result_path, payload)
    pages = _recycle_pages(cfg, round_id)
    return {
        "round": round_id,
        "result": str(result_path),
        "summary": summary,
        "log": result.log_path,
        "page_recycle": pages,
    }


def run_all(cfg: MatrixConfig) -> dict[str, Any]:
    cfg.ensure_layout()
    completed: list[dict[str, Any]] = []
    for round_id in ROUND_ORDER:
        completed.append(run_round(cfg, round_id))
    failed = [item for item in completed if any(item["summary"].get(key, 0) for key in ("fail", "error", "blocked"))]
    if failed:
        raise RuntimeError("one or more ego rounds did not pass; final round remains unexecuted")
    final = run_round(cfg, FINAL_ROUND)
    payload = {"generated_at": utc_now(), "taskSpace": cfg.task_space, "rounds": completed + [final]}
    write_json(cfg.evidence_root / "ego" / "runner-summary.json", payload)
    append_jsonl(cfg.evidence_root / "lock-log.jsonl", {"event": "ego-all", "at": utc_now(), "rounds": len(completed) + 1})
    return payload


__all__ = ["ROUND_ORDER", "FINAL_ROUND", "run_all", "run_round", "sync_round_templates"]