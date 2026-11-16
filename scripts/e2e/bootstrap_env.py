"""E2E 环境自举：造测试文件 + 复制一份独立业务数据目录。

为什么需要它：E2E 会真的上传/删除/备份，绝不能跑在演示库 `./data` 上。
本脚本把 `./data` 复制成一份一次性副本（默认 <系统临时目录>/fms_e2e_data，
由 scripts/_paths.py 统一解析，Windows 与 Linux 各自正确），E2E 实例只指向
副本；跑坏了删掉重来即可。

用法：
    python scripts/e2e/bootstrap_env.py            # 重建（默认清掉旧副本）
    python scripts/e2e/bootstrap_env.py --keep     # 旧副本存在则跳过复制

随后启动被测实例（见 scripts/e2e/serve_e2e.sh）：
    DATA_DIR=<临时目录>/fms_e2e_data LOGIN_RATE_MAX_ATTEMPTS=5000 \n      python -m uvicorn app.main:app --port 8010
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from _paths import E2E_DATA_DIR, E2E_FILES_DIR, py_cmd  # noqa: E402

SRC_DATA = PROJECT_ROOT / "data"
DST_DATA = E2E_DATA_DIR
FILES = E2E_FILES_DIR


def make_files() -> None:
    """生成上传用测试文件（中文名，覆盖 PDF/DOCX/XLSX/XLS/伪造/非法）。"""
    FILES.mkdir(parents=True, exist_ok=True)

    # --- PDF：pypdf 造一页（预览链路只需可解析） ---
    pdf = FILES / "员工手册2026.pdf"
    if not pdf.exists():
        from pypdf import PdfWriter

        w = PdfWriter()
        w.add_blank_page(width=595, height=842)
        with pdf.open("wb") as fh:
            w.write(fh)

    # --- DOCX ---
    docx = FILES / "公司制度.docx"
    if not docx.exists():
        import docx as docxlib

        d = docxlib.Document()
        d.add_heading("公司制度（E2E 测试样本）", level=1)
        d.add_paragraph("本文件由自动化脚本生成，用于验证 DOCX 在线预览链路。")
        d.add_paragraph("第二段：中文正文，含数字 123 与英文 PDF。")
        d.save(str(docx))

    # --- XLSX ---
    xlsx = FILES / "费用报销表.xlsx"
    if not xlsx.exists():
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "报销明细"
        ws.append(["项目", "金额", "经办人"])
        ws.append(["差旅费", 1200, "张三"])
        ws.append(["办公用品", 350, "李四"])
        wb.save(str(xlsx))

    # --- XLS（老格式） ---
    xls = FILES / "工资明细表.xls"
    if not xls.exists():
        import xlwt

        wb = xlwt.Workbook()
        ws = wb.add_sheet("工资明细")
        for c, v in enumerate(["姓名", "基本工资", "绩效"]):
            ws.write(0, c, v)
        for r, row in enumerate([["张三", 8000, 2000], ["李四", 9000, 1500]], start=1):
            for c, v in enumerate(row):
                ws.write(r, c, v)
        wb.save(str(xls))

    # --- 伪造扩展名：MZ 头（exe）改名 .pdf，应被魔数校验拒绝 ---
    fake = FILES / "伪装成PDF的可执行文件.pdf"
    if not fake.exists():
        fake.write_bytes(b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 196)

    # --- 非法扩展名 ---
    exe = FILES / "木马.exe"
    if not exe.exists():
        exe.write_bytes(b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 196)

    # --- 损坏的 PDF（魔数正确、内容残破；m50 也会自备） ---
    broken = FILES / "损坏的.pdf"
    if not broken.exists():
        broken.write_bytes(b"%PDF-1.4\n" + b"garbage" * 5)

    print(f"[ok] 测试文件就绪：{FILES}（{len(list(FILES.iterdir()))} 个）")


def copy_data(keep: bool) -> None:
    if DST_DATA.exists() and keep:
        print(f"[skip] 保留已有副本：{DST_DATA}")
        return
    shutil.rmtree(DST_DATA, ignore_errors=True)
    DST_DATA.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SRC_DATA, DST_DATA, dirs_exist_ok=True)
    n = sum(1 for _ in DST_DATA.rglob("*") if _.is_file())
    print(f"[ok] 业务数据副本就绪：{DST_DATA}（{n} 个文件）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="已有副本则不重建")
    ap.add_argument(
        "--no-copy",
        action="store_true",
        help="只生成上传用测试文件，不复制 ./data",
    )
    args = ap.parse_args()
    make_files()
    if args.no_copy:
        # CI has no ./data at all (it is gitignored) and seeds its own data
        # with seed_demo.py; copying ./data there would both fail and
        # overwrite the freshly seeded demo rows.
        print("[skip] 未复制业务数据（--no-copy）")
    else:
        copy_data(args.keep)
    print("\n下一步：\n"
          "  scripts/e2e/serve_e2e.sh            # 起被测实例（:8010）\n"
          "  E2E_BASE_URL=http://127.0.0.1:8010 " + py_cmd("scripts/e2e_full_check.py"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
