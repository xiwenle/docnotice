"""验收框架自身的可信度回归（项目规范 §30；不覆盖产品功能）。

这里守护的是 `scripts.full_matrix` 的**证据完整性**：框架可以报 BLOCKED，但不能
把「没跑」「跑失败」「被跳过」伪装成 PASS。2026-11-16 实测发现三处会造假的路径：

1. `ego_rounds.run_round` 在本轮结果缺失时，会按文件 mtime 回退到旧的 scaffold 目录，
   把旧 PASS 当成本轮证据；
2. `report.build_report` 的 openDefects 不含 SKIP，被跳过的功能仍可判 PASS；
3. `server.stop_real_server` 不校验进程身份，会强杀端口 8000 上的任意程序。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.full_matrix import ego_rounds, report, server
from scripts.full_matrix.config import MatrixConfig


def _cfg(tmp_path: Path) -> MatrixConfig:
    return MatrixConfig(
        run_id="unittest-run",
        source_root=tmp_path / "src",
        sandbox_root=tmp_path / "sandbox",
        evidence_root=tmp_path / "evidence",
        task_space="unittest-space",
        port=8121,
    )


# ---------------------------------------------------------------------------
# 1. 旧结果不得冒充本轮结果
# ---------------------------------------------------------------------------
class TestNoStaleEvidenceFallback:
    def test_missing_result_is_error_not_stale_pass(self, tmp_path, monkeypatch):
        """本轮没写 result.json 时必须 ERROR，哪怕旧 scaffold 里有 PASS。"""
        cfg = _cfg(tmp_path)
        stale = cfg.source_root / "_agent_full_matrix_20261111_impl01" / "ego" / "results" / "round-00-smoke"
        stale.mkdir(parents=True)
        (stale / "result.json").write_text(
            json.dumps({
                "roundId": "round-00-smoke",
                "cases": [{"id": "EGO-SMOKE-ADMIN", "status": "PASS"}],
            }),
            encoding="utf-8",
        )

        monkeypatch.setattr(ego_rounds, "sync_round_templates", lambda _cfg: None)
        monkeypatch.setattr(ego_rounds, "_ego_exe", lambda: "ego-browser")

        round_path = cfg.evidence_root / "ego" / "rounds" / "round-00-smoke.mjs"
        round_path.parent.mkdir(parents=True, exist_ok=True)
        round_path.write_text("// noop\n", encoding="utf-8")

        # The round writes nothing at all.
        monkeypatch.setattr(
            ego_rounds, "run_command",
            lambda *a, **k: type("R", (), {"exit_code": 0, "timed_out": False, "log_path": cfg.evidence_root / "x.log"})(),
        )

        with pytest.raises(RuntimeError, match="no result"):
            ego_rounds.run_round(cfg, "round-00-smoke")

        written = json.loads(
            (cfg.evidence_root / "ego" / "results" / "round-00-smoke" / "result.json").read_text(encoding="utf-8")
        )
        statuses = {case["status"] for case in written["cases"]}
        assert statuses == {"ERROR"}, f"stale PASS leaked into this run: {written}"
        assert written["summary"]["pass"] == 0

    def test_empty_cases_result_is_error(self, tmp_path, monkeypatch):
        """写了 result.json 但 cases 为空，同样算没跑过。"""
        cfg = _cfg(tmp_path)
        monkeypatch.setattr(ego_rounds, "sync_round_templates", lambda _cfg: None)
        monkeypatch.setattr(ego_rounds, "_ego_exe", lambda: "ego-browser")
        round_path = cfg.evidence_root / "ego" / "rounds" / "round-01-roles.mjs"
        round_path.parent.mkdir(parents=True, exist_ok=True)
        round_path.write_text("// noop\n", encoding="utf-8")

        result_path = cfg.evidence_root / "ego" / "results" / "round-01-roles" / "result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)

        def fake_run(*a, **k):
            result_path.write_text(json.dumps({"roundId": "round-01-roles", "cases": []}), encoding="utf-8")
            return type("R", (), {"exit_code": 0, "timed_out": False, "log_path": cfg.evidence_root / "x.log"})()

        monkeypatch.setattr(ego_rounds, "run_command", fake_run)

        with pytest.raises(RuntimeError, match="empty result"):
            ego_rounds.run_round(cfg, "round-01-roles")


# ---------------------------------------------------------------------------
# 2. SKIP 不得判 PASS
# ---------------------------------------------------------------------------
class TestSkipIsNotPass:
    def _write_gates(self, cfg: MatrixConfig) -> None:
        cfg.ensure_layout()
        rows = [
            {"gate": gate, "status": "PASS", "commands": [], "notes": ""}
            for gate in ("g0", "g1", "g4", "g8")
        ]
        (cfg.evidence_root / "gate_results.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
        )

    def test_skip_makes_release_blocked(self, tmp_path):
        cfg = _cfg(tmp_path)
        self._write_gates(cfg)
        round_dir = cfg.evidence_root / "ego" / "results" / "round-03-notices"
        round_dir.mkdir(parents=True)
        (round_dir / "result.json").write_text(
            json.dumps({
                "roundId": "round-03-notices",
                "cases": [
                    {"id": "EGO-NOTICE-CREATE-ALL", "status": "PASS", "severity": "NONE"},
                    {"id": "EGO-NOTICE-THREE-AUDIENCES", "status": "SKIP", "severity": "P1"},
                ],
            }),
            encoding="utf-8",
        )

        built = report.build_report(cfg)

        assert built["summary"]["skipped"] == 1
        assert built["openDefects"] == 1, "SKIP must count as an open defect"
        assert built["skippedCount"] == 1
        assert built["releaseDecision"] == "BLOCKED"

    def test_all_pass_is_release_pass(self, tmp_path):
        cfg = _cfg(tmp_path)
        self._write_gates(cfg)
        round_dir = cfg.evidence_root / "ego" / "results" / "round-00-smoke"
        round_dir.mkdir(parents=True)
        (round_dir / "result.json").write_text(
            json.dumps({
                "roundId": "round-00-smoke",
                "cases": [{"id": "EGO-SMOKE-ADMIN", "status": "PASS", "severity": "NONE"}],
            }),
            encoding="utf-8",
        )

        assert report.build_report(cfg)["releaseDecision"] == "PASS"


# ---------------------------------------------------------------------------
# 3. 只停本项目自己的 8000，不误杀无关进程
# ---------------------------------------------------------------------------
class TestStopRealServerIdentity:
    def test_foreign_listener_is_refused(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path)
        cfg.ensure_layout()
        foreign = {
            "pid": 4242,
            "name": "python.exe",
            "command_line": '"C:/other/app/python.exe" -m http.server 8000',
        }
        monkeypatch.setattr(server, "save_real_server", lambda _cfg: {
            "port": 8000, "was_listening": True, "process": foreign,
        })

        def exploding_powershell(*a, **k):
            raise AssertionError("refused path must not attempt Stop-Process")

        monkeypatch.setattr(server, "_powershell", exploding_powershell)

        with pytest.raises(RuntimeError, match="refusing to stop"):
            server.stop_real_server(cfg)

    def test_project_uvicorn_is_accepted(self, tmp_path):
        cfg = _cfg(tmp_path)
        project_cmd = (
            f'"{cfg.source_root}/.venv/Scripts/python.exe" -m uvicorn app.main:app '
            "--host 0.0.0.0 --port 8000"
        )
        assert server._looks_like_project_uvicorn(cfg, {"command_line": project_cmd}) is True

    def test_uvicorn_from_another_project_is_refused(self, tmp_path):
        cfg = _cfg(tmp_path)
        other_cmd = '"/elsewhere/python.exe" -m uvicorn app.main:app --port 8000'
        assert server._looks_like_project_uvicorn(cfg, {"command_line": other_cmd}) is False


# ---------------------------------------------------------------------------
# 4. 报告指向的证据必须真的在磁盘上
# ---------------------------------------------------------------------------
class TestEvidenceMustExist:
    """A gate that records a log path it did not write must not read as PASS.

    The 2026-11-16 run's report pointed at `g4/pytest.log` and friends.  They
    did exist, but nothing in the framework checked — a typo or a moved file
    would have produced an unopenable "proof" that still counted as PASS.
    """

    def _write_gates(self, cfg: MatrixConfig, *, log_path: Path | None) -> None:
        cfg.ensure_layout()
        rows = [
            {
                "gate": gate,
                "status": "PASS",
                "commands": ([{"label": "pytest", "log": str(log_path)}] if log_path else []),
                "notes": "",
            }
            for gate in ("g0", "g1", "g4", "g8")
        ]
        (cfg.evidence_root / "gate_results.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
        )

    def test_missing_log_downgrades_gate_to_error(self, tmp_path):
        cfg = _cfg(tmp_path)
        self._write_gates(cfg, log_path=cfg.evidence_root / "g4" / "pytest.log")

        built = report.build_report(cfg)

        assert built["releaseDecision"] == "BLOCKED"
        assert built["openDefects"] == 4, "every gate points at a missing log"
        assert len(built["evidenceGaps"]) == 4
        assert any("pytest.log" in gap for gap in built["evidenceGaps"])

    def test_existing_log_keeps_gate_pass(self, tmp_path):
        cfg = _cfg(tmp_path)
        log_path = cfg.evidence_root / "g4" / "pytest.log"
        cfg.ensure_layout()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("773 passed\n", encoding="utf-8")
        self._write_gates(cfg, log_path=log_path)

        built = report.build_report(cfg)

        assert built["evidenceGaps"] == []
        assert built["releaseDecision"] == "PASS"

    def test_gate_with_no_commands_is_not_penalised(self, tmp_path):
        """G1/G8 record notes rather than commands; that is not a gap."""
        cfg = _cfg(tmp_path)
        self._write_gates(cfg, log_path=None)
        assert report.build_report(cfg)["releaseDecision"] == "PASS"


# ---------------------------------------------------------------------------
# 5. G4 必须验证沙箱副本，而不是实时源码树
# ---------------------------------------------------------------------------class TestG4RunsSandboxCopy:
    def test_g4_working_directory_is_sandbox_code(self, tmp_path, monkeypatch):
        """Otherwise a sandbox that diverged from source passes on source code."""
        cfg = _cfg(tmp_path)
        captured: dict[str, object] = {}

        monkeypatch.setattr(server, "port_in_use", lambda _port: True)

        def fake_sequence(_cfg, gate, specs, *, cwd):
            captured["cwd"] = cwd
            captured["gate"] = gate
            return []

        monkeypatch.setattr("scripts.full_matrix.gates._run_checked_sequence_with_cwd", fake_sequence)
        monkeypatch.setattr("scripts.full_matrix.gates._patch_legacy_guard", lambda c, r: c.code_dir / r)
        monkeypatch.setattr("scripts.full_matrix.gates._record", lambda *a, **k: None)

        from scripts.full_matrix import gates

        gates.run_g4(cfg)

        assert captured["cwd"] == cfg.code_dir
        assert captured["cwd"] != cfg.source_root


# ---------------------------------------------------------------------------
# 6. Page 回收必须真的拿到 round id
# ---------------------------------------------------------------------------
class TestRecyclePagesGetsRoundId:
    """`ego-browser nodejs` does not forward env vars.

    The first version passed the round id through cfg.child_env(), which looked
    correct and silently did nothing: every recycle run reported
    "no round id supplied", closed zero Pages, and later rounds died on
    PageBudgetError.  The id must be baked into the script itself.
    """

    def test_round_id_is_substituted_into_the_script(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path)
        lib = cfg.evidence_root / "ego" / "lib"
        lib.mkdir(parents=True)
        (lib / "recycle_pages.mjs").write_text(
            'const ROUND_ID = "__FULL_MATRIX_ROUND_ID__";\nconsole.log(ROUND_ID);\n',
            encoding="utf-8",
        )

        captured: dict[str, object] = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["env"] = kwargs.get("env") or {}
            return type("R", (), {"exit_code": 0, "log_path": tmp_path / "x.log"})()

        monkeypatch.setattr(ego_rounds, "run_command", fake_run)
        monkeypatch.setattr(ego_rounds, "_ego_exe", lambda: "ego-browser")

        ego_rounds._recycle_pages(cfg, "round-07-notice-search")

        joined = " ".join(captured["command"])
        scripted = lib / "recycle-pages-round-07-notice-search.mjs"
        assert scripted.is_file(), (
            "the round id must be baked into a per-round script: ego-browser's "
            "nodejs subcommand does not forward environment variables"
        )
        body = scripted.read_text(encoding="utf-8")
        assert "__FULL_MATRIX_ROUND_ID__" not in body, "placeholder left unsubstituted"
        assert 'const ROUND_ID = "round-07-notice-search";' in body
        assert "recycle-pages-round-07-notice-search.mjs" in joined


# ---------------------------------------------------------------------------
# 7. 没跑过的门禁不能算通过
# ---------------------------------------------------------------------------
class TestMissingGateIsNotPass:
    """A run split across two RUN_IDs must not report PASS.

    The report only sees the gates recorded under its own evidence root.  When
    G0/G4 were recorded under a different run id, `releaseDecision` still came
    out PASS — two gates had simply never run under this run.
    """

    def _write_gates(self, cfg: MatrixConfig, gates: list[str]) -> None:
        cfg.ensure_layout()
        rows = [
            {"gate": gate, "status": "PASS", "commands": [], "notes": ""}
            for gate in gates
        ]
        (cfg.evidence_root / "gate_results.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
        )

    def test_all_four_gates_present_is_pass(self, tmp_path):
        cfg = _cfg(tmp_path)
        self._write_gates(cfg, ["g0", "g1", "g4", "g8"])
        built = report.build_report(cfg)
        assert built["missingGates"] == []
        assert built["releaseDecision"] == "PASS"

    def test_missing_g4_blocks_release(self, tmp_path):
        cfg = _cfg(tmp_path)
        self._write_gates(cfg, ["g1", "g8"])
        built = report.build_report(cfg)
        assert built["missingGates"] == ["g0", "g4"]
        assert built["releaseDecision"] == "BLOCKED"
        assert built["openDefects"] >= 1

    def test_missing_g0_blocks_release(self, tmp_path):
        cfg = _cfg(tmp_path)
        self._write_gates(cfg, ["g1", "g4", "g8"])
        built = report.build_report(cfg)
        assert built["missingGates"] == ["g0"]
        assert built["releaseDecision"] == "BLOCKED"
