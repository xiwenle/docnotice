# -*- coding: utf-8 -*-
"""生成干净的生产部署包（默认 release/docnotice-deploy-YYYYMMDD）。

用法：
    python scripts/package_for_deploy.py
    python scripts/package_for_deploy.py --out release/custom --zip

原则：使用明确允许清单，而不是“复制整个仓库再排除”。生产包只包含
运行代码、运维脚本、部署文档和启动模板；真实数据与密钥永不进入包内。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

ROOT_FILES = (
    ".env.example",
    "README.md",
    "DESIGN.md",
    "requirements.txt",
    "pyproject.toml",
)
ROOT_DIRS = ("app", "deploy")
SCRIPT_FILES = (
    "_paths.py",
    "check_env.py",
    "init_db.py",
    "migrate.py",
    "migrate_partial_unique.py",
)
SCRIPT_DIRS = ("migrations",)
DOC_FILES = (
    "API.md",
    "DATABASE.md",
    "DEPLOY.md",
    "DEPLOY_WINDOWS.md",
    "EMPLOYEE_GUIDE.md",
    "ADMIN_LAUNCH_CHECKLIST.md",
    "DESIGN.md",
    "TESTING.md",
)

SKIP_DIR_NAMES = {
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".git", ".venv", "data", "tests", "prototypes", "screenshots",
    "node_modules", ".workbuddy", ".agent-teams",
}
SKIP_SUFFIXES = {".pyc", ".pyo", ".log", ".db", ".sqlite", ".zip", ".pid"}
SKIP_FILES = {".env", ".env.bak", ".DS_Store"}


def _skip_path(path: Path) -> bool:
    return (
        path.name in SKIP_FILES
        or path.name in SKIP_DIR_NAMES
        or path.name.startswith("_agent_")
        or path.name.startswith("docnotice-tests-")
        or path.suffix.lower() in SKIP_SUFFIXES
    )


def _copy_file(src: Path, out_dir: Path, copied: list[Path]) -> None:
    if not src.is_file() or _skip_path(src):
        return
    rel = src.relative_to(PROJECT_ROOT)
    dst = out_dir / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    copied.append(dst)


def _copy_tree(src_root: Path, out_dir: Path, copied: list[Path]) -> None:
    for dirpath, dirnames, filenames in os.walk(src_root, topdown=True):
        current = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if not _skip_path(current / d))
        for name in sorted(filenames):
            _copy_file(current / name, out_dir, copied)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_manifest(out_dir: Path, copied: list[Path]) -> None:
    manifest = out_dir / "PACKAGE_SHA256.txt"
    lines = []
    for path in sorted(copied):
        lines.append(f"{_sha256(path)}  {path.relative_to(out_dir).as_posix()}")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _git_revision() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip() or "unknown"
    except Exception:  # noqa: BLE001 - 打包不应因非 Git 副本失败
        return "unknown"


def _write_build_info(out_dir: Path, copied: list[Path]) -> None:
    info = out_dir / "BUILD_INFO.txt"
    version = out_dir.name or "docnotice-deploy"
    all_files = sorted(p for p in out_dir.rglob("*") if p.is_file())
    info.write_text(
        "\n".join(
            [
                "作业资料系统部署包",
                f"版本: {version}",
                f"Git 提交: {_git_revision()}",
                f"生成时间: {datetime.now().isoformat(timespec='seconds')}",
                f"生成主机: {platform.node()}",
                f"Python: {platform.python_version()}",
                f"源文件数: {len(copied)}",
                f"包内文件数: {len(all_files)}",
                "包含: app / runtime scripts / deploy docs / start templates",
                "不包含: .env / data / .venv / tests / prototypes / audits / screenshots / Git",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _self_check(out_dir: Path) -> None:
    forbidden_names = {".env", "data", ".venv", ".git", "tests", "prototypes", "screenshots"}
    bad = [p for p in out_dir.rglob("*") if p.name in forbidden_names or p.name.startswith("_agent_")]
    if bad:
        raise SystemExit(f"❌ 部署包含禁止项：{[str(p) for p in bad[:10]]}")
    bad_files = [p for p in out_dir.rglob("*") if p.is_file() and p.suffix.lower() in SKIP_SUFFIXES]
    if bad_files:
        raise SystemExit(f"❌ 部署包含禁止文件：{[str(p) for p in bad_files[:10]]}")

    must_have = [
        "app/main.py",
        "requirements.txt",
        ".env.example",
        "deploy/DEPLOYMENT_README.md",
        "deploy/DELIVERY_MANIFEST.md",
        "deploy/VALIDATION.md",
        "deploy/windows/start_docnotice.bat",
        "deploy/linux/docnotice.service.example",
        "scripts/init_db.py",
        "scripts/migrate.py",
        "scripts/check_env.py",
        "docs/DEPLOY_WINDOWS.md",
    ]
    missing = [rel for rel in must_have if not (out_dir / rel).exists()]
    if missing:
        raise SystemExit(f"❌ 缺少必需文件：{missing}")
    secret_patterns = (
        b"Demo@123456",
        b"admin_demo / Demo",
        b"BEGIN RSA PRIVATE KEY",
        b"BEGIN OPENSSH PRIVATE KEY",
    )
    secret_hits = []
    for path in out_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".md", ".txt", ".py", ".js", ".html", ".css", ".toml", ".example"}:
            continue
        content = path.read_bytes()
        if any(pattern in content for pattern in secret_patterns):
            secret_hits.append(path.relative_to(out_dir).as_posix())
    if secret_hits:
        raise SystemExit(f"❌ 部署包含疑似真实凭据/私钥：{secret_hits}")

    migrations = list((out_dir / "scripts" / "migrations").glob("*.py"))
    if not migrations:
        raise SystemExit("❌ scripts/migrations 中没有迁移脚本")


def _make_zip(out_dir: Path) -> Path:
    zip_path = Path(str(out_dir) + ".zip")
    if zip_path.exists():
        raise SystemExit(f"ZIP 已存在，拒绝覆盖：{zip_path}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(out_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(out_dir.parent))
    digest = _sha256(zip_path)
    checksum = zip_path.with_suffix(zip_path.suffix + ".sha256")
    checksum.write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8")
    return zip_path


def build(out_dir: Path, also_zip: bool) -> None:
    if out_dir.exists() and any(out_dir.iterdir()):
        raise SystemExit(f"目标目录非空，拒绝覆盖：{out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []

    for rel in ROOT_FILES:
        _copy_file(PROJECT_ROOT / rel, out_dir, copied)
    for rel in ROOT_DIRS:
        _copy_tree(PROJECT_ROOT / rel, out_dir, copied)
    for rel in SCRIPT_FILES:
        _copy_file(PROJECT_ROOT / "scripts" / rel, out_dir, copied)
    for rel in SCRIPT_DIRS:
        _copy_tree(PROJECT_ROOT / "scripts" / rel, out_dir, copied)
    for rel in DOC_FILES:
        _copy_file(PROJECT_ROOT / "docs" / rel, out_dir, copied)

    _write_build_info(out_dir, copied)
    copied.append(out_dir / "BUILD_INFO.txt")
    _self_check(out_dir)
    _write_manifest(out_dir, copied)

    print(f"✅ 已生成部署目录：{out_dir}")
    print(f"✅ 文件数：{len(copied)}")
    print("✅ 自检通过：无 .env / data / .venv / tests / prototypes / 审计证据 / SQLite 文件")
    if also_zip:
        zip_path = _make_zip(out_dir)
        print(f"✅ ZIP：{zip_path}")
        print(f"✅ SHA256：{zip_path}.sha256")


def main() -> int:
    ap = argparse.ArgumentParser(description="生成干净的生产部署包")
    ap.add_argument("--out", default=None, help="输出目录；默认 release/docnotice-deploy-YYYYMMDD")
    ap.add_argument("--zip", action="store_true", help="同时生成 ZIP 和 SHA256")
    args = ap.parse_args()

    stamp = datetime.now().strftime("%Y%m%d")
    out = Path(args.out) if args.out else PROJECT_ROOT / "release" / f"docnotice-deploy-{stamp}"
    out = out.resolve()
    if out == PROJECT_ROOT:
        raise SystemExit("输出目录不能是项目根目录")
    if PROJECT_ROOT in out.parents:
        try:
            out.relative_to(PROJECT_ROOT / "release")
        except ValueError as exc:
            raise SystemExit("项目内输出只允许放在 release/ 下") from exc

    build(out, args.zip)
    print("\n上线前：")
    print("  ① 按 deploy/DEPLOYMENT_README.md 配置 .env")
    print("  ② python scripts/check_env.py")
    print("  ③ python scripts/init_db.py")
    print("  ④ 启动服务并完成备份/恢复演练")
    return 0


if __name__ == "__main__":
    sys.exit(main())