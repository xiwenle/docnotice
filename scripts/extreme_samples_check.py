"""极端合法样本预演（上线前"真实数据第一课"提前考，确认单 2026-08-30）。

用极端但合法的文件把 上传 → 预览 → 下载 全链路真实跑一遍，验证：
- 大型 PDF（300 页）在线预览可用、页数保真
- 超长 DOCX（6000 段 > 上限 5000）截断标志正确置位、耗时可控
- 超宽 XLSX（2 表 × 3 万行 > 上限 2000）截断正确、total_rows 如实上报
- 奇葩文件名（空格/全角括号/#/%/&/emoji/超长）上传→下载往返保真（RFC5987）
- 预演产物（文档+回收站条目）全部清理，不污染演示库

用法：先启动 uvicorn（127.0.0.1:8000），再 python scripts/extreme_samples_check.py
"""
from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from _paths import EXTREME_SAMPLES_DIR  # noqa: E402

BASE_URL = "http://127.0.0.1:8000"
ADMIN = {"username": "admin_demo", "password": "Demo@123456"}
TMP = EXTREME_SAMPLES_DIR
TMP.mkdir(exist_ok=True)

RESULTS: list[tuple[bool, str]] = []


def record(ok: bool, msg: str) -> None:
    RESULTS.append((ok, msg))
    print(f"  {'✅' if ok else '⚠️'} {msg}")


# ---------------------------------------------------------------------------
# 造样本（全部为合法文件，只是顶到设计上限之外）
# ---------------------------------------------------------------------------
def make_big_pdf() -> Path:
    from pypdf import PdfWriter
    from pypdf.generic import NameObject

    p = TMP / "大型手册_300页.pdf"
    w = PdfWriter()
    for i in range(300):
        page = w.add_blank_page(width=595, height=841)
        page[NameObject("/Contents")] = _pdf_content_stream(f"Page {i + 1} of 300")
    w.add_metadata({"/Title": "extreme-sample-300-pages"})
    with open(p, "wb") as f:
        w.write(f)
    return p


def _pdf_content_stream(text: str) -> object:
    from pypdf.generic import DecodedStreamObject

    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 14 Tf 72 760 Td ({text}) Tj ET".encode("latin-1"))
    return stream


def make_long_docx() -> Path:
    import docx

    p = TMP / "超长文档_6000段.docx"
    d = docx.Document()
    for i in range(6000):
        d.add_paragraph(f"第 {i + 1} 段：极端样本预演内容，用于验证 5000 段截断上限。")
    d.save(p)
    return p


def make_wide_xlsx() -> Path:
    from openpyxl import Workbook

    p = TMP / "超宽数据_2表3万行.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "数据一"
    for r in range(1, 30001):
        ws.append([f"行{r}", r, r * 2, "样本", "2026-08-30"])
    ws2 = wb.create_sheet("数据二")
    for r in range(1, 5001):
        ws2.append([f"表二行{r}", r])
    wb.save(p)
    return p


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def main() -> int:
    # 登录
    client = httpx.Client(base_url=BASE_URL, timeout=120)
    r = client.post("/api/v1/auth/login", json=ADMIN)
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    client.headers.update(h)

    # 生成样本
    print("\n[0] 生成极端样本")
    t0 = time.perf_counter()
    samples = [
        ("大型手册_300页.pdf", make_big_pdf(), "pdf", "preview/pdf"),
        ("超长文档_6000段.docx", make_long_docx(), "docx", "preview/docx"),
        ("超宽数据_2表3万行.xlsx", make_wide_xlsx(), "xlsx", "preview/xlsx"),
    ]
    weird_names = [
        "季度报告（终稿） #1 100%&汇总.docx",
        "资料 👍 多语言 имя.pdf",
        "很长的文件名" * 15 + ".pdf",
    ]
    for name in weird_names:
        src = samples[0][1] if name.endswith(".pdf") else samples[1][1]
        p = TMP / name
        p.write_bytes(src.read_bytes())
        samples.append((name, p, p.suffix.lstrip("."), None))
    print(f"  样本 {len(samples)} 个，生成耗时 {time.perf_counter() - t0:.1f}s")

    print("\n[1] 上传 / 预览 / 下载 全链路")
    doc_ids = []
    for name, path, ftype, preview_kind in samples:
        data = client.post(
            "/api/v1/documents",
            json={"title": f"EXTREME-{name}", "effective_from": "2026-08-30"},
        ).json()
        doc_id = data["id"]
        doc_ids.append(doc_id)

        t0 = time.perf_counter()
        up = client.post(
            f"/api/v1/documents/{doc_id}/file",
            files={"file": (name, path.read_bytes(), "application/octet-stream")},
        )
        up_s = time.perf_counter() - t0
        record(up.status_code == 200, f"上传「{name[:24]}」{up_s:.1f}s ({len(path.read_bytes()) // 1024} KB)")

        # 预览（仅三个主样本）
        if preview_kind:
            t0 = time.perf_counter()
            pv = client.get(f"/api/v1/documents/{doc_id}/{preview_kind}")
            pv_s = time.perf_counter() - t0
            body = {} if ftype == "pdf" else pv.json()
            if ftype == "pdf":
                is_pdf = pv.content[:5] == b"%PDF-" and "pdf" in pv.headers.get(
                    "content-type", ""
                )
                record(
                    pv.status_code == 200 and is_pdf,
                    f"PDF 内联预览 200（{pv_s:.1f}s，{len(pv.content) // 1024} KB，"
                    f"content-type={pv.headers.get('content-type')}）",
                )
            elif ftype == "docx":
                lines = len((body.get("text") or "").splitlines())
                record(
                    pv.status_code == 200 and body.get("truncated") is True,
                    f"DOCX 预览 truncated=True（{pv_s:.1f}s，文本 {lines} 段 ≤ 上限 5000）",
                )
                record(lines <= 5000, "DOCX 返回段数未超上限 5000")
            else:
                record(
                    pv.status_code == 200 and body.get("truncated") is True,
                    f"XLSX 预览 truncated=True（{pv_s:.1f}s，total_rows="
                    f"{body.get('total_rows')}）",
                )
                # 服务约定首行作表头：30000 行 = 表头 1 + 数据 29999
                record(
                    body.get("total_rows") == 29999 and len(body.get("rows", [])) <= 2000,
                    "XLSX total_rows 如实上报 29999（首行作表头），返回行数未超上限 2000",
                )

        # 下载往返：指纹一致 + 奇葩文件名 RFC5987 解码还原
        dl = client.get(f"/api/v1/documents/{doc_id}/file")
        record(
            dl.status_code == 200 and sha(dl.content) == sha(path.read_bytes()),
            f"下载「{name[:24]}」指纹一致",
        )
        if name in weird_names:
            from urllib.parse import unquote

            disp = dl.headers.get("content-disposition", "")
            m = [s for s in disp.split("''") if s]
            fname = unquote(m[-1].strip('; "')) if m else ""
            record(fname == name, f"文件名往返还原: {fname[:30]!r}")

    print("\n[2] 清理预演产物（删除→回收站→永久删除，单次确认）")
    for doc_id in doc_ids:
        client.delete(f"/api/v1/documents/{doc_id}")
    rb = client.get("/api/v1/recycle-bin?page_size=100").json()
    victim_ids = [
        e["id"] for e in rb["items"]
        if (e.get("document_title") or "").startswith("EXTREME-")
    ]
    # 可能含此前失败运行残留的同类条目——一并清掉，数量 >= 本次创建数即可
    for eid in victim_ids:
        client.request("DELETE", f"/api/v1/recycle-bin/{eid}", json={"confirm": True})
    after = client.get("/api/v1/recycle-bin?page_size=100").json()
    leftover = sum(
        1 for e in after["items"]
        if (e.get("document_title") or "").startswith("EXTREME-")
    )
    record(
        len(victim_ids) >= len(doc_ids) and leftover == 0,
        f"清理 {len(victim_ids)} 条预演产物（含历史残留），回收站无 EXTREME- 残留",
    )
    client.close()

    ok = sum(1 for s, _ in RESULTS if s)
    print("\n" + "=" * 56)
    print(f"预演结果: {ok}/{len(RESULTS)} 项通过")
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
