"""部署前配置预检脚本（scripts/check_env.py）的行为测试。

覆盖：
- .env 解析：行尾注释剥离、export 前缀、引号值、注释行忽略
- 只读纪律：脚本运行【不得创建目录】（曾因未剥行尾注释产生垃圾目录）
- 各类错误配置的判定：APP_ENV 拼错、占位 SECRET_KEY、短密钥、弱管理员密码、
  非法端口、非法 BACKUP_SCHEDULE
- 警告项：COOKIE_SECURE 与 APP_ENV 不匹配
- 生产阻断项：TRUSTED_HOSTS 仅剩本机默认、通配 *、未配置
- 退出码：0=通过 / 1=存在阻塞项
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "check_env.py"


def _load_module():
    """按文件路径加载 scripts/check_env.py（scripts 非包，不能直接 import）。"""
    spec = importlib.util.spec_from_file_location("check_env_under_test", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check_env = _load_module()


# ---------------------------------------------------------------------------
# .env 解析
# ---------------------------------------------------------------------------
class TestEnvParsing:
    def test_strips_trailing_comment(self, tmp_path):
        """行尾注释必须剥离（本仓库 .env 即 `KEY=value   # 注释` 写法）。"""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "APP_ENV=production   # 仅支持 development/production\n",
            encoding="utf-8",
        )
        parsed = check_env._parse_env_file(env_file)
        assert parsed["APP_ENV"] == "production"

    def test_keeps_hash_inside_value_when_quoted(self, tmp_path):
        """引号内的 # 属于值的一部分，不能被当作注释砍掉。"""
        env_file = tmp_path / ".env"
        env_file.write_text('SECRET_KEY="abc#def"  # 这里是注释\n', encoding="utf-8")
        parsed = check_env._parse_env_file(env_file)
        assert parsed["SECRET_KEY"] == "abc#def"

    def test_ignores_comments_and_blank_lines(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# 注释行\n\nPORT=8000\n", encoding="utf-8")
        parsed = check_env._parse_env_file(env_file)
        assert parsed == {"PORT": "8000"}

    def test_strips_export_prefix(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("export PORT=9000\n", encoding="utf-8")
        assert check_env._parse_env_file(env_file)["PORT"] == "9000"


# ---------------------------------------------------------------------------
# 只读纪律
# ---------------------------------------------------------------------------
class TestReadOnlyDiscipline:
    def test_creates_no_directories(self, tmp_path, monkeypatch):
        """检查脚本绝不能创建目录（曾因未剥行尾注释建出垃圾目录）。"""
        monkeypatch.chdir(tmp_path)
        env_file = tmp_path / ".env"
        env_file.write_text(
            "APP_ENV=development\nDATA_DIR=./nonexistent_data_dir\n",
            encoding="utf-8",
        )
        check_env._results.clear()
        check_env.check_config(
            check_env._parse_env_file(env_file), env_file, strict=False
        )
        assert not (tmp_path / "nonexistent_data_dir").exists()
        assert not (tmp_path / "nonexistent_data_dir").is_dir()

    def test_leaves_no_probe_file(self, tmp_path, monkeypatch):
        """写探针必须清理干净。"""
        monkeypatch.chdir(tmp_path)
        env_file = tmp_path / ".env"
        env_file.write_text("APP_ENV=development\nDATA_DIR=./data\n", encoding="utf-8")
        (tmp_path / "data").mkdir()
        check_env._results.clear()
        check_env.check_config(
            check_env._parse_env_file(env_file), env_file, strict=False
        )
        assert list((tmp_path / "data").glob(".check_env_write_probe")) == []

    def test_does_not_import_scheduler_or_settings(self, tmp_path, monkeypatch):
        """备份调度校验与磁盘检查不得触碰 Settings（会 ensure_dirs 建目录）。

        2026-10-29 回归守卫。此前 check_config 里
        `from app.core.scheduler import parse_schedule`，而 scheduler 模块
        顶层就执行 `settings = get_settings()` → ensure_dirs()，
        于是"只读"的预检脚本在磁盘上建出了 6 个目录。
        """
        import sys

        # 确保这两个模块没有被前面别的测试导入过（否则本测无意义）
        for mod in ("app.core.scheduler",):
            sys.modules.pop(mod, None)

        monkeypatch.chdir(tmp_path)
        env_file = tmp_path / ".env"
        env_file.write_text(
            "APP_ENV=development\nDATA_DIR=./ro_check\n"
            "BACKUP_SCHEDULE=sat 03:00\nDISK_MIN_FREE_MB=1\n",
            encoding="utf-8",
        )
        check_env._results.clear()
        check_env.check_config(
            check_env._parse_env_file(env_file), env_file, strict=False
        )
        assert not (tmp_path / "ro_check").exists(), "只读预检不得创建数据目录"
        assert "app.core.scheduler" not in sys.modules, (
            "check_env 不应导入 scheduler（导入即建目录）"
        )

    def test_schedule_validation_matches_scheduler(self):
        """内联调度校验的判据必须与 scheduler.parse_schedule 一致。"""
        from app.core.scheduler import parse_schedule

        good = ["sat 03:00", "daily 03:00", "mon 00:00", "sun 23:59", "SAT 3:00"]
        bad = ["", "sat", "sat 25:00", "sat 03:60", "funday 03:00", "03:00", "sat 3"]

        for s in good:
            try:
                parse_schedule(s)
                scheduler_ok = True
            except ValueError:
                scheduler_ok = False
            try:
                check_env._validate_schedule(s)
                script_ok = True
            except ValueError:
                script_ok = False
            assert scheduler_ok == script_ok, f"合法判定不一致: {s!r}"

        for s in bad:
            try:
                parse_schedule(s)
                scheduler_ok = True
            except ValueError:
                scheduler_ok = False
            try:
                check_env._validate_schedule(s)
                script_ok = True
            except ValueError:
                script_ok = False
            assert scheduler_ok == script_ok, f"非法判定不一致: {s!r}"


class TestProcessEnvPriority:
    """进程环境变量必须优先于 .env 并参与判定（2026-10-29 修复）。

    此前注释写着"环境变量优先级高于 .env"，但代码根本没读 os.environ，
    于是 systemd `Environment=` 注入 APP_ENV/SECRET_KEY 时脚本给出错误结论。
    """

    def test_process_env_overrides_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        env_file = tmp_path / ".env"
        env_file.write_text(
            "APP_ENV=development\nSECRET_KEY=" + "x" * 64 + "\n", encoding="utf-8"
        )
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("SECRET_KEY", "short")

        base = check_env._parse_env_file(env_file)
        merged, overridden = check_env._merge_process_env(base)

        assert merged["APP_ENV"] == "production"
        assert merged["SECRET_KEY"] == "short"
        assert set(overridden) >= {"APP_ENV", "SECRET_KEY"}

        # 合并后按生产标准判定 → 短密钥必须报错
        check_env._results.clear()
        check_env.check_config(merged, env_file, strict=False)
        levels = {item: level for level, item, _ in check_env._results}
        assert levels["APP_ENV"] == "OK"
        assert levels["SECRET_KEY"] == "ERROR"

    def test_unrelated_env_vars_are_ignored(self, tmp_path, monkeypatch):
        """只合并已知配置键，不把无关环境变量卷进来。"""
        monkeypatch.setenv("SOME_UNRELATED_TOKEN", "zzz")
        merged, overridden = check_env._merge_process_env({})
        assert "SOME_UNRELATED_TOKEN" not in merged
        assert "SOME_UNRELATED_TOKEN" not in overridden


# ---------------------------------------------------------------------------
# 判定逻辑
# ---------------------------------------------------------------------------
def _run(env_text: str, tmp_path: Path, strict: bool = False) -> list[tuple[str, str, str]]:
    """跑一次 check_config 并返回 (level, item, message) 列表。

    强制把 DATA_DIR 指向 tmp_path：否则默认 `./data` 会让写探针落到
    **真实** data/ 目录里（即使探针会删除，也不该在测试期间碰真实数据区）。
    """
    env_file = tmp_path / ".env"
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    if "DATA_DIR" not in env_text:
        env_text = f"DATA_DIR={data_dir.as_posix()}\n" + env_text
    env_file.write_text(env_text, encoding="utf-8")
    check_env._results.clear()
    check_env.check_config(check_env._parse_env_file(env_file), env_file, strict)
    return list(check_env._results)


def _levels(results, item: str) -> list[str]:
    return [level for level, it, _ in results if it == item]


class TestConfigJudgement:
    def test_invalid_app_env_is_error(self, tmp_path):
        results = _run("APP_ENV=prodction\n", tmp_path)
        assert "ERROR" in _levels(results, "APP_ENV")

    def test_valid_app_env_ok(self, tmp_path):
        results = _run("APP_ENV=production\nSECRET_KEY=%s\n" % ("a" * 48), tmp_path)
        assert "OK" in _levels(results, "APP_ENV")

    def test_placeholder_secret_key_is_error_in_production(self, tmp_path):
        results = _run(
            "APP_ENV=production\nSECRET_KEY=change-me-to-a-long-random-string\n", tmp_path
        )
        assert "ERROR" in _levels(results, "SECRET_KEY")

    def test_short_secret_key_is_error_in_production(self, tmp_path):
        results = _run("APP_ENV=production\nSECRET_KEY=tooshort\n", tmp_path)
        assert "ERROR" in _levels(results, "SECRET_KEY")

    def test_good_secret_key_passes(self, tmp_path):
        results = _run("APP_ENV=production\nSECRET_KEY=%s\n" % ("x" * 48), tmp_path)
        assert "OK" in _levels(results, "SECRET_KEY")

    def test_placeholder_admin_password_error_in_production(self, tmp_path):
        results = _run(
            "APP_ENV=production\nSECRET_KEY=%s\nINITIAL_ADMIN_PASSWORD=please-change-me\n"
            % ("x" * 48),
            tmp_path,
        )
        assert "ERROR" in _levels(results, "INITIAL_ADMIN_PASSWORD")

    def test_weak_admin_password_error_in_production(self, tmp_path):
        results = _run(
            "APP_ENV=production\nSECRET_KEY=%s\nINITIAL_ADMIN_PASSWORD=abcdefgh\n"
            % ("x" * 48),
            tmp_path,
        )
        assert "ERROR" in _levels(results, "INITIAL_ADMIN_PASSWORD")

    def test_weak_admin_password_only_warn_in_development(self, tmp_path):
        results = _run("APP_ENV=development\nINITIAL_ADMIN_PASSWORD=abcdefgh\n", tmp_path)
        assert "WARN" in _levels(results, "INITIAL_ADMIN_PASSWORD")
        assert "ERROR" not in _levels(results, "INITIAL_ADMIN_PASSWORD")

    def test_strict_mode_escalates_development_to_production(self, tmp_path):
        results = _run(
            "APP_ENV=development\nSECRET_KEY=change-me-to-a-long-random-string\n",
            tmp_path,
            strict=True,
        )
        assert "ERROR" in _levels(results, "SECRET_KEY")

    def test_invalid_port_is_error(self, tmp_path):
        results = _run("PORT=99999\n", tmp_path)
        assert "ERROR" in _levels(results, "PORT")

    def test_valid_port_ok(self, tmp_path):
        results = _run("PORT=8000\n", tmp_path)
        assert "OK" in _levels(results, "PORT")

    def test_invalid_backup_schedule_is_error(self, tmp_path):
        results = _run("BACKUP_SCHEDULE=weekly 03:00\n", tmp_path)
        assert "ERROR" in _levels(results, "BACKUP_SCHEDULE")

    def test_valid_backup_schedule_ok(self, tmp_path):
        results = _run("BACKUP_SCHEDULE=sat 03:00\n", tmp_path)
        assert "OK" in _levels(results, "BACKUP_SCHEDULE")

    def test_cookie_secure_true_in_development_warns(self, tmp_path):
        """内网 http 部署时 Secure=true 会让浏览器静默拒存 Cookie。"""
        results = _run("APP_ENV=development\nCOOKIE_SECURE=true\n", tmp_path)
        assert "WARN" in _levels(results, "COOKIE_SECURE")

    def test_cookie_secure_false_in_production_warns(self, tmp_path):
        results = _run(
            "APP_ENV=production\nSECRET_KEY=%s\nCOOKIE_SECURE=false\n" % ("x" * 48), tmp_path
        )
        assert "WARN" in _levels(results, "COOKIE_SECURE")

    def test_trusted_hosts_default_is_error_in_production(self, tmp_path):
        results = _run(
            "APP_ENV=production\nSECRET_KEY=%s\nTRUSTED_HOSTS=localhost,*.localhost,127.0.0.1,::1,testserver\n"
            % ("x" * 48),
            tmp_path,
        )
        assert "ERROR" in _levels(results, "TRUSTED_HOSTS")

    def test_trusted_hosts_wildcard_is_error(self, tmp_path):
        results = _run("TRUSTED_HOSTS=*\n", tmp_path)
        assert "ERROR" in _levels(results, "TRUSTED_HOSTS")

    def test_trusted_hosts_explicit_domain_ok(self, tmp_path):
        results = _run(
            "APP_ENV=production\nSECRET_KEY=%s\nTRUSTED_HOSTS=docnotice.example.com,10.20.30.40\n"
            % ("x" * 48),
            tmp_path,
        )
        assert "OK" in _levels(results, "TRUSTED_HOSTS")



# ---------------------------------------------------------------------------
# 端到端：退出码
# ---------------------------------------------------------------------------
def _clear_process_env(monkeypatch):
    """清掉进程里可能覆盖 .env 的配置变量（测试隔离用）。

    check_env 现在会合并 os.environ（进程环境优先，与 pydantic-settings 一致），
    而 pytest/conftest 会设置 APP_ENV=test 等变量 —— 不清掉的话，
    测试里写的 .env 内容根本不会参与判定（这正是新逻辑生效的证明）。
    """
    for key in check_env._KNOWN_KEYS:
        monkeypatch.delenv(key, raising=False)


class TestExitCodes:
    def test_exit_1_when_blocking_errors(self, tmp_path, monkeypatch):
        _clear_process_env(monkeypatch)
        env_file = tmp_path / "bad.env"
        env_file.write_text("APP_ENV=prodction\n", encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["check_env.py", "--env-file", str(env_file)])
        assert check_env.main() == 1

    def test_exit_0_when_no_errors(self, tmp_path, monkeypatch):
        _clear_process_env(monkeypatch)
        env_file = tmp_path / "good.env"
        env_file.write_text(
            "APP_ENV=production\nSECRET_KEY=%s\nINITIAL_ADMIN_PASSWORD=Str0ngPass\n"
            "TRUSTED_HOSTS=docnotice.example.com\n" % ("x" * 48),
            encoding="utf-8",
        )
        monkeypatch.setattr(sys, "argv", ["check_env.py", "--env-file", str(env_file)])
        assert check_env.main() == 0

    def test_exit_1_when_env_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            sys, "argv", ["check_env.py", "--env-file", str(tmp_path / "nope.env")]
        )
        assert check_env.main() == 1

    def test_main_resets_results_between_runs(self, tmp_path, monkeypatch):
        """回归：_results 是模块级全局，同进程内多次 main() 必须互相隔离。

        曾经的真实缺陷：main() 不清空 _results，第二次运行会带着第一次的
        结果做统计，导致"好配置"被判为失败（错误数被前一次污染）。
        单跑该文件不显形，必须在同一进程内连续调用两次才会暴露。
        """
        _clear_process_env(monkeypatch)
        bad = tmp_path / "bad.env"
        bad.write_text("APP_ENV=prodction\n", encoding="utf-8")
        good = tmp_path / "good.env"
        good.write_text(
            "APP_ENV=production\nSECRET_KEY=%s\nINITIAL_ADMIN_PASSWORD=Str0ngPass\n"
            "TRUSTED_HOSTS=docnotice.example.com\n" % ("x" * 48),
            encoding="utf-8",
        )

        monkeypatch.setattr(sys, "argv", ["check_env.py", "--env-file", str(bad)])
        assert check_env.main() == 1

        monkeypatch.setattr(sys, "argv", ["check_env.py", "--env-file", str(good)])
        assert check_env.main() == 0, "第二次运行不得受第一次结果污染"
