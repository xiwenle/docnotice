"""Isolated sandbox preparation, seeding, snapshots, and clean-room checks."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from .config import MatrixConfig
from .io_utils import read_json, run_command, sha256_file, utc_now, write_json

TMP_ROOT_MARKER = "docnotice-e2e"
EXCLUDED_NAMES = {
    ".env",
    ".git",
    ".venv",
    "release",
    "screenshots",
    "prototypes",
    "_agent_*",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
}
EXCLUDED_SUFFIXES = (".db", ".sqlite", ".pyc", ".pyo")


def _is_excluded_name(name: str, parent: Path, root: Path) -> bool:
    if name in EXCLUDED_NAMES or name.startswith("_agent_"):
        return True
    if name == "data" and parent == root:
        return True
    if name == "seed_demo_manifest.json" and parent.name == "scripts":
        return True
    if name.endswith(EXCLUDED_SUFFIXES):
        return True
    return False


def _copytree_ignore(root: Path):
    def ignore(directory: str, names: list[str]) -> set[str]:
        parent = Path(directory)
        return {name for name in names if _is_excluded_name(name, parent, root)}
    return ignore


def assert_under_sandbox(cfg: MatrixConfig, path: Path, *, allow_root: bool = False) -> Path:
    resolved = path.resolve()
    root = cfg.sandbox_root.resolve()
    if resolved == root and allow_root:
        return resolved
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"refusing path outside sandbox: {resolved}") from exc
    if resolved == root and not allow_root:
        raise RuntimeError(f"refusing sandbox root without allow_root: {resolved}")
    return resolved


def _remove_sandbox_dir(cfg: MatrixConfig, path: Path) -> None:
    resolved = assert_under_sandbox(cfg, path, allow_root=(path.resolve() == cfg.sandbox_root.resolve()))
    if resolved.name in {"data", "code", "e2e_files", "runtime", "report"}:
        if resolved.parent != cfg.sandbox_root.resolve():
            raise RuntimeError(f"unexpected sandbox directory parent: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


def _tree_entries(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    entries: list[dict[str, Any]] = []
    for current, dirnames, filenames in os.walk(root):
        current_path = Path(current)
        dirnames.sort()
        filenames.sort()
        for dirname in dirnames:
            path = current_path / dirname
            relative = path.relative_to(root).as_posix()
            stat = path.stat()
            entries.append({
                "path": relative,
                "type": "dir",
                "size": 0,
                "mtime_ticks": int(stat.st_mtime_ns // 100),
                "mtime_ns": stat.st_mtime_ns,
                "sha256": None,
            })
        for filename in filenames:
            path = current_path / filename
            relative = path.relative_to(root).as_posix()
            stat = path.stat()
            entries.append({
                "path": relative,
                "type": "file",
                "size": stat.st_size,
                "mtime_ticks": int(stat.st_mtime_ns // 100),
                "mtime_ns": stat.st_mtime_ns,
                "sha256": sha256_file(path),
            })
    entries.sort(key=lambda item: item["path"])
    return entries


def tree_snapshot(root: Path) -> dict[str, Any]:
    """Full, no-exemption snapshot including databases, backups, and logs.

    The file content is read only for hashing.  The real .env is handled
    separately below and its bytes are never printed or copied.
    """
    return {
        "root": str(root.resolve()),
        "captured_at": utc_now(),
        "exists": root.exists(),
        "entries": _tree_entries(root),
    }


def real_state_snapshot(cfg: MatrixConfig) -> dict[str, Any]:
    real_data = cfg.source_root / "data"
    real_env = cfg.source_root / ".env"
    return {
        "captured_at": utc_now(),
        "data": tree_snapshot(real_data),
        "env": {
            "path": str(real_env),
            "exists": real_env.exists(),
            "sha256": sha256_file(real_env) if real_env.is_file() else None,
        },
    }


def diff_tree_snapshots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Strictly compare paths, types, sizes, mtimes and file SHA-256."""
    before_map = {item["path"]: item for item in before.get("entries", [])}
    after_map = {item["path"]: item for item in after.get("entries", [])}
    changed: list[dict[str, Any]] = []
    for path in sorted(set(before_map) | set(after_map)):
        left = before_map.get(path)
        right = after_map.get(path)
        if left != right:
            changed.append({"path": path, "before": left, "after": right})
    return {
        "before_root": before.get("root"),
        "after_root": after.get("root"),
        "changed_count": len(changed),
        "changed": changed,
    }


def write_real_snapshots(cfg: MatrixConfig, *, phase: str) -> dict[str, Any]:
    cfg.ensure_layout()
    snapshot = real_state_snapshot(cfg)
    write_json(cfg.evidence_root / f"data-snapshot-{phase}.json", snapshot)
    env_path = cfg.evidence_root / f"real-env-{phase}.sha256"
    env_hash = snapshot.get("env", {}).get("sha256") or "MISSING"
    env_path.write_text(str(env_hash) + "\n", encoding="utf-8", newline="\n")
    return snapshot


def compare_real_snapshots(cfg: MatrixConfig, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    data_diff = diff_tree_snapshots(before.get("data", {}), after.get("data", {}))
    env_before = (before.get("env") or {}).get("sha256")
    env_after = (after.get("env") or {}).get("sha256")
    result = {
        "captured_at": utc_now(),
        "data": data_diff,
        "env_before": env_before,
        "env_after": env_after,
        "env_changed": env_before != env_after,
        "changed": data_diff["changed_count"] > 0 or env_before != env_after,
    }
    write_json(cfg.evidence_root / "data-diff.json", result)
    return result


def copy_source(cfg: MatrixConfig) -> Path:
    """Copy the source tree without .env, real data, caches, or evidence dirs."""
    if cfg.code_dir.exists():
        _remove_sandbox_dir(cfg, cfg.code_dir)
    cfg.code_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        cfg.source_root,
        cfg.code_dir,
        dirs_exist_ok=True,
        ignore=_copytree_ignore(cfg.source_root),
        copy_function=shutil.copy2,
    )
    if (cfg.code_dir / ".env").exists():
        raise RuntimeError("sandbox contains .env after copy")
    if (cfg.code_dir / "data").exists():
        raise RuntimeError("sandbox contains data/ after copy")
    return cfg.code_dir


def initialize_layout(cfg: MatrixConfig) -> None:
    cfg.ensure_layout()
    for name in ("database", "documents", "notices", "uploads", "recycle_bin", "backups", "logs"):
        (cfg.data_dir / name).mkdir(parents=True, exist_ok=True)
    (cfg.e2e_files_dir).mkdir(parents=True, exist_ok=True)
    (cfg.fms_tmp_dir / "fms_e2e_data").mkdir(parents=True, exist_ok=True)


_PASSWORD_SYNC = r'''
import os
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.user import User
from app.core.security import hash_password

admin_pwd = os.environ.get("E2E_ADMIN_PWD", "Demo@123456")
user_pwd = os.environ.get("E2E_USER_PWD", "Demo@123456")
pairs = [
    ("admin_demo", admin_pwd),
    ("admin2_demo", admin_pwd),
    ("user_rnd_1", user_pwd),
    ("user_fin_1", user_pwd),
    ("user_hr_1", user_pwd),
]
db = SessionLocal()
try:
    changed = 0
    for username, password in pairs:
        user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if user is None:
            continue
        user.password_hash = hash_password(password)
        user.must_change_password = False
        user.token_version = int(user.token_version or 0) + 1
        changed += 1
    db.commit()
    print(f"demo-passwords-synced:{changed}")
finally:
    db.close()
'''


def sync_demo_passwords(cfg: MatrixConfig) -> dict[str, Any]:
    result = run_command(
        [str(cfg.source_python), "-c", _PASSWORD_SYNC],
        cwd=cfg.code_dir,
        env=cfg.child_env(),
        log_path=cfg.evidence_root / "prepare" / "sync-demo-passwords.log",
        timeout_seconds=300,
        check=True,
        extra_secrets=(cfg.initial_admin_password, cfg.admin_password, cfg.user_password),
    )
    return {"exit_code": result.exit_code, "log": result.log_path}

def _seed_command(cfg: MatrixConfig, script: str, log_name: str) -> dict[str, Any]:
    cmd = [str(cfg.source_python), str(cfg.code_dir / "scripts" / script)]
    result = run_command(
        cmd,
        cwd=cfg.code_dir,
        env=cfg.child_env(),
        log_path=cfg.evidence_root / "prepare" / log_name,
        timeout_seconds=900,
        check=True,
        extra_secrets=(cfg.initial_admin_password, cfg.admin_password, cfg.user_password),
    )
    return {
        "command": [str(cfg.source_python), f"scripts/{script}"],
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "log": result.log_path,
    }


def seed_sandbox(cfg: MatrixConfig) -> dict[str, Any]:
    if not cfg.code_dir.exists():
        raise RuntimeError("sandbox code is not prepared")
    init_db = _seed_command(cfg, "init_db.py", "init_db.log")
    seed_demo = _seed_command(cfg, "seed_demo.py", "seed_demo.log")
    password_sync = sync_demo_passwords(cfg)
    return {
        "init_db": init_db,
        "seed_demo": seed_demo,
        "password_sync": password_sync,
        "database_exists": cfg.database_path.exists(),
        "database_sha256": sha256_file(cfg.database_path) if cfg.database_path.exists() else None,
    }


_FILE_GENERATOR = r'''
from __future__ import annotations
import base64
import sys
from pathlib import Path

root = Path.cwd()
sys.path.insert(0, str(root))
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)

import importlib.util
_generators_path = root / "tests" / "files" / "generators.py"
_spec = importlib.util.spec_from_file_location("fm_file_generators", _generators_path)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"cannot load file generator: {_generators_path}")
generators = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generators)

good_pdf = generators.make_pdf(out / "员工手册2026.pdf", pages=2)
generators.make_docx(out / "公司制度.docx")
generators.make_xlsx(out / "费用报销表.xlsx")
generators.make_xls(out / "工资明细表.xls")
generators.make_pptx(out / "季度汇报.pptx")
# Large but structurally valid PDF used by the upload-limit case.
try:
    generators.make_large_pdf(out / "超大文件.pdf", target_bytes=60 * 1024 * 1024)
except Exception as exc:
    print(f"large-pdf-fallback: {exc}")
    with (out / "超大文件.pdf").open("wb") as fh:
        fh.write(b"%PDF-1.4\n% oversized fixture for rejection path\n")
        chunk = b"0" * (1024 * 1024)
        for _ in range(60):
            fh.write(chunk)
        fh.write(b"\n%%EOF\n")
try:
    generators.make_corrupted(good_pdf, out / "损坏的.pdf")
except Exception as exc:
    print(f"corrupted-pdf-fallback: {exc}")
    (out / "损坏的.pdf").write_bytes(b"%PDF-1.4\n\x00\x01broken")
(out / "伪装成PDF的可执行文件.pdf").write_bytes(b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 256)
(out / "木马.exe").write_bytes(b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 256)
(out / "..%2f..%2f机密.pdf").write_bytes(good_pdf.read_bytes())
(out / "..%2F..%2F..%2Fetc%2Fpasswd.pdf").write_bytes(good_pdf.read_bytes())
(out / "path%5C..%5C..%5C机密.pdf").write_bytes(good_pdf.read_bytes())
(out / "空文件.pdf").write_bytes(b"")
(out / "假图片.png").write_bytes(b"%PDF-1.4 this is not an image")
# Valid, browser-decodable PNG.
png = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
(out / "测试图片.png").write_bytes(png)
# Valid tiny JPEG.
jpeg = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAH/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAEFAqf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/AX//xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/AX//xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAY/Aqf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/IV//2gAMAwEAAgADAAAAEP/EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQMBAT8Qf//EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQIBAT8Qf//EABQQAQAAAAAAAAAAAAAAAAAAABD/2gAIAQEAAT8Qf//Z"
)
(out / "测试图片.jpg").write_bytes(jpeg)
print("files-ok", len(list(out.iterdir())))
'''


def generate_e2e_files(cfg: MatrixConfig) -> dict[str, Any]:
    initialize_layout(cfg)
    helper = cfg.sandbox_root / "runtime" / "make_e2e_files.py"
    helper.parent.mkdir(parents=True, exist_ok=True)
    helper.write_text(_FILE_GENERATOR, encoding="utf-8", newline="\n")
    result = run_command(
        [str(cfg.source_python), str(helper), str(cfg.e2e_files_dir)],
        cwd=cfg.code_dir,
        env=cfg.child_env(),
        log_path=cfg.evidence_root / "prepare" / "make_e2e_files.log",
        timeout_seconds=1200,
        check=True,
        extra_secrets=(cfg.initial_admin_password, cfg.admin_password, cfg.user_password),
    )
    inventory = []
    for path in sorted(cfg.e2e_files_dir.iterdir()):
        if path.is_file():
            inventory.append({"name": path.name, "size": path.stat().st_size, "sha256": sha256_file(path)})
    write_json(cfg.evidence_root / "prepare" / "e2e-files.json", inventory)
    return {"exit_code": result.exit_code, "files": inventory, "log": result.log_path}


_VERIFY_SNIPPET = r'''
import json
import os
from pathlib import Path
from app.core.config import get_settings

s = get_settings()
data = Path(os.environ["DATA_DIR"]).resolve()
expected_db = data / "database" / "app.db"
result = {
    "data_dir": str(s.data_dir),
    "database_path": str(s.database_path),
    "database_url": s.database_url,
    "cookie_secure": bool(s.cookie_secure),
    "backup_schedule": s.BACKUP_SCHEDULE,
    "app_env": s.app_env,
    "expected_data_dir": str(data),
    "expected_database_path": str(expected_db),
}
assert Path(result["data_dir"]).resolve() == data, result
assert Path(result["database_path"]).resolve() == expected_db, result
assert result["database_url"].startswith("sqlite:///"), result
assert result["cookie_secure"] is False, result
assert result["backup_schedule"] == "", result
assert result["app_env"] == "test", result
print("sandbox-config-ok")
print(json.dumps(result, ensure_ascii=False))
'''


def verify_config(cfg: MatrixConfig) -> dict[str, Any]:
    result = run_command(
        [str(cfg.source_python), "-c", _VERIFY_SNIPPET],
        cwd=cfg.code_dir,
        env=cfg.child_env(),
        log_path=cfg.evidence_root / "prepare" / "verify-config.log",
        timeout_seconds=120,
        check=True,
        extra_secrets=(cfg.initial_admin_password, cfg.admin_password, cfg.user_password),
    )
    text = (cfg.evidence_root / "prepare" / "verify-config.log").read_text(encoding="utf-8")
    if "sandbox-config-ok" not in text:
        raise RuntimeError("config verification did not report success")
    return {
        "ok": True,
        "log": result.log_path,
        "sandbox_root": str(cfg.sandbox_root),
        "data_dir": str(cfg.data_dir),
        "database_path": str(cfg.database_path),
    }


def prepare(cfg: MatrixConfig, *, reset: bool = False, preserve_before: bool = False) -> dict[str, Any]:
    """Prepare the clean sandbox and return a public manifest."""
    if reset:
        _remove_sandbox_dir(cfg, cfg.sandbox_root)
    cfg.ensure_layout()
    before = read_json(cfg.evidence_root / "data-snapshot-before.json", default=None) if preserve_before else None
    if before is None:
        before = write_real_snapshots(cfg, phase="before")
    copy_source(cfg)
    initialize_layout(cfg)
    seeded = seed_sandbox(cfg)
    files = generate_e2e_files(cfg)
    config_check = verify_config(cfg)
    manifest = {
        **cfg.public_dict(),
        "prepared_at": utc_now(),
        "real_data_before": before.get("data", {}).get("captured_at"),
        "real_env_sha256": before.get("env", {}).get("sha256"),
        "seed": seeded,
        "e2e_files": files.get("files", []),
        "config_check": config_check,
    }
    write_json(cfg.evidence_root / "prepare" / "sandbox.json", manifest)
    write_json(cfg.evidence_root / "run-manifest.json", manifest)
    return manifest


def validate_no_env_or_real_data(cfg: MatrixConfig) -> None:
    if (cfg.code_dir / ".env").exists():
        raise RuntimeError("sandbox code contains .env")
    if (cfg.code_dir / "data").exists():
        raise RuntimeError("sandbox code contains data/")
    if cfg.data_dir == cfg.source_root / "data":
        raise RuntimeError("sandbox DATA_DIR equals real data directory")


def snapshot_after(cfg: MatrixConfig) -> dict[str, Any]:
    after = write_real_snapshots(cfg, phase="after")
    before = __import__("json").loads((cfg.evidence_root / "data-snapshot-before.json").read_text(encoding="utf-8"))
    return compare_real_snapshots(cfg, before, after)


__all__ = [
    "assert_under_sandbox",
    "compare_real_snapshots",
    "copy_source",
    "diff_tree_snapshots",
    "generate_e2e_files",
    "initialize_layout",
    "prepare",
    "real_state_snapshot",
    "seed_sandbox",
    "sha256_file",
    "snapshot_after",
    "tree_snapshot",
    "validate_no_env_or_real_data",
    "verify_config",
    "write_real_snapshots",
]