"""t33 修复回归：管理员账号管理边界 + S-1（文件替换原子性）。

对应 docs/history/CODE_REVIEW_2026-08-26.md；权限模型 v3（2026-08-29 两角色合并）：
- 原 B-1「admin 不可管理 superadmin」的跨层级边界已随 superadmin 并入 admin 而消失；
  等价保护迁移为：不能停用自己 / 不能改自己角色 / 最后一个启用 admin 不可停用或降级 /
  普通用户访问管理端点一律 403（§33 后端强制）
- S-1：替换时旧文件磁盘缺失 → 曾产生孤儿文件 + 资料永久卡死 → 修复后
      409 明确错误、无孤儿、修复后可继续替换
"""
from __future__ import annotations


import pytest

from files.generators import make_pdf
from helpers import create_document, login_headers, upload_document_file

pytestmark = pytest.mark.usefixtures("_data_isolation_guard")

ADMIN_PW = "manager1234"   # 第二管理员密码（满足强度：≥8 含字母数字）
SA_PW = "superadmin99"     # 历史账号密码（v3 迁移后仍为 admin）
SA2_PW = "secondadmin99"   # 第二管理员初始密码


# ---------------------------------------------------------------------------
# 权限模型 v3：管理员账号管理边界（原 B-1 superadmin 保护的等价迁移）
# ---------------------------------------------------------------------------
class TestAdminManagementRules:
    def _make_admin_actor(self, client, user_factory):
        """创建并登录一个 admin 角色账号。"""
        user_factory("manageractor", password=ADMIN_PW, role="admin")
        return login_headers(client, "manageractor", ADMIN_PW)

    def test_admin_can_reset_other_admin_password(
        self, client, user_factory, admin_token, db
    ):
        """v3：admin 可重置其他 admin 密码 → 200 + 强制改密（合并后无跨层级限制）。"""
        from app.models.user import User

        user_factory("二号管理员", password=SA2_PW, role="admin")
        sa = db.query(User).filter(User.username == "二号管理员").one()

        resp = client.post(
            f"/api/v1/users/{sa.id}/reset-password",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"new_password": "newpass8888"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["must_change_password"] is True

        assert login_headers(client, "二号管理员", "newpass8888")
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "二号管理员", "password": SA2_PW},
        )
        assert resp.status_code == 401, "旧密码应失效"

    def test_admin_cannot_disable_self(
        self, client, admin_user, admin_headers
    ):
        """v3：不能停用自己的账号（防自锁）→ 409。"""
        resp = client.request(
            "DELETE", f"/api/v1/users/{admin_user.id}", headers=admin_headers
        )
        assert resp.status_code == 409, f"停用自己应 409: {resp.text}"
        assert "自己" in resp.json()["error"]["message"]

    def test_admin_cannot_change_own_role(
        self, client, admin_user, admin_headers
    ):
        """v3：不能修改自己的角色（防误操作自降后无人可恢复）→ 409。"""
        resp = client.put(
            f"/api/v1/users/{admin_user.id}",
            headers=admin_headers,
            json={"role": "user"},
        )
        assert resp.status_code == 409, f"改自己角色应 409: {resp.text}"

    def test_normal_user_cannot_list_users(self, client, user_headers):
        """§33：普通用户访问用户管理端点 → 403（后端强制，非前端隐藏）。"""
        resp = client.get("/api/v1/users", headers=user_headers)
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"]["code"] == "FORBIDDEN"

    def test_two_active_admins_can_deactivate_each_other(
        self, client, user_factory, admin_headers, db
    ):
        """v3：存在多个启用 admin 时，停用另一个 admin → 200（剩余 ≥1 不触发锁死保护）。"""

        other = user_factory("三号管理员", password=SA2_PW, role="admin")
        resp = client.request(
            "DELETE", f"/api/v1/users/{other.id}", headers=admin_headers
        )
        assert resp.status_code == 200, resp.text
        db.refresh(other)
        assert not other.is_active

    def test_last_active_admin_cannot_be_downgraded(
        self, client, db, user_factory
    ):
        """v3 纵深防御：最后一个启用 admin 不可被停用/降级（服务层直调验证，
        覆盖未来可能新增的绕过自我保护的调用路径）。"""
        from app.core.errors import AppError
        from app.services import user_service

        keeper = user_factory("看守管理员", role="admin", password="manager1234")
        actor = user_factory("离职管理员", role="admin", password="manager1234")
        # 仅保留 keeper 一个启用 admin
        actor.is_active = False
        db.commit()

        # 服务层直调：即使 actor 绕过路由层，最后一个启用 admin 也不可被停用
        try:
            user_service.update_user(
                db, current_user=actor, target_id=keeper.id, is_active=False
            )
            raise AssertionError("停用最后一个启用 admin 应被拒绝")
        except AppError as e:
            assert e.code == "CONFLICT"
        db.rollback()

# ---------------------------------------------------------------------------
# S-1：文件替换两阶段原子（无孤儿、不卡死、可明确处理）
# ---------------------------------------------------------------------------
class TestS1AtomicReplace:
    def _internal_name(self, db, file_record_id: int) -> str:
        from app.models.document import FileRecord

        rec = db.get(FileRecord, file_record_id)
        assert rec is not None
        return rec.internal_name

    def test_replace_normal_roundtrip(
        self, client, admin_headers, db, tmp_path, data_dir
    ):
        """S-1 回归：正常替换 1→2→3 均 200，每次 rev_count+1，旧文件进回收站。"""
        doc = create_document(client, admin_headers, title="原子替换回归")
        doc_id = doc["id"]

        v1 = make_pdf(tmp_path / "v1.pdf")
        r1 = upload_document_file(
            client, admin_headers, doc_id, filename="v1.pdf", content=v1.read_bytes()
        )
        assert r1.status_code == 200, r1.text
        assert r1.json()["rev_count"] == 1
        old_rec_id = r1.json()["current_file"]["id"]

        v2 = make_pdf(tmp_path / "v2.pdf")
        r2 = upload_document_file(
            client, admin_headers, doc_id, filename="v2.pdf", content=v2.read_bytes()
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["rev_count"] == 2
        assert r2.json()["recycled_file_id"] == old_rec_id

        v3 = make_pdf(tmp_path / "v3.pdf")
        r3 = upload_document_file(
            client, admin_headers, doc_id, filename="v3.pdf", content=v3.read_bytes()
        )
        assert r3.status_code == 200, f"替换 3 次后资料不得卡死: {r3.text}"
        assert r3.json()["rev_count"] == 3

        # 回收站有 2 条 replaced 记录
        resp = client.get("/api/v1/recycle-bin?reason=replaced", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] == 2

    def test_replace_with_missing_old_file_no_orphan_and_recoverable(
        self, client, admin_headers, db, tmp_path, data_dir
    ):
        """S-1 核心：旧文件磁盘缺失时替换 → 409 明确错误、无孤儿文件、
        DB 无脏记录、修复（文件回位）后可继续替换。"""
        from app.models.document import FileRecord

        doc = create_document(client, admin_headers, title="旧文件缺失场景")
        doc_id = doc["id"]

        v1 = make_pdf(tmp_path / "v1.pdf")
        r1 = upload_document_file(
            client, admin_headers, doc_id, filename="v1.pdf", content=v1.read_bytes()
        )
        assert r1.status_code == 200
        internal = self._internal_name(db, r1.json()["current_file"]["id"])
        old_path = data_dir / "documents" / internal[:2] / internal
        assert old_path.exists()

        # 记录基线：documents 树文件集合（含分片子目录）/ file_records 行数 / 文档 current_file
        # （目录内可能残留其他测试的文件，因此孤儿断言只用“无新增”口径）
        files_before = {
            p.relative_to(data_dir).as_posix()
            for p in (data_dir / "documents").rglob("*") if p.is_file()
        }
        recs_before = db.query(FileRecord).count()

        # 模拟磁盘损坏：删除旧文件（项目规范 §32 文件不存在场景）
        old_path.unlink()

        # 替换 → 409 明确业务错误（不再 404 卡死，也不产生孤儿）
        v2 = make_pdf(tmp_path / "v2.pdf")
        r2 = upload_document_file(
            client, admin_headers, doc_id, filename="v2.pdf", content=v2.read_bytes()
        )
        assert r2.status_code == 409, f"应返回 409 明确错误: {r2.text}"
        body = r2.json()
        assert body["error"]["code"] == "CONFLICT"
        assert "缺失" in body["error"]["message"]

        # 1) 无孤儿文件：失败替换后 documents 树未新增任何文件（旧文件被本测试删除属预期）
        files_after = {
            p.relative_to(data_dir).as_posix()
            for p in (data_dir / "documents").rglob("*") if p.is_file()
        }
        added = files_after - files_before
        assert not added, f"产生孤儿文件: {added}"

        # 2) DB 无脏记录：file_records 行数不变、文档 current_file 仍指向旧记录
        assert db.query(FileRecord).count() == recs_before
        from app.models.document import Document

        doc_row = db.get(Document, doc_id)
        assert doc_row.current_file_id == r1.json()["current_file"]["id"]
        assert doc_row.rev_count == 1

        # 3) 可明确处理：把文件放回（模拟管理员经回收站/恢复修复），再替换 → 200
        (data_dir / "documents" / internal[:2]).mkdir(parents=True, exist_ok=True)
        (data_dir / "documents" / internal[:2] / internal).write_bytes(v1.read_bytes())
        r3 = upload_document_file(
            client, admin_headers, doc_id, filename="v2.pdf", content=v2.read_bytes()
        )
        assert r3.status_code == 200, f"修复后替换应成功: {r3.text}"
        assert r3.json()["rev_count"] == 2

# ---------------------------------------------------------------------------
# S-1 补偿路径回归：替换失败时孤儿按分片路径清理（code review 2026-08-30）
# ---------------------------------------------------------------------------
class TestReplaceCompensationShardPath:
    def test_replace_failure_cleans_orphan_in_shard_dir(
        self, client, admin_headers, db, tmp_path, data_dir, monkeypatch
    ):
        """历史缺陷：补偿清理用扁平路径 documents/<名>，与分片落盘
        documents/<前2位>/<名> 不符 → 替换失败后孤儿文件永不清理。
        修复后：失败注入下孤儿被删、旧文件回位、DB 回滚到 v1。"""
        from app.models.document import FileRecord
        from app.services import file_service

        doc = create_document(client, admin_headers, title="替换失败补偿-分片路径")
        doc_id = doc["id"]
        v1 = make_pdf(tmp_path / "v1.pdf")
        r1 = upload_document_file(
            client, admin_headers, doc_id, filename="v1.pdf", content=v1.read_bytes()
        )
        assert r1.status_code == 200
        rec1 = db.get(FileRecord, r1.json()["current_file"]["id"])
        v1_path = data_dir / "documents" / rec1.internal_name[:2] / rec1.internal_name
        assert v1_path.exists()

        # 基线：documents 树全部文件
        files_before = {
            p.relative_to(data_dir).as_posix()
            for p in (data_dir / "documents").rglob("*")
            if p.is_file()
        }

        # 注入阶段 3 失败：审计写入抛异常 → 回滚 → 走物理补偿
        def _boom(*args, **kwargs):
            raise RuntimeError("注入的失败：模拟提交前异常")

        monkeypatch.setattr(file_service.audit_service, "log", _boom)
        v2 = make_pdf(tmp_path / "v2.pdf")
        r2 = upload_document_file(
            client, admin_headers, doc_id, filename="v2.pdf", content=v2.read_bytes()
        )
        assert r2.status_code == 500, f"注入失败应返回 500: {r2.text}"

        # 1) 无孤儿：documents 树无任何新增文件（v2 的分片孤儿已被删除）
        files_after = {
            p.relative_to(data_dir).as_posix()
            for p in (data_dir / "documents").rglob("*")
            if p.is_file()
        }
        assert files_after - files_before == set(), f"存在孤儿残留: {files_after - files_before}"

        # 2) 旧文件已从回收站回位
        assert v1_path.exists(), "旧文件未回位"

        # 3) DB 回滚：current_file 仍为 v1，版本号未变，回收站无 replaced 记录
        db.expire_all()
        from app.models.document import Document

        document = db.get(Document, doc_id)
        assert document.current_file_id == rec1.id
        assert document.rev_count == 1
        resp = client.get("/api/v1/recycle-bin?reason=replaced", headers=admin_headers)
        assert resp.json()["total"] == 0
