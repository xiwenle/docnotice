"""M64 图片资料：5 种格式上传 + 预览 + 伪造拦截。

背景（2026-11-03 新增能力）：图片成为资料主文件类型。后端单测覆盖了
magic 校验与端点，但**浏览器里到底能不能显示**只有真实渲染才知道
（`<img>` 在模糊/损坏时不一定报错，要看 naturalWidth）。
"""
from __future__ import annotations

from harness import REPORT, case, http

# 2026-11-04：必须用**真实可解码**的图片字节。
# 只写文件头（FFD8FF..FFD9）能过服务端魔数校验，但浏览器 naturalWidth=0。
import base64
import struct
import zlib


def _png(w=8, h=8, color=(43, 93, 140)):
    raw = b"".join(b"\x00" + bytes(color) * w for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


IMAGES = {
    "png": _png(),
    "jpg": base64.b64decode(
        "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
        "Hh0aHBwcJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPDs0NDT/wAALCAABAAEBAREA/8QAFAAB"
        "AAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="),
    "gif": base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"),
    "webp": base64.b64decode("UklGRhoAAABXRUJQVlA4TA0AAAAvAAAAEAcQERGIiP4HAA=="),
}


@case("M01", "后端：5 种图片都能上传，预览端点返回正确 Content-Type")
def i01(app):
    """直接用 API 验证（UI 上传在 I02）。jpeg 与 jpg 同族，共用字节。"""
    for ext, data in list(IMAGES.items()) + [("jpeg", IMAGES["jpg"])]:
        # 建资料
        r = http("POST", "/api/v1/documents", app.admin.token, {
            "title": f"E2E图片-{ext}", "effective_from": "2026-01-01",
        })
        if r["status"] not in (200, 201):
            REPORT.fail(f"{ext}: 建资料失败 {r['status']}")
            continue
        did = r["body"]["id"]

        # 上传
        import urllib.request
        boundary = "----E2EBoundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="测试图.{ext}"\r\n'
            f"Content-Type: image/{'jpeg' if ext in ('jpg', 'jpeg') else ext}\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            http.__globals__["BASE_URL"] + f"/api/v1/documents/{did}/file",
            method="POST", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                     "Authorization": f"Bearer {app.admin.token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                up_ok = resp.status == 200
        except Exception as e:  # noqa: BLE001
            up_ok = False
            REPORT.note(f"{ext} 上传异常：{e}")

        REPORT.check(up_ok, f"{ext}: 上传成功")
        if not up_ok:
            continue

        # 预览端点：raw=True 时 body 是文本，不能当 dict 用
        pv = http("GET", f"/api/v1/documents/{did}/preview/image",
                  app.admin.token, raw=True)
        ok = pv["status"] == 200
        REPORT.check(ok, f"{ext}: preview/image 200（实际 {pv['status']}）")
        if ok:
            size = len(pv["body"]) if isinstance(pv["body"], str) else 0
            REPORT.note(f"{ext}: 预览返回 {size} 字符")


@case("M02", "浏览器：图片资料详情页真的渲染出图像")
def i02(app):
    """关键：naturalWidth > 0 才证明浏览器解码成功（不只看 HTTP 200）。"""
    s = app.admin
    # 种子里的 5 种图片资料
    targets = ["DEMO-安全通道示意图", "DEMO-组织架构图", "DEMO-会议室布局",
               "DEMO-报销操作动图", "DEMO-首页截图"]
    checked = 0
    for title in targets:
        body = http("GET", f"/api/v1/documents?keyword={title}&page_size=20",
                    app.admin.token)["body"]
        items = (body or {}).get("items") or [] if isinstance(body, dict) else []
        doc = next((d for d in items if d["title"] == title), None)
        if not doc:
            REPORT.note(f"跳过（未找到）：{title}")
            continue

        s.goto(f"/documents/{doc['id']}")
        try:
            s.page.wait_for_selector("img.image-preview", timeout=12000)
            info = s.page.evaluate(
                "() => {const i=document.querySelector('img.image-preview');"
                " return {w:i.naturalWidth, h:i.naturalHeight, src:i.src.slice(0,5)};}")
            ok = info["w"] > 0 and info["h"] > 0 and info["src"] == "blob:"
            REPORT.check(ok, f"{title}：图像渲染 {info['w']}×{info['h']}（blob 源）")
            checked += 1
        except Exception as e:  # noqa: BLE001
            REPORT.fail(f"{title}：图片未渲染（{str(e)[:50]}）")

    REPORT.check(checked >= 3, f"至少 3 种图片在浏览器中真实渲染（实际 {checked}）")
    s.shot("I02_image_preview.png")


@case("M03", "伪造图片扩展名被拦截（415）")
def i03(app):
    r = http("POST", "/api/v1/documents", app.admin.token, {
        "title": "E2E伪造图片", "effective_from": "2026-01-01",
    })
    did = r["body"]["id"]

    import urllib.request
    boundary = "----E2EBoundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="假图片.png"\r\n'
        "Content-Type: image/png\r\n\r\n"
    ).encode() + b"%PDF-1.4 this is not an image" + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        http.__globals__["BASE_URL"] + f"/api/v1/documents/{did}/file",
        method="POST", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "Authorization": f"Bearer {app.admin.token}"},
    )
    try:
        urllib.request.urlopen(req, timeout=30)
        REPORT.fail("伪造 .png（实际是 PDF 字节）不应上传成功")
    except urllib.error.HTTPError as e:
        REPORT.check(e.code == 415, f"伪造图片被拒 415（实际 {e.code}）")
    except Exception as e:  # noqa: BLE001
        REPORT.fail(f"异常：{e}")
