"""测试公共夹具（t23 测试基建；项目规范 §30–33、DESIGN.md §13）。

环境隔离设计
------------
`app.db.session` 在模块导入时即创建 engine（读 `get_settings()`），因此所有
环境变量（DATA_DIR / SECRET_KEY / INITIAL_*）必须在任何 `app.*` 导入**之前**
设置。本文件在模块顶部完成：

1. 创建 pytest 会话级临时目录（`tempfile.mkdtemp`），把 DATA_DIR / DATABASE_URL
   指向该目录 —— 测试数据库与业务数据与真实 `data/` 完全分离，绝不污染（§7、§38）。
2. 把项目根目录加入 sys.path —— 保证直接运行 `pytest`（而非 `python -m pytest`）
   也能 `import app`。
3. 会话结束（autouse）断言真实 `data/` 目录快照（相对路径/大小/mtime）与开始时
   完全一致，任何污染直接让测试失败。

后续每测试自动重建全部表（setup_db），测试之间函数级隔离。

注意：为什么 DATA_DIR 是“会话级”而非“每个测试 tmp_path”？
因为 engine/session 在导入期绑定配置，逐测试换目录需要重载整个 app 包，得不偿失；
会话级临时目录 + 每测试重建表已满足隔离要求（t23 说明中的 tmp_path 语义由此实现，
`data_dir` fixture 暴露该临时目录，可直接断言六目录结构）。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 0) 路径与环境准备 —— 必须位于任何 app.* 导入之前
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 会话级临时数据目录（pytest 结束后由 OS 清理 /tmp）
_TEST_TMP = Path(tempfile.mkdtemp(prefix="docnotice-tests-"))
_TEST_DATA_DIR = _TEST_TMP / "data"

os.environ["DATA_DIR"] = str(_TEST_DATA_DIR)
os.environ["DATABASE_URL"] = f"sqlite:///{(_TEST_DATA_DIR / 'database' / 'app.db').as_posix()}"
os.environ["SECRET_KEY"] = "test-secret-key-for-testing-only"
os.environ["APP_ENV"] = "test"
# 初始管理员（init_db 若被 lifespan 触发时使用；测试夹具不依赖它）
os.environ["INITIAL_ADMIN_USERNAME"] = "admin"
os.environ["INITIAL_ADMIN_PASSWORD"] = "admin1234"

# ---------------------------------------------------------------------------
# 1) 应用导入（环境已就绪）
# ---------------------------------------------------------------------------
from app.core.config import get_settings  # noqa: E402
from app.core.security import create_access_token, hash_password  # noqa: E402
from app.db.session import Base, SessionLocal, engine  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
import app.models  # noqa: E402,F401  确保全部模型注册到 Base.metadata

settings = get_settings()

# 测试密码（公共常量，供夹具与 helper 复用；满足强度要求：≥8 位含字母数字）
ADMIN_PASSWORD = "admin1234"
USER_PASSWORD = "user1234"

# 真实项目 data/ 目录快照守卫（相对路径 → (size, mtime_ns)）
def _snapshot_tree(root: Path) -> dict:
    snap: dict[str, tuple[int, int]] = {}
    if not root.exists():
        return snap
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            snap[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return snap


# 守卫豁免区：外部开发服务器（uvicorn）会持续写入真实 data/database/app.db*
# 并在备份演练时生成 data/backups/backup_* 目录——这些变化并非测试造成。
# 业务目录（documents/notices/uploads/recycle_bin）仍**严格**监控：测试一旦
# 污染真实业务文件，立即失败（§7/§11/§26）。
_GUARD_IGNORE_PREFIXES = ("database/app.db", "backups/backup_")


def _guard_filter(snap: dict) -> dict:
    """剔除豁免区后的快照（守卫与显式隔离断言共用同一口径）。"""
    return {
        k: v for k, v in snap.items()
        if not any(k.startswith(pfx) for pfx in _GUARD_IGNORE_PREFIXES)
    }


# 会话开始前的真实 data/ 快照（模块导入时采集；守卫与本测试共用）
_REAL_DATA_BEFORE = _guard_filter(_snapshot_tree(PROJECT_ROOT / "data"))


@pytest.fixture(scope="session")
def project_root() -> Path:
    """项目根目录。"""
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def data_dir() -> Path:
    """本次测试会话的临时数据目录（DATA_DIR，六目录结构由 ensure_dirs 创建）。"""
    return _TEST_DATA_DIR


@pytest.fixture(scope="session")
def db_path() -> Path:
    """测试数据库文件路径。"""
    return _TEST_DATA_DIR / "database" / "app.db"


@pytest.fixture(scope="session")
def real_data_before() -> dict:
    """会话开始前的真实 data/ 目录快照（供隔离断言使用）。"""
    return _REAL_DATA_BEFORE


@pytest.fixture(scope="session", autouse=True)
def _data_isolation_guard(data_dir):
    """数据隔离守卫：断言真实 data/（业务目录部分）在测试会话前后完全不变。

    豁免 database/app.db* 与 backups/backup_*（外部开发服务器活动区，见
    _GUARD_IGNORE_PREFIXES）；documents/notices/uploads/recycle_bin 严格比对。
    """
    before = _REAL_DATA_BEFORE
    assert settings.data_dir.resolve() == data_dir.resolve(), (
        "测试配置未生效：settings.data_dir 不在临时目录！"
    )
    yield
    after = _guard_filter(_snapshot_tree(PROJECT_ROOT / "data"))
    assert before == after, (
        "测试污染了真实 data/ 目录！测试必须使用临时 DATA_DIR（数据与程序分离 §7）。"
    )


# ---------------------------------------------------------------------------
# 2) 数据库
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def setup_db():
    """每个测试前重建全部表（函数级隔离）+ 种子默认通知类型（自 2026-09 起类型可自定义）。"""
    from app.models.notice import DEFAULT_NOTICE_TYPES, NoticeType

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        for i, name in enumerate(DEFAULT_NOTICE_TYPES):
            session.add(NoticeType(name=name, sort_order=i, is_active=True))
        session.commit()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    """提供测试数据库会话。"""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 3) HTTP 客户端
# ---------------------------------------------------------------------------
@pytest.fixture
def client():
    """提供测试客户端（不触发 lifespan；建表交给 setup_db 夹具）。"""
    from fastapi.testclient import TestClient

    return TestClient(fastapi_app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# 4) 用户 / 部门 / 分类 / 标签 夹具
# ---------------------------------------------------------------------------
@pytest.fixture
def user_factory(db):
    """创建任意用户（ORM 直插 + bcrypt 哈希，密码可指定）。"""
    users = []

    def _make(
        username: str,
        password: str = USER_PASSWORD,
        display_name: str | None = None,
        role: str = "user",
        is_active: bool = True,
        must_change_password: bool = False,
        department_ids: list[int] | None = None,
        **extra,
    ):
        from app.models.user import Department, User

        user = User(
            username=username,
            password_hash=hash_password(password),
            display_name=display_name or username,
            role=role,
            is_active=is_active,
            auth_source="local",
            must_change_password=must_change_password,
            **extra,
        )
        if department_ids:
            deps = db.query(Department).filter(Department.id.in_(department_ids)).all()
            user.departments = deps
        db.add(user)
        db.commit()
        db.refresh(user)
        users.append(user)
        return user

    return _make


@pytest.fixture
def admin_user(user_factory):
    """创建管理员（默认密码 ADMIN_PASSWORD；权限模型 v3 两角色）。"""
    return user_factory("admin", password=ADMIN_PASSWORD, display_name="测试管理员", role="admin")


@pytest.fixture
def normal_user(user_factory):
    """创建普通用户（默认密码 USER_PASSWORD）。"""
    return user_factory("zhangsan", password=USER_PASSWORD, display_name="张三", role="user")


@pytest.fixture
def department_factory(db):
    """创建部门。"""
    def _make(name: str = "财务部", description: str | None = None):
        # 2026-11-03：部门编码已移除，工厂不再接受 code 参数
        from app.models.user import Department

        dept = Department(name=name, description=description)
        db.add(dept)
        db.commit()
        db.refresh(dept)
        return dept

    return _make


@pytest.fixture
def department(department_factory):
    """默认部门。"""
    return department_factory()


@pytest.fixture
def category_factory(db):
    """创建分类（parent_id=None 为一级）。

    直连 ORM，绕过 service 的深度/成环校验——要验证 MAX_DEPTH 与重挂成环
    请走 API（见 tests/test_categories.py）。
    """
    def _make(name: str, parent_id: int | None = None, sort_order: int = 0):
        from app.models.category import Category

        cat = Category(name=name, parent_id=parent_id, sort_order=sort_order)
        db.add(cat)
        db.commit()
        db.refresh(cat)
        return cat

    return _make


@pytest.fixture
def category(category_factory):
    """默认一级分类。"""
    return category_factory("规章制度")


@pytest.fixture
def tag_factory(db):
    """创建标签。"""
    def _make(name: str):
        from app.models.category import Tag

        tag = Tag(name=name)
        db.add(tag)
        db.commit()
        db.refresh(tag)
        return tag

    return _make


@pytest.fixture
def tag(tag_factory):
    """默认标签。"""
    return tag_factory("重要")


# ---------------------------------------------------------------------------
# 5) 认证夹具：直接签发 JWT（快速）与 API 登录（真实链路）
# ---------------------------------------------------------------------------
@pytest.fixture
def admin_token(admin_user):
    """管理员 JWT（直接签发，不走登录接口）。"""
    return create_access_token(
        user_id=admin_user.id, username=admin_user.username, role=admin_user.role
    )


@pytest.fixture
def user_token(normal_user):
    """普通用户 JWT（直接签发）。"""
    return create_access_token(
        user_id=normal_user.id, username=normal_user.username, role=normal_user.role
    )


@pytest.fixture
def admin_headers(admin_token):
    """管理员认证头。"""
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def user_headers(user_token):
    """普通用户认证头。"""
    return {"Authorization": f"Bearer {user_token}"}


@pytest.fixture
def login(client):
    """通过真实登录接口换取 JWT（API.md §1 POST /api/v1/auth/login）。"""

    def _login(username: str, password: str = USER_PASSWORD) -> str:
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        assert resp.status_code == 200, f"登录失败 {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body.get("access_token"), f"登录响应缺少 access_token: {body}"
        return body["access_token"]

    return _login


@pytest.fixture
def headers_for(login):
    """为任意用户生成 Bearer 认证头（走真实登录，验证密码哈希链路）。"""

    def _headers(
        user,
        username: str | None = None,
        password: str = USER_PASSWORD,
    ) -> dict[str, str]:
        token = login(username or user.username, password)
        return {"Authorization": f"Bearer {token}"}

    return _headers


# ---------------------------------------------------------------------------
# 6) 工具类夹具
# ---------------------------------------------------------------------------
@pytest.fixture
def freeze():
    """freezegun.freeze_time（冻结时间，时效边界测试用，项目规范 §31）。

    用法：with freeze("2026-06-30 12:00:00"): ...
    """
    from freezegun import freeze_time

    return freeze_time

@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """每个测试前重置登录限速器（防全量测试间的状态污染）。"""
    from app.core.security_middleware import reset_login_rate_limiter
    reset_login_rate_limiter()
    yield
    reset_login_rate_limiter()
