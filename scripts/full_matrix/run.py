"""Command-line entry point for the full-matrix orchestration."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.full_matrix.config import MatrixConfig
    from scripts.full_matrix import gates, report, sandbox, server
    from scripts.full_matrix.ego_rounds import run_all as run_ego_all
    from scripts.full_matrix.ego_rounds import run_round as run_ego_round
    from scripts.full_matrix.ego_rounds import sync_round_templates
else:
    from .config import MatrixConfig
    from . import gates, report, sandbox, server
    from .ego_rounds import run_all as run_ego_all
    from .ego_rounds import run_round as run_ego_round
    from .ego_rounds import sync_round_templates


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _all(cfg: MatrixConfig) -> int:
    """Run the complete G0 -> G1 -> G4 -> ego -> G8 -> restore/report pipeline."""
    real_restore_needed = False
    exit_code = 0
    failure: str | None = None
    try:
        _print({"phase": "G0", "result": gates.run_g0(cfg)})
        real_restore_needed = True
        _print({"phase": "G1", "result": gates.run_g1(cfg)})
        _print({"phase": "G4", "result": gates.run_g4(cfg)})
        _print({"phase": "EGO", "result": run_ego_all(cfg)})
        _print({"phase": "G8", "result": gates.run_g8(cfg)})
    except Exception as exc:  # noqa: BLE001 - coordinator must report after any gate failure
        exit_code = 1
        failure = f"{type(exc).__name__}: {exc}"
        print(f"[full-matrix] failure: {failure}", file=sys.stderr)
    finally:
        if real_restore_needed:
            try:
                _print({"phase": "STOP-SANDBOX", "result": server.stop_sandbox(cfg)})
            except Exception as exc:  # noqa: BLE001
                exit_code = 1
                print(f"[full-matrix] sandbox stop failure: {exc}", file=sys.stderr)
            try:
                _print({"phase": "RESTORE-REAL", "result": server.restore_real_server(cfg)})
            except Exception as exc:  # noqa: BLE001 - preserve original failure and report restore failure
                exit_code = 1
                failure = (failure or "") + f" | restore real server failed: {type(exc).__name__}: {exc}"
                print(f"[full-matrix] restore failure: {exc}", file=sys.stderr)
        try:
            report_result = report.build_report(cfg)
            _print({"phase": "REPORT", "summary": report_result.get("summary"), "releaseDecision": report_result.get("releaseDecision")})
            if report_result.get("releaseDecision") != "PASS":
                exit_code = 1
        except Exception as exc:  # noqa: BLE001 - report failure must also be visible
            exit_code = 1
            print(f"[full-matrix] report failure: {exc}", file=sys.stderr)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Full-matrix isolated test coordinator")
    parser.add_argument(
        "command",
        choices=("prepare", "g0", "g1", "g4", "ego", "g8", "report", "all"),
        help="operation to run",
    )
    parser.add_argument("round", nargs="?", help="ego round id, e.g. round-01-roles")
    parser.add_argument("--reset", action="store_true", help="reset the sandbox before prepare")
    args = parser.parse_args(argv)
    cfg = MatrixConfig.from_env()
    if args.command == "prepare":
        _print(sandbox.prepare(cfg, reset=args.reset))
        sync_round_templates(cfg)
        return 0
    if args.command == "g0":
        _print(gates.run_g0(cfg))
        return 0
    if args.command == "g1":
        _print(gates.run_g1(cfg))
        return 0
    if args.command == "g4":
        _print(gates.run_g4(cfg))
        return 0
    if args.command == "g8":
        _print(gates.run_g8(cfg))
        return 0
    if args.command == "report":
        return report.main(cfg)
    if args.command == "ego":
        if not args.round:
            parser.error("ego requires a round id")
        _print(run_ego_round(cfg, args.round))
        return 0
    return _all(cfg)


if __name__ == "__main__":
    raise SystemExit(main())