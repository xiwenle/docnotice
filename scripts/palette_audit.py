#!/usr/bin/env python3
"""配色落地后的真实页面走查（2026-09-29）。

`scripts/theme_check.py` 验的是**契约**（令牌值 / 外壳 / 组件 / 交互 / 列一致 / 零溢出）。
本脚本补它没覆盖的两件事：

  ① **逐页量对比度**：沿祖先链合成真实有效底色（含 rgba 与逐层 opacity），
     对每个可见文字节点算 WCAG 比值 —— 契约脚本只比"某个元素的色值等于期望"，
     不回答"页面上还有没有看不清的字"。
  ② **旧主题残留扫描**：把上一版调色板（素卷墨绿）与最初那版专业蓝的 hex 列成清单，
     在**真实渲染出的** computed style 里找 —— 源码扫过不代表 DOM 里没有
     （渐变/阴影/内联样式都可能带进来）。

用法（先起被测实例）：
    DATA_DIR=<临时目录>/fms_theme_data python -m uvicorn app.main:app --port 8000 &
    python scripts/palette_audit.py
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
ADMIN = {"username": "admin_demo", "password": "Demo@123456"}
NORMAL = {"username": "user_rnd_1", "password": "Demo@123456"}
SHOT = Path(__file__).resolve().parent.parent / "screenshots" / "palette"

PAGES = [
    "/", "/documents", "/notices",
    "/admin/documents/new", "/admin/notices/new", "/admin/categories",
    "/admin/tags", "/admin/notice-types", "/admin/recycle-bin",
    "/admin/users", "/admin/departments", "/admin/backups", "/admin/audit-logs",
]

# 上一版（素卷墨绿）与最初那版（专业蓝）的调色板，用于残留扫描
OLD_HEXES = {
    "22624d": "旧主色墨绿", "1a4d3d": "旧 primary-hover", "144034": "旧 primary-active",
    "e7efe9": "旧 primary-light", "f0f4ef": "旧 primary-lighter",
    "f6f4ef": "旧画布暖纸", "fffefa": "旧卡面暖白", "f1ede3": "旧 hover 底",
    "f3f0e8": "旧表头底", "e6e0d2": "旧边框", "d5cdba": "旧边框 strong",
    "29241c": "旧正文", "574f42": "旧次文", "6b6455": "旧辅助文", "5f594c": "旧 mut",
    "8b8171": "旧 sep", "867d6e": "旧 disabled-ink",
    "3d5a80": "旧 info 冷蓝", "eaeff5": "旧 info-light",
    "6b4a70": "旧 purple", "f2ecf2": "旧 purple-light",
    "2563eb": "最初那版专业蓝", "37,99,235": "最初专业蓝(rgba)",
    "41,36,28": "旧暖调阴影", "34,98,77": "旧绿光晕", "15,23,42": "旧蓝黑阴影",
}

CONTRAST_JS = r"""
() => {
  const lin = c => c <= 0.04045 ? c/12.92 : Math.pow((c+0.055)/1.055, 2.4);
  const lum = ([r,g,b]) => 0.2126*lin(r/255) + 0.7152*lin(g/255) + 0.0722*lin(b/255);
  const parse = s => {
    const m = s.match(/rgba?\(([^)]+)\)/); if (!m) return null;
    const p = m[1].split(/[,\s/]+/).filter(Boolean).map(Number);
    return {rgb:[p[0],p[1],p[2]], a: p.length > 3 ? p[3] : 1};
  };
  const effBg = el => {                       // 沿祖先链合成有效底色
    let node = el, acc = null;
    while (node) {
      const c = parse(getComputedStyle(node).backgroundColor);
      if (c && c.a > 0) {
        // 半透明**叠加**合成：上层 acc 压在下层 c 之上
        //   结果 = acc.rgb * acc.a + c.rgb * (1 - acc.a)
        // 2026-09-29 修：初版把 acc.a 误写成 c.a，导致
        // 「白字 + rgba(255,255,255,.25) 徽章 + 蓝底」被算成白底白字（比值 1.00）的假阳性。
        acc = acc === null ? c : {
          rgb: acc.rgb.map((v,i) => v*acc.a + c.rgb[i]*(1-acc.a)),
          a: acc.a + c.a*(1-acc.a)
        };
        if (acc.a >= 0.999) return acc.rgb;
      }
      node = node.parentElement;
    }
    return acc ? acc.rgb : [255,255,255];
  };
  const opac = el => {                        // 逐层 opacity 乘积
    let o = 1, n = el;
    while (n && n !== document.documentElement) { o *= parseFloat(getComputedStyle(n).opacity || 1); n = n.parentElement; }
    return o;
  };
  const ratio = (a,b) => { const l1=lum(a), l2=lum(b); return (Math.max(l1,l2)+0.05)/(Math.min(l1,l2)+0.05); };

  const out = [];
  document.querySelectorAll('body *').forEach(el => {
    if (!el.offsetParent && getComputedStyle(el).position !== 'fixed') return;
    const txt = Array.from(el.childNodes)
      .filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join('');
    if (!txt) return;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') return;
    const fg = parse(cs.color); if (!fg) return;
    const bg = effBg(el);
    const r = ratio(fg.rgb, bg);
    const size = parseFloat(cs.fontSize);
    const bold = (parseInt(cs.fontWeight, 10) || 400) >= 700;
    const large = size >= 24 || (size >= 18.66 && bold);
    // DESIGN.md 的档位：正文/表格/表单 ≥4.5；装饰性分隔符与禁用态文字 ≥3.0。
    // 分类判据用「语义」而不是「颜色像」——靠计算属性反查 :disabled 与 --sep 的实际值。
    const disabled = el.disabled === true || el.closest('[disabled]') !== null
                     || el.closest('.page-btn.is-disabled') !== null;
    const sepColor = getComputedStyle(document.documentElement).getPropertyValue('--sep').trim();
    const isSep = sepColor && cs.color ===
      `rgb(${parseInt(sepColor.slice(1,3),16)}, ${parseInt(sepColor.slice(3,5),16)}, ${parseInt(sepColor.slice(5,7),16)})`;
    const tier = disabled ? 'disabled' : (isSep ? 'decorative' : 'body');
    const need = (tier === 'body' && !large) ? 4.5 : 3.0;
    if (r < need) {
      out.push({text: txt.slice(0, 18), color: cs.color, size: Math.round(size),
                bold, ratio: Math.round(r*100)/100, need, tier});
    }
  });
  return out;
}
"""

RESIDUE_JS = r"""
(hexes) => {
  const found = {};
  const keys = hexes.map(h => h.toLowerCase());
  document.querySelectorAll('body *').forEach(el => {
    const cs = getComputedStyle(el);
    for (const prop of ['backgroundColor','color','borderTopColor','borderLeftColor',
                        'borderBottomColor','borderRightColor','outlineColor',
                        'boxShadow','backgroundImage','fill','stroke']) {
      const v = (cs[prop] || '').toLowerCase();
      if (!v || v === 'none') continue;
      for (const k of keys) {
        if (v.includes(k)) found[k] = (found[k] || 0) + 1;
      }
    }
  });
  return found;
}
"""


def login_cookie(username: str, password: str) -> str:
    req = urllib.request.Request(
        BASE + "/api/v1/auth/login",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.headers.get_all("Set-Cookie") or []
    return "; ".join(c.split(";")[0] for c in raw)


def main() -> int:
    SHOT.mkdir(parents=True, exist_ok=True)
    fails: list[str] = []
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True,
                              args=["--no-sandbox", "--disable-dev-shm-usage"])
        for role, creds, pages in (("admin", ADMIN, PAGES),
                                   ("user", NORMAL, ["/", "/documents", "/notices"])):
            cookie = login_cookie(**creds)
            ctx = b.new_context(viewport={"width": 1440, "height": 1000})
            ctx.add_cookies([{"name": n, "value": v, "url": BASE}
                             for n, v in (c.split("=", 1) for c in cookie.split("; "))])
            pg = ctx.new_page()
            errs = []
            pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            pg.on("pageerror", lambda e: errs.append(str(e)))
            print("\n===== [%s] =====" % role)
            for path in pages:
                pg.goto(BASE + path, wait_until="load")
                pg.wait_for_timeout(500)
                low = pg.evaluate(CONTRAST_JS)
                res = pg.evaluate(RESIDUE_JS, list(OLD_HEXES))
                ovf = pg.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
                tag = "%-26s" % path
                state = []
                if low:
                    state.append("对比度失败 %d" % len(low))
                    fails.append("%s 对比度 %s" % (path, low[:3]))
                if res:
                    state.append("旧色残留 %s" % {OLD_HEXES[k]: v for k, v in res.items()})
                    fails.append("%s 残留 %s" % (path, res))
                if ovf > 2:
                    state.append("横向溢出 %dpx" % ovf)
                    fails.append("%s 溢出 %d" % (path, ovf))
                print("  %s %s" % (tag, "OK" if not state else " | ".join(state)))
                if low:
                    for it in low[:4]:
                        print("        文字「%s」 %s / %dpx  比值 %.2f < %.1f  [%s 档]"
                              % (it["text"], it["color"], it["size"], it["ratio"],
                                 it["need"], it.get("tier", "?")))
                if path in ("/", "/documents", "/notices", "/admin/recycle-bin", "/admin/users"):
                    pg.screenshot(path=str(SHOT / ("%s%s.png" % (role, path.replace("/", "_")))))
            unexpected = [e for e in errs if "401" not in e and "403" not in e]
            print("  控制台 error：%s" % (unexpected if unexpected else "0（除预期 4xx）"))
            if unexpected:
                fails.append("[%s] 控制台 error %s" % (role, unexpected[:2]))
            ctx.close()
        b.close()

    print("\n" + "=" * 62)
    if fails:
        print("失败 %d 项：" % len(fails))
        for f in fails:
            print("  -", f)
        return 1
    print("全部通过：%d 个页面 × 2 角色，零对比度失败、零旧色残留、零横向溢出" % len(PAGES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
