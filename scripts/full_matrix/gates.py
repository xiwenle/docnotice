"""Gate runners for G0, G1, G4, and G8.

These functions are intentionally callable by the future coordinator, but this
module never starts a service by itself during static preparation.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from .config import MatrixConfig
from .io_utils import append_jsonl, run_command, utc_now, write_json
from . import sandbox, server

GATE_LOG = "gate_results.jsonl"


def _record(cfg: MatrixConfig, gate: str, status: str, commands: list[dict[str, Any]], notes: str = "") -> dict[str, Any]:
    row = {
        "gate": gate,
        "status": status,
        "recorded_at": utc_now(),
        "fms_tmp_dir": str(cfg.fms_tmp_dir),
        "commands": commands,
        "notes": notes,
    }
    append_jsonl(cfg.evidence_root / GATE_LOG, row)
    write_json(cfg.evidence_root / f"{gate}-result.json", row)
    return row


def _command_row(result: Any) -> dict[str, Any]:
    return {
        "command": result.command,
        "cwd": result.cwd,
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "log": result.log_path,
        "ok": result.ok,
        "timed_out": result.timed_out,
    }


def _run_checked_sequence(cfg: MatrixConfig, gate: str, specs: list[tuple[str, list[str], int]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label, command, timeout in specs:
        result = run_command(
            command,
            cwd=cfg.source_root,
            env=cfg.child_env(),
            log_path=cfg.evidence_root / gate / f"{label}.log",
            timeout_seconds=timeout,
            check=False,
            extra_secrets=(cfg.initial_admin_password, cfg.admin_password, cfg.user_password),
        )
        row = _command_row(result)
        row["label"] = label
        rows.append(row)
        if not result.ok:
            _record(cfg, gate, "FAIL", rows, f"first failing command: {label}")
            raise RuntimeError(f"{gate} failed at {label}; see {result.log_path}")
    return rows


def _collect_e2e_cases(root: Path) -> dict[str, Any]:
    e2e_dir = root / "scripts" / "e2e"
    files: dict[str, int] = {}
    cases: list[dict[str, str]] = []
    pattern = re.compile(r'@case\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']')
    for path in sorted(e2e_dir.glob("m*.py")):
        text = path.read_text(encoding="utf-8")
        matches = pattern.findall(text)
        files[path.name] = len(matches)
        for code, title in matches:
            cases.append({"module": path.stem, "code": code, "title": title})
    return {"modules": len(files), "case_count": len(cases), "files": files, "cases": cases}


def _route_inventory(cfg: MatrixConfig) -> dict[str, Any]:
    """Import app.main and read FastAPI/OpenAPI routes in an isolated child."""
    route_data = cfg.sandbox_root / "runtime" / "route-inventory-data"
    route_data.mkdir(parents=True, exist_ok=True)
    code_root = cfg.code_dir if (cfg.code_dir / "app" / "main.py").is_file() else cfg.source_root
    output = cfg.evidence_root / "route-inventory.json"
    snippet = '''\nimport json\nfrom app.main import app\nopenapi = app.openapi()\nroutes = []\nfor route in app.routes:\n    methods = sorted(getattr(route, "methods", set()) or set())\n    path = getattr(route, "path", "")\n    if not path:\n        continue\n    routes.append({\n        "path": path,\n        "methods": methods,\n        "name": getattr(route, "name", ""),\n        "include_in_schema": bool(getattr(route, "include_in_schema", True)),\n    })\napi = [r for r in routes if r["path"].startswith("/api/")]\npages = [r for r in routes if not r["path"].startswith("/api/") and "GET" in r["methods"]]\nresult = {"openapi_paths": sorted(openapi.get("paths", {}).keys()), "routes": routes, "api_routes": api, "page_routes": pages}\nprint(json.dumps(result, ensure_ascii=False))\n'''
    env = cfg.child_env({"DATA_DIR": str(route_data), "DATABASE_URL": ""})
    result = run_command(
        [str(cfg.source_python), "-c", snippet],
        cwd=code_root,
        env=env,
        log_path=cfg.evidence_root / "g0" / "route-inventory.log",
        timeout_seconds=120,
        check=False,
        extra_secrets=(cfg.initial_admin_password, cfg.admin_password, cfg.user_password),
    )
    if not result.ok:
        raise RuntimeError(f"route inventory failed; see {result.log_path}")
    text = (cfg.evidence_root / "g0" / "route-inventory.log").read_text(encoding="utf-8")
    # The route script emits one JSON line after any import warnings.
    line = next((line for line in reversed(text.splitlines()) if line.strip().startswith("{")), "")
    if not line:
        raise RuntimeError("route inventory produced no JSON payload")
    payload = json.loads(line)
    payload["generated_at"] = utc_now()
    payload["code_root"] = str(code_root)
    write_json(output, payload)
    return payload


def run_g0(cfg: MatrixConfig) -> dict[str, Any]:
    cfg.ensure_layout()
    inventory = _collect_e2e_cases(cfg.source_root)
    write_json(cfg.evidence_root / "e2e" / "test-inventory-before.json", inventory)
    specs: list[tuple[str, list[str], int]] = [
        ("git-status", ["git", "status", "--short"], 60),
        ("git-diff-check", ["git", "diff", "--check"], 60),
        ("ruff", [str(cfg.source_python), "-m", "ruff", "check", "."], 300),
        ("mypy", [str(cfg.source_python), "-m", "mypy", "app"], 600),
        ("frontend-debt", [str(cfg.source_python), "scripts/frontend_debt_check.py"], 300),
        ("pytest-collect", [str(cfg.source_python), "-m", "pytest", "--collect-only", "-q"], 600),
    ]
    node = shutil.which("node")
    if node:
        for js in sorted((cfg.source_root / "app" / "web" / "static" / "js").rglob("*.js")):
            label = "node-check-" + re.sub(r"[^A-Za-z0-9_.-]+", "_", str(js.relative_to(cfg.source_root)))
            specs.append((label, [node, "--check", str(js)], 120))
        for mjs in sorted((cfg.evidence_root / "ego").rglob("*.mjs")):
            label = "node-check-ego-" + re.sub(r"[^A-Za-z0-9_.-]+", "_", mjs.name)
            specs.append((label, [node, "--check", str(mjs)], 120))
    else:
        _record(cfg, "g0", "FAIL", [], "node executable not found; cannot run JS syntax checks")
        raise RuntimeError("node executable not found")
    rows = _run_checked_sequence(cfg, "g0", specs)
    routes = _route_inventory(cfg)
    payload = {
        "fms_tmp_dir": str(cfg.fms_tmp_dir),
        "e2e_inventory": inventory,
        "route_counts": {
            "all": len(routes["routes"]),
            "api": len(routes["api_routes"]),
            "pages": len(routes["page_routes"]),
        },
    }
    _record(cfg, "g0", "PASS", rows, json.dumps(payload, ensure_ascii=False))
    return payload


def run_g1(cfg: MatrixConfig) -> dict[str, Any]:
    cfg.ensure_layout()
    real = server.stop_real_server(cfg)
    # G8 compares this snapshot with the state immediately before the real
    # server is restored. Take "before" while :8000 is stopped, so normal
    # SQLite WAL/SHM and app.log writes from the restored real service are not
    # mistaken for writes by the isolated matrix.
    sandbox.write_real_snapshots(cfg, phase="before")
    sandbox.prepare(cfg, reset=True, preserve_before=True)
    sandbox.validate_no_env_or_real_data(cfg)
    verify = sandbox.verify_config(cfg)
    started = server.start_sandbox(cfg)
    health = server.wait_health(cfg)
    hosts = server.check_hosts(cfg)
    if not hosts.get("ok"):
        _record(cfg, "g1", "FAIL", [], "host-only cookie or host health preflight failed")
        raise RuntimeError(f"G1 host preflight failed: see {cfg.evidence_root / 'g1-hosts.json'}")
    payload = {
        "real_server": real,
        "sandbox_server": started,
        "health": health,
        "hosts": hosts,
        "config": verify,
    }
    _record(cfg, "g1", "PASS", [], json.dumps(payload, ensure_ascii=False))
    write_json(cfg.evidence_root / "g1.json", payload)
    return payload


def _patch_legacy_guard(cfg: MatrixConfig, relative: str) -> Path:
    # Read from the sandbox copy, not the live tree: G4's whole point is to
    # prove the copied code is sound.  Reading from source_root would let a
    # stale sandbox pass on source files it no longer contains.
    source = cfg.code_dir / relative
    text = source.read_text(encoding="utf-8")
    text = text.replace('BASE_URL = "http://127.0.0.1:8000"', f'BASE_URL = "{cfg.base_url}"')
    text = text.replace('BASE_URL = "http://127.0.0.1:8010"', f'BASE_URL = "{cfg.base_url}"')
    text = text.replace('BASE = "http://127.0.0.1:8000"', f'BASE = "{cfg.base_url}"')
    text = text.replace('BASE = "http://127.0.0.1:8010"', f'BASE = "{cfg.base_url}"')
    text = text.replace('"username": "admin_demo", "password": "Demo@123456"',
                        f'"username": "{cfg.admin_username}", "password": "{cfg.admin_password}"')
    text = text.replace('"username": "user_rnd_1", "password": "Demo@123456"',
                        f'"username": "{cfg.user_username}", "password": "{cfg.user_password}"')
    text = text.replace('ADMIN = ("admin_demo", "Demo@123456")',
                        f'ADMIN = ("{cfg.admin_username}", "{cfg.admin_password}")')
    target = cfg.code_dir / "scripts" / ("_fm_" + Path(relative).name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    return target


def _run_checked_sequence_with_cwd(cfg: MatrixConfig, gate: str, specs: list[tuple[str, list[str], int]], *, cwd: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label, command, timeout in specs:
        # Unit tests must not inherit browser-server backup/session env overrides:
        # those values intentionally differ from pytest settings and would make
        # configuration assertions fail for environmental, not product, reasons.
        command_env = dict(os.environ) if label == "pytest" else cfg.child_env()
        result = run_command(
            command,
            cwd=cwd,
            env=command_env,
            log_path=cfg.evidence_root / gate / f"{label}.log",
            timeout_seconds=timeout,
            check=False,
            extra_secrets=(cfg.initial_admin_password, cfg.admin_password, cfg.user_password),
        )
        row = _command_row(result)
        row["label"] = label
        rows.append(row)
        if not result.ok:
            _record(cfg, gate, "FAIL", rows, f"first failing command: {label}")
            raise RuntimeError(f"{gate} failed at {label}; see {result.log_path}")
    return rows


def run_g4(cfg: MatrixConfig) -> dict[str, Any]:
    if not server.port_in_use(cfg.port):
        raise RuntimeError("G4 requires the sandbox server on the configured port")
    specs: list[tuple[str, list[str], int]] = [
        ("pytest", [str(cfg.source_python), "-m", "pytest"], 3600),
        ("playwright-e2e", [str(cfg.source_python), "scripts/e2e_full_check.py"], 7200),
        ("admin-journey", [str(cfg.source_python), "scripts/admin_journey.py"], 1200),
        ("user-journey", [str(cfg.source_python), "scripts/user_journey.py"], 1200),
        ("theme-check", [str(cfg.source_python), "scripts/theme_check.py"], 1200),
        ("a11y-dom", [str(cfg.source_python), "scripts/a11y_dom_check.py"], 1200),
        ("modal-a11y", [str(cfg.source_python), "scripts/modal_a11y_check.py"], 1200),
    ]
    # These historical guards hard-code old ports; run patched copies from the
    # copied sandbox so source files remain untouched.
    for relative in (
        "scripts/frontend_p1_check.py",
        "scripts/palette_audit.py",
        "scripts/extreme_samples_check.py",
    ):
        patched = _patch_legacy_guard(cfg, relative)
        specs.append((patched.stem, [str(cfg.source_python), str(patched)], 2400))
    rows = _run_checked_sequence_with_cwd(cfg, "g4", specs, cwd=cfg.code_dir)
    _record(cfg, "g4", "PASS", rows, "pytest + 31-module Playwright E2E + browser guards executed against the sandbox copy")
    return {"commands": rows}


def run_g8(cfg: MatrixConfig) -> dict[str, Any]:
    result = sandbox.snapshot_after(cfg)
    status = "PASS" if not result.get("changed") else "FAIL"
    _record(cfg, "g8", status, [], json.dumps(result, ensure_ascii=False))
    if result.get("changed"):
        raise RuntimeError(f"real data/.env changed; see {cfg.evidence_root / 'data-diff.json'}")
    return result


__all__ = ["run_g0", "run_g1", "run_g4", "run_g8"]