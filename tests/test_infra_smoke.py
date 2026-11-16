"""测试基础设施自检（t23）。

验证内容：
- 应用可导入、健康检查与骨架首页可达（TestClient 工作）
- 真实登录链路（ORM 建用户 + API 登录 + /auth/me）
- 数据隔离：DATA_DIR 指向临时目录、六目录结构齐全、真实 data/ 未被触碰
- 真实文件生成器：PDF/DOCX/XLSX/XLS 全部可被对应解析器读取（中文内容）、
  中文文件名、大小/哈希可测、大文件目标大小可配置、损坏/重复文件构造正确
- freezegun 冻结时间可用（时效边界测试前置）

这些测试**不依赖**后端阶段2/3（资料/通知路由），当前即可全绿。
"""
from __future__ import annotations

from datetime import datetime

import pytest

from files.generators import (
    DOCX_FILENAME,
    PDF_FILENAME,
    XLS_FILENAME,
    XLSX_FILENAME,
    make_corrupted,
    make_docx,
    make_duplicate,
    make_large_pdf,
    make_pdf,
    make_xls,
    make_xlsx,
    sha256_of,
    write_raw_file,
)


# ---------------------------------------------------------------------------
# 应用与数据隔离
# ---------------------------------------------------------------------------
class TestAppSmoke:
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"

    def test_index_page(self, client):
        """未登录访问 / 最终落到登录页（欢迎壳已移除，2026-09-03；
        TestClient 默认跟随重定向）。"""
        resp = client.get("/")
        assert resp.status_code == 200
        assert "登录" in resp.text

    def test_dashboard_alias_redirects_home(self, client):
        """2026-09-15：/dashboard 是外部自然猜法（书签、next 参数、口口相传），
        此前直接落 404 返回裸 JSON。现为 301 永久重定向到工作台 "/"。

        不跟随重定向，直接断言状态码与 Location，防止有人把 301 改成 302 或删掉。
        """
        resp = client.get("/dashboard", follow_redirects=False)
        assert resp.status_code == 301
        assert resp.headers["location"] == "/"

    def test_dashboard_alias_reaches_workbench(self, client, admin_user, login):
        """别名跟随重定向后必须真的到达工作台（而非再跳登录页）。"""
        import re

        from tests.conftest import ADMIN_PASSWORD

        token = login("admin", ADMIN_PASSWORD)
        resp = client.get(
            "/dashboard", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        assert "工作台" in resp.text
        assert not re.search(r'"error"\s*:\s*\{', resp.text), "工作台返回了错误 JSON"

    def test_docs_disabled_in_production(self, project_root):
        """生产环境必须关闭 /docs、/redoc、/openapi.json（§2 非目标第 10 条）。

        用独立子进程构造 production 配置下的 app，断言三者都不在路由表里；
        同时确认业务路由数量未受影响（防止误伤）。
        """
        import os
        import subprocess
        import sys

        code = (
            "from app.main import app;"
            "p=[str(getattr(r,'path','')) for r in app.routes];"
            "print(any(x in ('/docs','/redoc','/openapi.json') for x in p),"
            "len([x for x in p if x.startswith('/api/v1')]))"
        )
        env = dict(os.environ)
        env.update(
            APP_ENV="production",
            SECRET_KEY="x" * 64,
            INITIAL_ADMIN_PASSWORD="Str0ng-Pass-2026!",
            DATA_DIR="/tmp/fms_docs_probe",
        )
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, env=env,
            cwd=str(project_root),
        )
        assert out.returncode == 0, out.stderr
        docs_present, api_count = out.stdout.split()
        assert docs_present == "False", "生产环境仍暴露 API 文档路由"
        # 路由数随功能演进会增减（2026-11-04 删 /publish、/notices DELETE；
        # 2026-11-06 加 3 个附件端点）。这里只校验「数量稳定在合理区间且与
        # 文档表一致」，不再硬编码具体值——硬编码每次加接口就要改测试。
        assert int(api_count) >= 70, f"业务路由数异常偏少: {api_count}"

    def test_docs_available_in_dev(self, client):
        """开发环境保留文档三件套（联调用），确认修复没有一刀切关掉。"""
        assert client.get("/openapi.json").status_code == 200
        assert client.get("/docs").status_code == 200

    def test_app_env_typo_fails_fast(self, project_root):
        """APP_ENV 拼写错误必须拒绝启动（2026-09-15 失败安全加固）。

        此前 `APP_ENV=prodction` 会落到 else 分支，静默按开发模式启动：
        SECRET_KEY 校验、占位密码校验、Cookie Secure 全部失效，而运维以为
        生产加固已生效。现在非法值直接拒绝启动。
        """
        import os
        import subprocess
        import sys

        code = (
            "from app.core.config import get_settings;"
            "get_settings();print('STARTED')"
        )

        def run(app_env: str) -> subprocess.CompletedProcess:
            env = dict(os.environ)
            env.update(
                APP_ENV=app_env,
                SECRET_KEY="x" * 64,
                INITIAL_ADMIN_PASSWORD="Str0ng-Pass-2026!",
                DATA_DIR="/tmp/fms_appenv_probe",
            )
            return subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True, text=True, env=env, cwd=str(project_root),
            )

        for bad in ("prodction", "PROD", "production-x", ""):
            r = run(bad)
            assert r.returncode != 0, f"APP_ENV={bad!r} 未拒绝启动"
            assert "APP_ENV" in (r.stdout + r.stderr), f"APP_ENV={bad!r} 报错文案未指明原因"

        # 合法值（含大小写/尾随空格变体）必须可启动
        for good in ("production", "Production", "production ", "development", "test"):
            r = run(good)
            assert r.returncode == 0, f"APP_ENV={good!r} 被误拒: {r.stderr[-300:]}"
            assert "STARTED" in r.stdout

    def test_app_env_case_variants_still_disable_docs(self, project_root):
        """APP_ENV 归一化判定必须一致：大小写/空格变体的 production 也要关文档。

        2026-10-29 回归守卫。此前 main.py 用原始字符串 `!= "production"` 判断，
        而 check_secret_key 用归一化值——于是 `APP_ENV=Production` 时
        生产密钥校验生效了，但 `/docs`、`/redoc`、`/openapi.json` 仍对未登录开放
        （系统自称生产、实际暴露完整 API schema）。两个判定标准必须统一。
        """
        import os
        import subprocess
        import sys

        code = (
            "from app.core.config import get_settings;"
            "s=get_settings();"
            "print('PROD' if s.is_production else 'NONPROD')"
        )

        for variant in ("production", "Production", "PRODUCTION", " production "):
            env = dict(os.environ)
            env.update(
                APP_ENV=variant,
                SECRET_KEY="x" * 64,
                INITIAL_ADMIN_PASSWORD="Str0ng-Pass-2026!",
                DATA_DIR="/tmp/fms_appenv_docs_probe",
            )
            r = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True, text=True, env=env, cwd=str(project_root),
            )
            assert r.returncode == 0, f"APP_ENV={variant!r} 意外失败: {r.stderr[-300:]}"
            assert "PROD" in r.stdout and "NONPROD" not in r.stdout, (
                f"APP_ENV={variant!r} 未被识别为生产 → /docs 会对外开放"
            )

    def test_app_env_production_rejects_placeholder_secret(self, project_root):
        """生产模式 + 占位 SECRET_KEY → 拒绝启动（回归守卫，防加固被改回）。"""
        import os
        import subprocess
        import sys

        env = dict(os.environ)
        env.update(
            APP_ENV="production",
            SECRET_KEY="change-me-to-a-long-random-string",
            INITIAL_ADMIN_PASSWORD="Str0ng-Pass-2026!",
            DATA_DIR="/tmp/fms_appenv_probe2",
        )
        r = subprocess.run(
            [
                sys.executable,
                "-c",
                "from app.core.config import get_settings;get_settings()",
            ],
            capture_output=True, text=True, env=env, cwd=str(project_root),
        )
        assert r.returncode != 0
        assert "SECRET_KEY" in (r.stdout + r.stderr)

    def test_cookie_secure_follows_env_and_override(self, client, admin_user, login):
        """登录 Cookie 的 Secure 标志（2026-09-15 可配置）。

        默认按 APP_ENV 自动；显式 COOKIE_SECURE 优先。
        可配的动因：内网 http:// 部署若强制 Secure，浏览器会静默拒存 Cookie，
        表现为「登录成功却立刻退回登录页」，极难排查。
        """
        from app.core.config import get_settings

        settings = get_settings()
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": admin_user.username, "password": "admin1234"},
        )
        assert resp.status_code == 200
        set_cookie = resp.headers["set-cookie"]
        secure_attr = "secure" in set_cookie.lower()

        # 测试环境 APP_ENV=test → 自动 False；显式覆盖需生效
        assert secure_attr == settings.cookie_secure
        if settings.COOKIE_SECURE is None:
            assert settings.cookie_secure is False, "非生产环境不应默认开 Secure"

        # HttpOnly / SameSite 必须始终在位（本次改动不得顺手弄丢）
        low = set_cookie.lower()
        assert "httponly" in low
        assert "samesite=lax" in low

    def test_cookie_secure_override_true(self, monkeypatch, admin_user):
        """显式 COOKIE_SECURE=true 时，即使非生产也必须带上 Secure 标志。"""
        from app.core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "COOKIE_SECURE", True)
        assert settings.cookie_secure is True
        monkeypatch.setattr(settings, "COOKIE_SECURE", False)
        assert settings.cookie_secure is False

    def test_api_doc_table_matches_routes(self, project_root):
        """docs/API.md 末尾总表必须与真实路由完全一致（§34.2 文档随代码更新）。

        2026-09-15 修：总表仍列着 2026-09-03 已移除的
        `DELETE /api/v1/categories/{id}/purge`（正文自己都写了"已移除"，总表漏改）。
        本测试同时防"幽灵端点"（文档有代码无）与"漏记端点"（代码有文档无）。
        """
        import re

        from app.main import app

        actual = set()
        for r in app.routes:
            p = str(getattr(r, "path", ""))
            if p.startswith("/api/"):
                for m in getattr(r, "methods", None) or []:
                    if m in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                        actual.add((m, re.sub(r"\{[^}]+\}", "{}", p)))

        doc = (project_root / "docs" / "API.md").read_text(encoding="utf-8")
        table = doc[: doc.rfind("### 页面路由")]
        listed = set()
        for line in table.splitlines():
            mm = re.match(
                r"\|\s*(GET|POST|PUT|DELETE|PATCH)\s*\|\s*`(/api/v1/[^`]+)`\s*\|", line
            )
            if mm:
                listed.add((mm.group(1), re.sub(r"\{[^}]+\}", "{}", mm.group(2))))

        ghosts = sorted(listed - actual)
        missing = sorted(actual - listed)
        assert not ghosts, f"API.md 列了代码不存在的端点（幽灵）: {ghosts}"
        assert not missing, f"API.md 漏记了代码里的端点: {missing}"

    def test_data_dir_isolation(self, data_dir, db_path):
        """临时数据目录 + 六子目录结构 + 测试库文件落位（不污染真实 data/）。"""
        from app.core.config import get_settings

        settings = get_settings()
        assert settings.data_dir.resolve() == data_dir.resolve()
        for sub in ("database", "documents", "notices", "uploads", "recycle_bin", "backups"):
            assert (data_dir / sub).is_dir(), f"缺少数据子目录: {sub}"
        assert db_path.exists(), "测试数据库文件未创建"

    def test_real_data_untouched(self, project_root):
        """显式断言：真实 data/ 业务目录在本会话前后完全一致（快照比对）。

        说明：与 conftest 守卫同口径 —— 豁免 database/app.db* 与 backups/backup_*
        （外部开发服务器活动区）；documents/notices/uploads/recycle_bin 严格比对。
        """
        from conftest import _guard_filter, _REAL_DATA_BEFORE, _snapshot_tree

        real_data = project_root / "data"
        assert _guard_filter(_snapshot_tree(real_data)) == _REAL_DATA_BEFORE, "真实 data/ 业务目录被改动！"


# ---------------------------------------------------------------------------
# 认证链路（阶段1已实现）
# ---------------------------------------------------------------------------
class TestAuthSmoke:
    def test_api_login_and_me(self, client, admin_user, login):
        token = login(admin_user.username, "admin1234")
        resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        me = resp.json()
        assert me["username"] == admin_user.username
        assert me["role"] == "admin"

    def test_wrong_password_rejected(self, client, admin_user, login):
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": admin_user.username, "password": "wrong-pass-1"},
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "UNAUTHORIZED"

    def test_normal_user_cannot_manage_users(self, client, normal_user, login):
        """普通用户访问管理端点 → 403（权限由后端强制，§33）。"""
        token = login(normal_user.username, "user1234")
        resp = client.get(
            "/api/v1/users", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "FORBIDDEN"


# ---------------------------------------------------------------------------
# 真实文件生成器
# ---------------------------------------------------------------------------
class TestFileGenerators:
    def test_pdf_real_and_parseable(self, tmp_path):
        pdf = make_pdf(tmp_path / PDF_FILENAME, pages=2)
        assert pdf.name == PDF_FILENAME
        assert pdf.stat().st_size > 500

        from pypdf import PdfReader

        reader = PdfReader(str(pdf))
        assert len(reader.pages) == 2
        text = reader.pages[0].extract_text()
        assert "Company Internal Document Test" in text
        meta_title = (reader.metadata or {}).get("/Title", "")
        assert "员工手册" in str(meta_title)

    def test_docx_real_and_parseable(self, tmp_path):
        docx = make_docx(tmp_path / DOCX_FILENAME)
        assert docx.name == DOCX_FILENAME

        from docx import Document

        doc = Document(str(docx))
        paras = [p.text for p in doc.paragraphs if p.text.strip()]
        assert any("本制度" in p for p in paras), f"DOCX 段落缺少中文正文: {paras}"
        assert len(doc.tables) == 1
        assert doc.tables[0].cell(0, 0).text == "单元格1-1"

    def test_xlsx_real_and_parseable(self, tmp_path):
        xlsx = make_xlsx(tmp_path / XLSX_FILENAME, sheet_names=["费用明细", "汇总"], rows=5)
        assert xlsx.name == XLSX_FILENAME

        from openpyxl import load_workbook

        wb = load_workbook(str(xlsx), read_only=True, data_only=True)
        assert wb.sheetnames == ["费用明细", "汇总"]
        ws = wb["费用明细"]
        assert ws.max_row == 5
        assert ws.cell(row=1, column=1).value == "数据1-1"
        wb.close()

    def test_xls_real_and_parseable(self, tmp_path):
        xls = make_xls(tmp_path / XLS_FILENAME, sheet_names=["工资表", "考勤表"], rows=4)
        assert xls.name == XLS_FILENAME

        import xlrd

        book = xlrd.open_workbook(str(xls))
        assert book.sheet_names() == ["工资表", "考勤表"]
        ws = book.sheet_by_index(0)
        assert ws.nrows == 4
        assert "数据1-1" in str(ws.cell_value(0, 0))

    def test_magic_headers_match_spec(self, tmp_path):
        """魔数头与 DESIGN.md §5.1 一致（后续魔数校验/伪造扩展名测试依赖）。"""
        pdf = make_pdf(tmp_path / "a.pdf")
        docx = make_docx(tmp_path / "b.docx")
        xlsx = make_xlsx(tmp_path / "c.xlsx")
        xls = make_xls(tmp_path / "d.xls")
        assert pdf.read_bytes()[:5] == b"%PDF-"
        assert docx.read_bytes()[:4] == b"PK\x03\x04"
        assert xlsx.read_bytes()[:4] == b"PK\x03\x04"
        assert xls.read_bytes()[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

    def test_size_and_hash_measurable(self, tmp_path):
        pdf = make_pdf(tmp_path / PDF_FILENAME)
        digest = sha256_of(pdf)
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)

    def test_duplicate_files_same_hash(self, tmp_path):
        src = make_pdf(tmp_path / "源文件.pdf")
        dst = make_duplicate(src, tmp_path / "副本.pdf")
        assert sha256_of(src) == sha256_of(dst)
        assert src.read_bytes() == dst.read_bytes()

    def test_large_pdf_target_size(self, tmp_path):
        target = 2 * 1024 * 1024  # 2MB（可配置阈值）
        large = make_large_pdf(tmp_path / "大文件测试.pdf", target)
        actual = large.stat().st_size
        assert abs(actual - target) <= 2048, f"实际 {actual} 与目标 {target} 偏差过大"

        from pypdf import PdfReader

        reader = PdfReader(str(large))
        assert len(reader.pages) == 1  # 大文件仍可解析

    def test_corrupted_keeps_magic_but_breaks_parse(self, tmp_path):
        """损坏文件：保留魔数头（可过魔数校验）但解析必然失败（§32 文件损坏）。"""
        good = make_docx(tmp_path / DOCX_FILENAME)
        bad = make_corrupted(tmp_path / "损坏.docx", good)
        assert bad.read_bytes()[:4] == b"PK\x03\x04", "损坏文件应保留 ZIP 魔数头"
        assert bad.read_bytes() != good.read_bytes()

        from docx import Document

        with pytest.raises(Exception):
            Document(str(bad))

    def test_forged_extension_bytes(self, tmp_path):
        """伪造扩展名：exe 内容改名为 .pdf（魔数校验应拒绝，415）。"""
        exe_like = write_raw_file(tmp_path / "test.pdf", b"MZ\x90\x00" + b"\x00" * 64)
        assert exe_like.read_bytes()[:2] == b"MZ"


# ---------------------------------------------------------------------------
# freezegun（时效边界测试前置）
# ---------------------------------------------------------------------------
class TestFreezeTime:
    def test_freeze_works(self, freeze):
        # 本机系统时钟为 2026 年，使用 2024 作为冻结目标以区分真实时间
        with freeze("2024-01-15 12:00:00"):
            now = datetime.now()
            assert now.year == 2024
            assert now.month == 1
            assert now.day == 15
        assert datetime.now().year != 2024  # 退出冻结后恢复正常