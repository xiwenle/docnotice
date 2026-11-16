"""Aggregate gate and ego evidence into the final machine-readable report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import MatrixConfig
from .io_utils import read_json, read_jsonl, redact_text, summarize_cases, utc_now, write_json


def _safe(value: Any, cfg: MatrixConfig) -> Any:
    if isinstance(value, str):
        return redact_text(value, extra_secrets=(cfg.initial_admin_password, cfg.admin_password, cfg.user_password))
    if isinstance(value, list):
        return [_safe(item, cfg) for item in value]
    if isinstance(value, dict):
        return {key: _safe(item, cfg) for key, item in value.items()}
    return value


def _normalise_case(raw: dict[str, Any], *, default_phase: str, default_owner: str) -> dict[str, Any]:
    return {
        "id": raw.get("id") or raw.get("caseId") or "UNKNOWN",
        "phase": raw.get("phase") or default_phase,
        "owner": raw.get("owner") or default_owner,
        "role": raw.get("role") or "system",
        "mutability": raw.get("mutability") or "READ_SHARED",
        "precondition": raw.get("precondition") or "",
        "depends_on": raw.get("depends_on") or [],
        "steps": raw.get("steps") or [],
        "expected": raw.get("expected") or [],
        "actual": raw.get("actual") or {},
        "status": raw.get("status") or "BLOCKED",
        "severity": raw.get("severity") or "NONE",
        "evidence": raw.get("evidence") or [],
        "repro": raw.get("repro") or {},
        "attempt": raw.get("attempt") or 1,
        "defect_id": raw.get("defect_id"),
        "fix_commit": raw.get("fix_commit"),
        "duration_ms": raw.get("duration_ms") or 0,
    }


def _missing_evidence(cfg: MatrixConfig, gate: dict[str, Any]) -> list[str]:
    """Return the log paths a gate claims but that are not on disk.

    A report that points at a file nobody can open is worse than no report:
    it reads as proof while being unverifiable.  Gates record their log paths
    as they run, so every recorded path must exist afterwards, or the gate is
    reported as ERROR instead of PASS.
    """
    missing: list[str] = []
    for command in gate.get("commands", []) or []:
        log_path = command.get("log")
        if not log_path:
            missing.append(f"{gate.get('gate')}/{command.get('label', '?')}: no log recorded")
            continue
        if not Path(log_path).exists():
            missing.append(f"{gate.get('gate')}/{command.get('label', '?')}: log missing at {log_path}")
    return missing


def collect_rounds(cfg: MatrixConfig) -> list[dict[str, Any]]:
    rounds: list[dict[str, Any]] = []
    results_root = cfg.evidence_root / "ego" / "results"
    if not results_root.exists():
        return rounds
    for path in sorted(results_root.rglob("result.json")):
        payload = read_json(path, default={}) or {}
        payload["result_path"] = str(path)
        rounds.append(_safe(payload, cfg))
    return rounds


def collect_gates(cfg: MatrixConfig) -> list[dict[str, Any]]:
    """Return the latest row per gate.

    gate_results.jsonl is append-only. Earlier failed attempts remain as history,
    but the release decision must use the final rerun result for each gate.
    """
    latest: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(cfg.evidence_root / "gate_results.jsonl"):
        key = str(row.get("gate") or "unknown")
        latest[key] = row
    ordered = [latest[key] for key in sorted(latest)]
    return [_safe(row, cfg) for row in ordered]


def build_report(cfg: MatrixConfig) -> dict[str, Any]:
    cfg.ensure_layout()
    manifest = read_json(cfg.evidence_root / "run-manifest.json", default={}) or {}
    gates = collect_gates(cfg)
    rounds = collect_rounds(cfg)
    evidence_gaps: list[str] = []
    cases: list[dict[str, Any]] = []
    for gate in gates:
        status = gate.get("status", "ERROR")
        missing = _missing_evidence(cfg, gate)
        if missing:
            evidence_gaps.extend(missing)
            status = "ERROR"
        cases.append(_normalise_case({
            "id": f"GATE-{gate.get('gate', 'UNKNOWN')}",
            "phase": gate.get("gate", "G0"),
            "owner": "H",
            "role": "system",
            "mutability": "ENV_EXCLUSIVE" if gate.get("gate") == "G1" else "READ_SHARED",
            "precondition": "command gate",
            "expected": ["command exits 0", "every recorded log exists on disk"],
            "actual": {
                "commands": gate.get("commands", []),
                "notes": gate.get("notes", ""),
                "missingEvidence": missing,
            },
            "status": "PASS" if status == "PASS" else "ERROR",
            "severity": "P0" if status != "PASS" else "NONE",
            "evidence": [{"kind": "json", "path": str(cfg.evidence_root / f"{gate.get('gate')}-result.json")}],
        }, default_phase="G0", default_owner="H"))
    for round_data in rounds:
        round_id = round_data.get("roundId") or round_data.get("round") or "unknown-round"
        for item in round_data.get("cases", []):
            cases.append(_normalise_case(item, default_phase=round_id, default_owner="F"))
    summary = summarize_cases(cases)
    # SKIP is deliberately counted as an open defect: a skipped case means the
    # behaviour was never verified in this run, so it must not contribute to a
    # PASS release decision.
    open_statuses = {"FAIL", "ERROR", "BLOCKED", "SKIP"}
    open_defects = [item for item in cases if item["status"] in open_statuses]
    # A gate that never ran is not a gate that passed.  The report only sees the
    # gates recorded under this run's evidence root, so a run split across two
    # RUN_IDs would otherwise report PASS while G0/G4 were never executed here.
    required_gates = {"g0", "g1", "g4", "g8"}
    recorded_gates = {str(gate.get("gate")) for gate in gates}
    missing_gates = sorted(required_gates - recorded_gates)
    if missing_gates:
        open_defects.append({
            "id": "GATE-MISSING",
            "phase": "gate",
            "owner": "H",
            "role": "system",
            "mutability": "READ_SHARED",
            "precondition": "every required gate recorded under this run id",
            "expected": sorted(required_gates),
            "actual": {"missing": missing_gates},
            "status": "ERROR",
            "severity": "P0",
            "evidence": [],
            "steps": [],
            "depends_on": [],
            "repro": {},
            "attempt": 1,
            "defect_id": None,
            "fix_commit": None,
            "duration_ms": 0,
        })
    report = _safe({
        "schemaVersion": "full-matrix-report/v1",
        "runId": cfg.run_id,
        "generatedAt": utc_now(),
        "manifest": manifest,
        "gates": gates,
        "rounds": rounds,
        "cases": cases,
        "summary": summary,
        "openDefects": len(open_defects),
        "skippedCount": summary.get("skipped", 0),
        "evidenceGaps": evidence_gaps,
        "missingGates": missing_gates,
        "releaseDecision": "PASS" if not open_defects and gates and all(g.get("status") == "PASS" for g in gates) else "BLOCKED",
        "notes": [
            "A release decision requires G0/G1/G4/G8 all PASS and no FAIL/ERROR/BLOCKED/SKIP ego cases.",
            "SKIP counts as unverified, not as a pass.",
            "PASS here is evidence-based only; missing round results remain visible as gaps.",
            "A gate whose recorded log is missing on disk is reported as ERROR, not PASS.",
            "A required gate that never ran under this run id is an ERROR, not a pass.",
        ],
    }, cfg)
    write_json(cfg.evidence_root / "report.json", report)
    with (cfg.evidence_root / "cases.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
        for item in cases:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    _write_defects(cfg, cases)
    _write_locks(cfg)
    _update_manifest(cfg, manifest, summary, report["releaseDecision"])
    return report


def _write_defects(cfg: MatrixConfig, cases: list[dict[str, Any]]) -> None:
    lines = [
        "# Full matrix defects",
        "",
        f"Generated: {utc_now()}",
        "",
    ]
    open_cases = [item for item in cases if item["status"] in {"FAIL", "ERROR", "BLOCKED", "SKIP"}]
    if not open_cases:
        lines.append("No FAIL, ERROR, BLOCKED, or SKIP cases recorded.")
    for item in open_cases:
        lines.extend([
            f"## {item['id']} — {item['status']} / {item['severity']}",
            "",
            f"- Phase: `{item['phase']}`",
            f"- Owner: `{item['owner']}`",
            f"- Role: `{item['role']}`",
            f"- Precondition: {item['precondition'] or '(none)'}",
            f"- Repro: `{json.dumps(item['repro'], ensure_ascii=False)}`",
            f"- Evidence: `{json.dumps(item['evidence'], ensure_ascii=False)}`",
            "",
        ])
    (cfg.evidence_root / "defects.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _write_locks(cfg: MatrixConfig) -> None:
    path = cfg.evidence_root / "lock-log.json"
    if not path.exists():
        write_json(path, {"schemaVersion": "full-matrix-lock-log/v1", "locks": [], "policy": "all writes global serial"})


def _update_manifest(cfg: MatrixConfig, manifest: dict[str, Any], summary: dict[str, int], decision: str) -> None:
    updated = dict(manifest)
    updated.update({
        "run_id": cfg.run_id,
        "report_generated_at": utc_now(),
        "summary": summary,
        "release_decision": decision,
        "evidence_root": str(cfg.evidence_root),
    })
    write_json(cfg.evidence_root / "run-manifest.json", _safe(updated, cfg))


def main(cfg: MatrixConfig | None = None) -> int:
    config = cfg or MatrixConfig.from_env()
    report = build_report(config)
    print(json.dumps({"summary": report["summary"], "releaseDecision": report["releaseDecision"]}, ensure_ascii=False))
    return 0 if report["releaseDecision"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())