# -*- coding: utf-8 -*-
"""生成《项目全景》drawio（7 页）。手写 XML，无外部依赖。

用法：python scripts/gen_project_diagram.py

设计约束（经验教训）：
- XML 属性值必须完整转义（含 < > " &），否则文件不是合法 XML；
- 跨区块的长连线会穿过其他方块、标签互相压叠 —— 区块之间用左侧竖向流程箭头
  表示层级流向，不画跨区连线；
- 实体关系图用固定栅格排布 + 显式出入口，避免连线穿越表格。
"""
import io
import os


def esc(s: str) -> str:
    """XML 属性值转义。`<br>` 也须转义成 `&lt;br&gt;`——drawio 反转义后按 HTML 渲染。"""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


class Page:
    def __init__(self, pid, name, w, h):
        self.pid, self.name, self.w, self.h = pid, name, w, h
        self.cells, self._n = [], 0

    def nid(self, p="c"):
        self._n += 1
        return f"{self.pid}-{p}{self._n}"

    def box(self, label, x, y, w, h, style=""):
        cid = self.nid()
        st = (
            "rounded=1;whiteSpace=wrap;html=1;arcSize=8;fontSize=12;"
            "fillColor=#ffffff;strokeColor=#8aa4bb;fontColor=#1f2d3a;" + style
        )
        self.cells.append(
            f'<mxCell id="{cid}" value="{esc(label)}" style="{st}" vertex="1" parent="1">'
            f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
        )
        return cid

    def band(self, label, x, y, w, h, color="#eef2f6", stroke="#a9bccd", fs=13):
        cid = self.nid("band")
        st = (
            f"rounded=0;whiteSpace=wrap;html=1;fillColor={color};strokeColor={stroke};"
            f"verticalAlign=top;align=left;spacingLeft=12;spacingTop=6;fontSize={fs};"
            "fontStyle=1;fontColor=#33475b;"
        )
        self.cells.append(
            f'<mxCell id="{cid}" value="{esc(label)}" style="{st}" vertex="1" parent="1">'
            f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
        )
        return cid

    def note(self, label, x, y, w, h, color="#fff8e6", stroke="#d9b45a", fs=11):
        cid = self.nid("note")
        st = (
            f"shape=note;whiteSpace=wrap;html=1;fillColor={color};strokeColor={stroke};"
            f"fontSize={fs};align=left;spacingLeft=6;"
        )
        self.cells.append(
            f'<mxCell id="{cid}" value="{esc(label)}" style="{st}" vertex="1" parent="1">'
            f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
        )
        return cid

    def title(self, label, sub=""):
        cid = self.nid("t")
        txt = f"<b>{label}</b>"
        if sub:
            txt += f"<br><font style='font-size:11px;color:#5d7083'>{sub}</font>"
        st = "text;html=1;align=left;verticalAlign=top;fontSize=20;fontColor=#1b2a38;spacingLeft=2;"
        self.cells.append(
            f'<mxCell id="{cid}" value="{esc(txt)}" style="{st}" vertex="1" parent="1">'
            f'<mxGeometry x="30" y="14" width="1200" height="52" as="geometry"/></mxCell>'
        )
        return cid

    def edge(
        self, src, dst, label="", dashed=False, color="#5d7083", arrow=True,
        width=1.5, exit_xy=None, entry_xy=None,
    ):
        """连线。exit_xy / entry_xy 形如 (0.5, 1)，控制起止锚点，避免穿框。"""
        cid = self.nid("e")
        st = (
            f"edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;strokeColor={color};"
            f"strokeWidth={width};endArrow={'block' if arrow else 'none'};endFill=1;"
            "fontSize=10;fontColor=#41556a;labelBackgroundColor=#ffffff;"
        )
        if dashed:
            st += "dashed=1;"
        if exit_xy:
            st += f"exitX={exit_xy[0]};exitY={exit_xy[1]};exitDx=0;exitDy=0;"
        if entry_xy:
            st += f"entryX={entry_xy[0]};entryY={entry_xy[1]};entryDx=0;entryDy=0;"
        self.cells.append(
            f'<mxCell id="{cid}" value="{esc(label)}" style="{st}" edge="1" parent="1" '
            f'source="{src}" target="{dst}"><mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        return cid

    def vflow(self, x, y1, y2, label=""):
        """左侧竖向层级流向箭头（区块之间用，避免跨区连线）。"""
        cid = self.nid("v")
        st = (
            "edgeStyle=none;html=1;strokeColor=#7f97ad;strokeWidth=2;"
            "endArrow=block;endFill=1;fontSize=10;fontColor=#5d7083;"
            "labelBackgroundColor=#ffffff;"
        )
        self.cells.append(
            f'<mxCell id="{cid}" value="{esc(label)}" style="{st}" edge="1" parent="1">'
            f'<mxGeometry relative="1" as="geometry">'
            f'<mxPoint x="{x}" y="{y1}" as="sourcePoint"/>'
            f'<mxPoint x="{x}" y="{y2}" as="targetPoint"/></mxGeometry></mxCell>'
        )
        return cid

    def xml(self):
        body = "\n".join(self.cells)
        return (
            f'  <diagram id="{self.pid}" name="{esc(self.name)}">\n'
            f'    <mxGraphModel dx="1400" dy="900" grid="1" gridSize="10" guides="1" '
            f'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
            f'pageWidth="{self.w}" pageHeight="{self.h}" math="0" shadow="0">\n'
            f'      <root>\n        <mxCell id="0"/>\n        <mxCell id="1" parent="0"/>\n'
            f'        {body}\n      </root>\n    </mxGraphModel>\n  </diagram>'
        )


PRIMARY = "fillColor=#2b5d8c;strokeColor=#1f4568;fontColor=#ffffff;fontStyle=1;"
PRIMARY_L = "fillColor=#dce8f2;strokeColor=#6f93b5;fontColor=#1f3a55;"
ACCENT = "fillColor=#e8f1e6;strokeColor=#6b9a68;fontColor=#2c4a2a;"
WARN = "fillColor=#fdf0dc;strokeColor=#d2983f;fontColor=#7a4d10;"
DANGER = "fillColor=#fbe4e4;strokeColor=#c96a6a;fontColor=#8a2626;"
NEUTRAL = "fillColor=#eef1f4;strokeColor=#9aa9b8;fontColor=#31414f;"
POINT = "fillColor=#fff3c4;strokeColor=#c9a227;fontColor=#6b5200;fontStyle=1;"
DATA = "fillColor=#dfe9f3;strokeColor=#6f93b5;fontColor=#1f3a55;"

pages = []


# =====================================================================
# 1 系统总览
# =====================================================================
p = Page("p1", "01 系统总览·分层架构", 1900, 1360)
p.title(
    "作业资料系统 · 分层架构与接入点",
    "FastAPI + SQLite + Jinja2 单进程单体；业务数据与程序代码分离（data/）",
)

p.band("接入层（外部边界）", 100, 78, 1770, 150)
p.box("员工浏览器<br>查看资料 · 在线预览<br>阅读通知 · 下载", 130, 118, 320, 88, PRIMARY_L)
p.box("管理员浏览器<br>资料/通知/分类/标签<br>用户/部门/备份/日志", 480, 118, 320, 88, PRIMARY_L)
p.box("内网防火墙 + IP 白名单<br>netsh / ufw / firewalld<br>仅放行指定 IP 或网段", 830, 118, 330, 88, WARN)
p.box("反向代理（可选）<br>Nginx / Caddy + HTTPS<br>应用监听 127.0.0.1:8000", 1190, 118, 330, 88, NEUTRAL)
p.box("① HTTP 接入点<br>唯一对外入口", 1550, 118, 290, 88, POINT)

p.band("中间件链（按注册顺序执行）", 100, 268, 1770, 140)
p.box("SecurityMiddleware<br>CSRF Origin 校验 · CSP(nonce)<br>frame-src blob:（PDF/图片预览）<br>nosniff / X-Frame-Options · 限速", 130, 310, 340, 80, ACCENT)
p.box("统一异常处理器<br>AppError → 错误契约<br>500 不回显堆栈", 830, 310, 300, 80, NEUTRAL)
p.box("静态资源 /static<br>仅 app/web/static<br>data/ 绝不静态暴露", 1150, 310, 300, 80, NEUTRAL)

p.band("路由层（app/routers，96 个端点）", 100, 448, 1770, 130)
p.box("pages.py<br>Jinja2 页面<br>/login /admin/*", 130, 490, 220, 70, PRIMARY_L)
p.box("auth.py<br>登录/登出/me/改密", 370, 490, 200, 70, PRIMARY_L)
p.box("documents.py<br>dashboard.py", 590, 490, 200, 70, PRIMARY_L)
p.box("notices.py<br>notice_types.py", 810, 490, 210, 70, PRIMARY_L)
p.box("users / departments<br>categories / tags", 1040, 490, 230, 70, PRIMARY_L)
p.box("recycle_bin<br>backups / audits", 1290, 490, 200, 70, PRIMARY_L)
p.box("预览与下载<br>/file · /preview/*", 1510, 490, 220, 70, NEUTRAL)

p.band("服务层（app/services，20 个模块按域划分）", 100, 618, 1770, 200)
p.box("<b>认证与用户域</b><br>auth_service<br>user_service<br>department_service", 130, 662, 320, 130, ACCENT)
p.box(
    "<b>资料与文件域</b><br>document_service · file_service<br>category_service · tag_service<br>"
    "preview_service · validity_service",
    480, 662, 350, 130, ACCENT,
)
p.box(
    "<b>通知域（7 模块拆分）</b><br>notice_service · notice_read<br>notice_recycle · notice_linkage<br>"
    "notice_audience · notice_common<br>notice_serializer · notice_type_service",
    860, 662, 400, 130, ACCENT,
)
p.box("<b>运维支撑域</b><br>backup_service<br>recycle_service<br>audit_service", 1290, 662, 300, 130, ACCENT)
p.box("<b>跨域约束</b><br>恢复互斥锁<br>备份/恢复互斥<br>列表缓存失效", 1620, 662, 220, 130, WARN)

p.band("基础设施（app/core）", 100, 858, 1770, 180)
for lbl, x, w in [
    ("config.py<br>Settings · ensure_dirs<br>app_env 归一化", 130, 235),
    ("deps.py<br>get_current_user<br>require_roles", 390, 215),
    ("security.py<br>bcrypt · JWT", 630, 200),
    ("file_safety.py<br>扩展名/魔数/MIME<br>safe_join · 随机名", 855, 235),
    ("security_middleware<br>CSRF · CSP nonce<br>安全响应头", 1115, 235),
    ("cache.py<br>进程内 TTL<br>(多 worker 失效)", 1375, 220),
    ("scheduler.py<br>disk_health.py<br>errors.py", 1620, 220),
]:
    p.box(lbl, x, 900, w, 118, NEUTRAL)

p.band("数据层（AGENTS §7：程序与数据分离，路径全部可配置）", 100, 1078, 1770, 200)
p.box("SQLite<br>app.db<br>17 张表", 130, 1120, 220, 130, DATA)
p.box("data/documents/<br>资料当前文件<br>(随机内部名)", 380, 1120, 240, 130, NEUTRAL)
p.box("data/notices/<br>通知附件", 650, 1120, 210, 130, NEUTRAL)
p.box("data/recycle_bin/<br>旧文件不物理删除", 890, 1120, 250, 130, NEUTRAL)
p.box("data/backups/<br>backup_&lt;ts&gt;/<br>app.db + files + manifest", 1170, 1120, 300, 130, NEUTRAL)
p.box("data/uploads/<br>预留目录", 1500, 1120, 190, 130, NEUTRAL)
p.box("③ 文件系统接入点<br>④ SQLite 接入点", 1710, 1120, 130, 130, POINT)

for y1, y2 in [(228, 268), (408, 448), (578, 618), (818, 858), (1038, 1078)]:
    p.vflow(70, y1, y2)

p.note(
    "<b>层级流向</b>：接入层 → 中间件 → 路由 → 服务 → 数据。<br>"
    "左侧竖箭头表示依赖方向，跨层不画连线以免穿越方块。",
    40, 1290, 760, 60, "#eef2f6", "#8aa4bb",
)
p.note(
    "<b>外部接入点（详见第 7 页）</b><br>"
    "① HTTP 唯一入口　② 文件系统<br>"
    "④ SQLite 单文件库　⑤ 内网 pip 源　⑥ 进程常驻",
    820, 1290, 1050, 60, "#fff8e6", "#d9b45a",
)
pages.append(p)


# =====================================================================
# 2 认证与权限
# =====================================================================
p = Page("p2", "02 认证与权限", 1750, 1180)
p.title("认证与权限流程", "本地账号 + JWT；每请求回库复核")

p.band("登录（POST /api/v1/auth/login）", 40, 80, 1670, 240)
l1 = p.box("提交<br>username + password", 70, 122, 220, 80, PRIMARY_L)
l2 = p.box("auth_service.login<br>① 查用户<br>② 限速检查", 320, 122, 260, 80, NEUTRAL)
l3 = p.box("bcrypt 校验<br>用户不存在时不跑哈希<br>(时间侧信道 · 已知 P2)", 610, 122, 270, 80, WARN)
l4 = p.box("签发 JWT<br>{user_id, role, ver}<br>有效期 720 分钟", 910, 122, 250, 80, ACCENT)
l5 = p.box("设置 Cookie<br>HttpOnly · SameSite=Lax<br>Secure 按 APP_ENV", 1190, 122, 260, 80, ACCENT)
p.box("响应体也返回 access_token<br>(纵深防御面 · 已知 P2)", 1480, 122, 210, 80, WARN)
p.box("失败 401 / 429<br>审计 login_failed", 610, 226, 270, 60, DANGER)
p.box("成功：审计 login<br>记录 ip / user_agent", 910, 226, 250, 60, NEUTRAL)

p.band("每请求鉴权（app/core/deps.py）", 40, 340, 1670, 330)
g1 = p.box("Cookie 或<br>Authorization: Bearer", 70, 382, 240, 70, PRIMARY_L)
g2 = p.box("① decode_access_token<br>签名 / 过期", 340, 382, 250, 70, NEUTRAL)
g3 = p.box("② db.get(User)<br>auth_source=='local'", 620, 382, 270, 70, NEUTRAL)
g4 = p.box("③ is_active<br>停用立即失效", 920, 382, 240, 70, NEUTRAL)
g5 = p.box("④ role 复核<br>令牌 ≠ 库 → 401", 1190, 382, 250, 70, NEUTRAL)
g6 = p.box("⑤ token_version 复核<br>ver ≠ 库值 → 401<br>(新登录/改密顶掉旧设备)", 620, 490, 270, 76, NEUTRAL)
g7 = p.box("⑥ must_change_password 闸门<br><b>计划移除，见页底</b>", 920, 490, 240, 76, WARN)
g8 = p.box("⑦ 放行<br>current_user 注入依赖", 1190, 490, 250, 76, ACCENT)
p.box("拒绝：401 / 403<br>页面路由吞异常 → 匿名态", 70, 490, 240, 76, DANGER)
p.box('require_roles("admin")<br>普通用户调用管理端点 → 403', 340, 490, 250, 76, WARN)

p.band("角色与边界（权限模型 v3：两角色）", 40, 700, 1670, 180)
p.box(
    "<b>user 普通员工</b><br>查看/搜索资料 · 在线预览<br>下载（受 allow_download）<br>查看/阅读自己的通知",
    70, 742, 350, 120, PRIMARY_L,
)
p.box(
    "<b>admin 管理员</b><br>资料/通知/分类/标签/类型<br>用户/部门 · 回收站<br>备份恢复 · 阅读统计 · 操作日志",
    450, 742, 350, 120, PRIMARY,
)
p.note(
    "权限必须后端校验（AGENTS §33）<br>前端隐藏按钮只是体验，不承担权限。<br>"
    "所有管理端点由<br>Depends(require_roles(\"admin\")) 强制。",
    830, 742, 400, 120,
)
p.box(
    "登出 POST /auth/logout<br>清 Cookie + 审计<br><b>不 bump token_version</b><br>已知 P2：令牌 12h 内仍可用",
    1260, 742, 420, 120, DANGER,
)

p.note(
    "<b>⑥ must_change_password 闸门（待移除）</b><br>"
    "现状：标记为真的用户，除 /auth/me、/auth/change-password、/auth/logout 外，<br>"
    "其余 /api/ 一律 403 PASSWORD_CHANGE_REQUIRED。<br>"
    
    "涉及面：deps.py 闸门 · pages.py 跳转 · base.html meta refresh ·<br>"
    "change_password 页面 · auth_service 改密逻辑 · 相关测试 6 处",
    70, 910, 800, 190, "#fff3e0", "#d2983f",
)
p.note(
    "<b>为什么保留而非立即删除</b><br>"
    "本地登录仍在用。当前若删掉闸门，初始管理员密码未改就会被<br>"
    "长期使用（首次引导密码写在 .env 里）。<br>"
    "待未来改用外部身份认证后，再一并移除。",
    900, 910, 800, 190, "#eef2f6", "#8aa4bb",
)

p.edge(l1, l2)
p.edge(l2, l3)
p.edge(l3, l4)
p.edge(l4, l5)
p.edge(g1, g2)
p.edge(g2, g3)
p.edge(g3, g4)
p.edge(g4, g5)
p.edge(g5, g6, exit_xy=(0.5, 1), entry_xy=(0.75, 0))
p.edge(g6, g7)
p.edge(g7, g8)
pages.append(p)


# =====================================================================
# 3 资料全生命周期
# =====================================================================
p = Page("p3", "03 资料全生命周期", 1800, 1300)
p.title("资料全生命周期", "创建 → 上传 → 时效计算 → 替换/归档 → 回收站 → 恢复/彻底删除")

p.band("① 创建资料（POST /documents）", 40, 80, 1720, 150)
c1 = p.box("管理员填表<br>标题 · 编号 · 摘要<br>分类 · 标签 · 生效区间", 70, 118, 300, 92, PRIMARY_L)
c2 = p.box("R1 校验<br>effective_from &gt; effective_to<br>→ 409 VALIDITY_CONFLICT", 400, 118, 310, 92, WARN)
c3 = p.box("R2 校验<br>与已有区间重叠 → 409<br>需 confirm_overwrite + 原因", 740, 118, 320, 92, WARN)
c4 = p.box("写入 documents<br>current_file_id = NULL<br>稍后可上传文件", 1090, 118, 290, 92, ACCENT)
p.box("审计<br>create_document", 1410, 118, 310, 92, NEUTRAL)

p.band("② 上传/替换文件（file_service）", 40, 250, 1720, 240)
u1 = p.box("上传<br>PDF/DOCX/XLSX/XLS", 70, 292, 230, 80, PRIMARY_L)
u2 = p.box(
    "<b>文件安全 6 步</b><br>① 扩展名白名单<br>② 魔数/真实解析<br>③ 服务端 MIME 派生<br>"
    "④ 大小上限(边读边计)<br>⑤ 文件名清洗<br>⑥ safe_join 防穿越",
    330, 286, 300, 172, ACCENT,
)
u3 = p.box("save_upload<br>uuid4 随机内部名<br>分片 documents/xx/", 660, 292, 280, 80, NEUTRAL)
u4 = p.box("替换旧文件？<br>→ recycle_file<br>reason='replaced'", 970, 292, 260, 80, WARN)
u5 = p.box("新 file_record<br>current_file_id 更新<br>rev_count++", 1260, 292, 270, 80, ACCENT)
p.box("失败补偿<br>两阶段原子<br>DB 回滚 + 物理补偿", 970, 392, 260, 66, DANGER)
p.box("审计 replace_file", 1260, 392, 270, 66, NEUTRAL)

p.band("③ 时效状态（自动计算，不落库）", 40, 510, 1720, 200)
p.box("as_of &lt; effective_from<br>→ <b>待生效</b>", 70, 552, 250, 70, NEUTRAL)
p.box("from ≤ as_of ≤ to<br>或 to 为空<br>→ <b>有效</b>", 350, 552, 300, 70, ACCENT)
p.box("as_of &gt; effective_to<br>→ <b>已失效</b>", 680, 552, 250, 70, WARN)
p.box("archived_at 非空<br>→ <b>已归档</b><br>(优先级最高)", 960, 552, 250, 70, DANGER)
p.box(
    "<b>闭区间</b>：to 当天仍有效<br><b>空值</b>：暂无失效日期(不表示永久有效)<br>"
    "<b>历史查询</b>：?as_of_date= 重新计算",
    1240, 552, 480, 70, PRIMARY_L,
)
p.box("管理员不能手工填状态<br>无 status 列，只能改日期区间", 350, 636, 300, 56, WARN)
p.box("列表 SQL 与 compute_status<br>逐条等价（防口径漂移）", 680, 636, 340, 56, NEUTRAL)

p.band("④ 归档 / 恢复 / 彻底删除", 40, 730, 1720, 250)
a1 = p.box("DELETE /documents/{id}<br>archive_document<br>archived_at + reason", 70, 772, 290, 80, WARN)
a2 = p.box("当前文件 → 回收站<br>reason='archived'<br>无文件也留档", 390, 772, 290, 80, NEUTRAL)
a3 = p.box("POST /{id}/restore<br>清 archived_at<br>文件回 documents/", 710, 772, 290, 80, ACCENT)
a4 = p.box("回收站<br>restore / purge(confirm)<br>purge-all(confirm)", 1030, 772, 300, 80, PRIMARY_L)
a5 = p.box("物理删除<br>仅 recycle purge 入口<br>唯一允许删文件处", 1360, 772, 300, 80, DANGER)
p.box("普通用户不可见归档资料<br>列表/详情/下载/预览均 403", 70, 880, 290, 60, NEUTRAL)
p.box("旧文件绝不覆盖<br>禁止直接物理删除（§26）", 390, 880, 290, 60, NEUTRAL)
p.box("归档资料禁止编辑<br>（通知联动除外，见第 4 页）", 710, 880, 290, 60, WARN)

p.note(
    "<b>数据安全红线（§8/§26）</b><br>"
    "· 一个资料只有一份当前文件；替换时旧文件进回收站<br>"
    "· 禁止直接覆盖旧文件；禁止直接物理删除业务文件<br>"
    "· 归档是软删除，回收站可恢复<br>"
    "· 永久删除必须 confirm=true（单条/批量一致）<br>"
    "· allow_download 只关下载入口，不关预览（使用约定，非安全边界）",
    70, 1010, 800, 180, "#f0f6ec", "#6b9a68",
)
p.note(
    "<b>分类约束（§9）</b><br>层级上限 4 级（MAX_DEPTH）<br>"
    "移动分类时校验<b>整棵子树</b>移动后深度<br>（2026-10-29 修复：此前可构造 7 级）<br>"
    "同节点成环、挂到子孙下 → 400",
    900, 1010, 820, 180, "#eef2f6", "#8aa4bb",
)

p.edge(c1, c2)
p.edge(c2, c3)
p.edge(c3, c4)
p.edge(u1, u2)
p.edge(u2, u3)
p.edge(u3, u4)
p.edge(u4, u5)
p.edge(a1, a2)
p.edge(a2, a3)
p.edge(a3, a4)
p.edge(a4, a5)
pages.append(p)


# =====================================================================
# 4 通知
# =====================================================================
p = Page("p4", "04 通知生命周期与联动", 1820, 1300)
p.title("通知全生命周期与资料联动", "草稿 → 发布 → 阅读；废止/替换类型触发资料时效截断")

p.band("① 状态机", 40, 80, 1740, 180)
n1 = p.box("<b>draft 草稿</b><br>自由修改<br>可删除", 90, 126, 230, 100, NEUTRAL)
n2 = p.box("<b>published</b><br>物化阅读快照<br>联动生效", 480, 126, 230, 100, ACCENT)
n3 = p.box("<b>cancelled 已取消</b><br>联动回滚<br>可恢复/彻底删", 870, 126, 230, 100, WARN)
p.box("通知修改限制<br>有人已读后须<br>confirm_modify=true<br>+ modified_reason", 1260, 126, 280, 100, DANGER)
p.box("禁止改阅读对象<br>(防绕过既有快照)", 1580, 126, 190, 100, DANGER)

p.band("② 发布（POST /notices/{id}/publish）", 40, 290, 1740, 220)
pb1 = p.box("校验<br>confirm_targets=true<br>阅读对象非空", 70, 332, 260, 76, PRIMARY_L)
pb2 = p.box("status → published<br>published_at = now", 360, 332, 250, 76, NEUTRAL)
pb3 = p.box("物化阅读快照<br>notice_reads<br>(每个应读人一行)", 640, 332, 260, 76, ACCENT)
pb4 = p.box("reconcile_auto_expire_linkage<br>废止/替换 → effective_to<br>= 生效日(含已归档)", 930, 332, 350, 76, WARN)
pb5 = p.box("冲突 → 整体回滚<br>R1 违规则发布失败<br>(不允许半发布)", 1310, 332, 300, 76, DANGER)
p.box(
    "同一幂等实现被 4 处共用：publish / update / cancel / restore<br>"
    "防止「写入方有三个字段、重算只有一个入口」的口径漂移",
    640, 428, 670, 62, PRIMARY_L,
)
p.box("审计 publish_notice", 1340, 428, 270, 62, NEUTRAL)

p.band("③ 阅读（§20/§21）", 40, 530, 1740, 200)
r1 = p.box("打开通知详情<br>→ 写 read_at", 70, 572, 250, 76, PRIMARY_L)
r2 = p.box("<b>通知确认正式下线</b>（2026-11-08 用户确认）<br>只保留「未查看 / 已查看」<br>confirmed_at 列已删除", 360, 572, 340, 76, NEUTRAL)
r3 = p.box("两态<br>未查看 / 已查看", 740, 572, 260, 76, NEUTRAL)
r4 = p.box("阅读统计<br>分母 = 快照 ∩ 启用用户<br><b>不含管理员</b>", 1040, 572, 320, 76, NEUTRAL)
r5 = p.box("阅读对象三种范围<br>全体 / 指定部门 / 指定用户", 1400, 572, 340, 76, PRIMARY_L)
p.box("截止时间仅作展示<br>不影响查看", 740, 660, 260, 54, NEUTRAL)
p.box("纯 API 调用不自动置读<br>置读由详情页前端触发", 1040, 660, 320, 54, WARN)

p.band("④ 取消 / 恢复 / 删除", 40, 750, 1740, 190)
x1 = p.box("取消 cancel<br>联动回滚(按账本)<br>恢复管理员独立改的日期", 70, 792, 330, 90, ACCENT)
x2 = p.box("恢复 restore<br>废止/替换重新应用", 440, 792, 280, 90, ACCENT)
x3 = p.box("草稿删除<br>直接删", 760, 792, 230, 90, NEUTRAL)
x4 = p.box("彻底删除(confirm)<br>物理删通知<br>阅读快照随删", 1030, 792, 300, 90, DANGER)
p.box("通知类型词表<br>管理员可增删<br>内置：发布/修订/替换/废止/延期/其他", 1370, 792, 370, 90, PRIMARY_L)

p.note(
    "<b>联动语义边界（已知问题，待修）</b><br>"
    "· 解除关联时账本条目被无条件 pop，再关联会被当「首次」重新截断<br>"
    "  （与「墓碑永久不干预」的注释矛盾）<br>"
    "· skip_archived=False 会修改已归档资料有效期<br>"
    "· 内置联动类型（废止/替换）改名一律 409",
    70, 970, 820, 170, "#fdf0dc", "#d2983f",
)
p.note(
    "<b>阅读统计口径</b><br>"
    "应读 = 发布时快照 ∩ users.is_active ∩ role≠admin<br>"
    "已查看 = read_at 非空，未查看 = 应读 − 已查看<br>"
    "确认维度已于 2026-11-03 整体下线（只剩两态）",
    920, 970, 820, 170, "#eef2f6", "#8aa4bb",
)

p.edge(n1, n2, "发布")
p.edge(n2, n3, "取消")
p.edge(n3, n2, "恢复", dashed=True, exit_xy=(0.5, 0), entry_xy=(0.5, 0))
p.edge(pb1, pb2)
p.edge(pb2, pb3)
p.edge(pb3, pb4)
p.edge(pb4, pb5)
p.edge(r1, r2)
p.edge(r2, r3)
p.edge(r3, r4)
p.edge(x1, x2)
p.edge(x2, x3)
p.edge(x3, x4)
pages.append(p)


# =====================================================================
# 5 数据模型
# =====================================================================
p = Page("p5", "05 数据模型·17 张表", 1720, 1380)
p.title("数据模型 · 17 张表", "SQLite 单文件；数据库只存元数据与关系，文件二进制在文件系统")

TW = 330      # 表格统一宽度
TH = 150      # 表格统一高度
GAPX = 400    # 列间距
GAPY = 190    # 行间距
C0, R0 = 90, 110


def tbl(label, col, row, style=""):
    return p.box(
        label, C0 + col * GAPX, R0 + row * GAPY, TW, TH,
        "shape=table;whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor=#7f97ad;"
        "fontSize=10;align=left;verticalAlign=top;spacingLeft=10;spacingTop=8;" + style,
    )


def fk(cols: str) -> str:
    """把外键列标成蓝色，直观表达「这列指向谁」。"""
    return f"<font color='#2b5d8c'>{cols}</font>"


# 布局原则（2026-10-29 重做，2026-11-04 随建表变更同步）：17 张表按四行栅格排布，**表内标注外键**，
# 只在真正需要表达 M:N 的地方画少量连线——十几条交叉连线比没有连线更难读。
# 第 1 行：核心实体
tbl(
    "<b>users</b><br>id · username · password_hash<br>display_name · role · is_active<br>"
    "must_change_password<br>auth_source · external_uid<br>token_version · last_login_at",
    0, 0, PRIMARY_L,
)
tbl(
    f"<b>documents</b><br>id · title · summary<br>{fk('category_id → categories')}<br>"
    f"effective_from · effective_to<br>{fk('current_file_id → file_records')}<br>"
    "rev_count · archived_at · created_by<br>allow_download · audience_scope",
    1, 0, PRIMARY_L,
)
tbl(
    f"<b>notices</b><br>id · title · content<br>{fk('file_id → file_records')}<br>"
    f"{fk('notice_type → notice_types')}（可空）<br>status · published_at · effective_at<br>"
    "created_by · modified_reason",
    2, 0, PRIMARY_L,
)
tbl(
    f"<b>file_records</b><br>id · owner_type · owner_id<br>original_filename · internal_name<br>"
    "file_type · mime_type · size_bytes<br>sha256 · storage_rel_path · status<br>"
    f"{fk('uploaded_by → users')}",
    3, 0, PRIMARY_L,
)

# 第 2 行：分类、标签、通知类型
tbl(
    f"<b>categories</b><br>id · name<br>{fk('parent_id → categories')}（自关联树）<br>"
    "sort_order · is_active",
    0, 1, ACCENT,
)
tbl("<b>tags</b><br>id · name<br>（辅助搜索与筛选）", 1, 1, ACCENT)
tbl(
    "<b>notice_types</b><br>id · name<br>（管理员可增删的词表）<br>"
    "内置：发布/修订/替换/废止/延期/其他",
    2, 1, ACCENT,
)
tbl(
    "<b>departments</b><br>id · name · description<br>is_active<br>"
    "（仍有启用成员时不可删）",
    3, 1, ACCENT,
)

# 第 3 行：关联表（M:N）
tbl(
    f"<b>user_departments</b><br>{fk('user_id → users')}<br>"
    f"{fk('department_id → departments')}<br>（用户 ↔ 部门 M:N）",
    0, 2, WARN,
)
tbl(
    f"<b>document_tags</b><br>{fk('document_id → documents')}<br>{fk('tag_id → tags')}<br>"
    "（资料 ↔ 标签 M:N）",
    1, 2, WARN,
)
tbl(
    f"<b>notice_documents</b><br>{fk('notice_id → notices')}<br>{fk('document_id → documents')}<br>"
    "（通知 ↔ 资料 M:N）",
    2, 2, WARN,
)
tbl(
    f"<b>notice_targets</b><br>{fk('notice_id → notices')}<br>{fk('department_id → departments')}<br>"
    f"{fk('user_id → users')}<br>target_type：全体/部门/用户",
    3, 2, WARN,
)
# 第 3 行补位：资料可见范围（2026-11-03 新增，与 notice_targets 同构）
tbl(
    f"<b>document_targets</b><br>{fk('document_id → documents')}<br>"
    f"{fk('department_id → departments')}<br>{fk('user_id → users')}<br>"
    "target_type：全体/部门/用户",
    0, 3, WARN,
)

# 第 4 行：阅读状态与运维
tbl(
    f"<b>notice_reads</b><br>{fk('notice_id → notices')}<br>{fk('user_id → users')}<br>"
    "read_at · read_ip<br>（两态：未查看/已查看；管理员不入快照）",
    1, 3, PRIMARY_L,
)
tbl(
    f"<b>recycle_bin</b><br>{fk('document_id → documents')}<br>"
    f"{fk('file_record_id → file_records')}<br>reason：replaced / archived<br>origin_rel_path<br>"
    "deleted_by / restored_by / purged_by",
    2, 3, DANGER,
)
tbl(
    f"<b>backup_records</b><br>id · backup_type · backup_path<br>manifest_path · size_bytes<br>"
    f"db_sha256 · file_count<br>status · source<br>{fk('created_by / restored_by → users')}",
    3, 3, PRIMARY_L,
)
# audit_logs 移到第 5 行开头，避免与 backup_records 撞位
tbl(
    f"<b>audit_logs</b><br>{fk('user_id → users（删用户置 NULL）')}<br>username（文本保留）<br>"
    "action · object_type · object_id<br>detail(JSON) · ip · user_agent",
    0, 4, NEUTRAL,
)

p.note(
    "<b>关系设计要点</b><br>"
    "· documents.current_file_id 与 file_records 是「当前文件」指针；历史文件在 recycle_bin<br>"
    "· 通知 ↔ 资料 M:N（notice_documents）：一个通知可关联多份资料，反之亦然<br>"
    "· 阅读分母 = notice_targets 快照 ∩ users.is_active（不含管理员）<br>"
    "· 资料可见范围 = documents.audience_scope + document_targets<br>"
    "· categories 自关联树，服务层强制 ≤4 级且防成环<br>"
    "· audit_logs 删用户时 user_id 置 NULL、username 文本保留（§25 留痕）",
    90, 1140, 790, 190, "#eef2f6", "#8aa4bb",
)
p.note(
    "<b>17 张表构成</b><br>"
    "实体表 11：users · departments · documents<br>file_records · categories · tags · notices<br>"
    "notice_types · recycle_bin · backup_records · audit_logs<br><br>"
    "关联/范围表 6：user_departments · document_tags<br>notice_documents · notice_targets<br>"
    "document_targets · notice_reads<br>合计 17（与 docs/DATABASE.md 一致）",
    920, 1140, 750, 190, "#fff8e6", "#d9b45a",
)
pages.append(p)


# =====================================================================
# 6 备份恢复
# =====================================================================
p = Page("p6", "06 备份与恢复", 1800, 1220)
p.title("备份与恢复", "备份覆盖 数据库 + 业务文件；恢复是破坏性操作，全程互斥")

p.band("① 创建备份（POST /backups）", 40, 80, 1720, 250)
b1 = p.box("请求<br>backup_type=full|database<br>source=manual|auto", 70, 122, 260, 84, PRIMARY_L)
b2 = p.box("<b>恢复互斥检查</b><br>_restore_lock.locked()?<br>→ 409 CONFLICT", 370, 122, 270, 84, DANGER)
b3 = p.box("磁盘硬闸门<br>assert_disk_headroom<br><b>建目录之前</b>判", 680, 122, 270, 84, WARN)
b4 = p.box("建 backup_&lt;ts&gt;/<br>建 record(processing)", 990, 122, 270, 84, NEUTRAL)
b5 = p.box("SQLite online backup<br>复制 documents/notices/<br>recycle_bin/uploads", 1300, 122, 330, 84, ACCENT)
b6 = p.box("manifest.json<br>schema_version · created_at<br>db_sha256 · 每文件 sha256", 990, 230, 270, 70, NEUTRAL)
b7 = p.box("status=completed<br>审计 backup_create", 1300, 230, 330, 70, ACCENT)
b8 = p.box("保留策略 apply_retention<br>KEEP_DAYS / KEEP_COUNT<br>PRE_KEEP_COUNT（白名单目录）", 680, 230, 270, 70, NEUTRAL)
b9 = p.box("失败 → status=failed<br>同样写审计（留痕）", 370, 230, 270, 70, DANGER)

p.band("② 恢复（POST /backups/{id}/restore）", 40, 360, 1720, 400)
r1 = p.box("confirm=true<br>否则 400", 70, 402, 230, 76, PRIMARY_L)
r2 = p.box("<b>获取 _restore_lock</b><br>非阻塞：已有恢复→409<br>最外层 try/finally 释放", 340, 402, 300, 76, DANGER)
r3 = p.box("① 安全备份当前状态<br>create_backup(_inside_restore)<br>内部通道绕自锁", 680, 402, 320, 76, ACCENT)
r4 = p.box("② 全量校验<br>manifest + 所有 sha256<br><b>失败则不动数据</b>", 1040, 402, 290, 76, WARN)
r5 = p.box("③ stage 目录<br>复制+校验<br>恢复副本", 1370, 402, 260, 76, NEUTRAL)
r6 = p.box("④ 释放池化连接", 680, 512, 320, 64, NEUTRAL)
r7 = p.box("⑤ 回写 app.db<br>切换业务目录<br>旧状态留 *.pre_&lt;ts&gt;", 1040, 512, 290, 64, WARN)
r8 = p.box("⑥ 新会话记录<br>restored + 审计<br>补录安全备份", 1370, 512, 260, 64, ACCENT)
r9 = p.box("finally: 释放锁<br>失败路径也必须释放<br>否则系统永久无法恢复", 340, 512, 300, 64, DANGER)
p.box("并发恢复：第二个 409<br>恢复期间备份：409<br>（2026-10-29 补齐）", 680, 616, 320, 70, PRIMARY_L)
p.box("启动时孤儿备份认领<br>_reclaim_orphan_backups<br>（认领不删除，幂等）", 1040, 616, 290, 70, NEUTRAL)

p.band("③ 备份覆盖范围（§28：不能只备数据库）", 40, 800, 1720, 180)
p.box("data/database/app.db<br>(online backup 快照)", 70, 842, 300, 110, DATA)
p.box("data/documents/<br>资料当前文件", 400, 842, 250, 110, NEUTRAL)
p.box("data/notices/<br>通知附件", 680, 842, 250, 110, NEUTRAL)
p.box("data/recycle_bin/<br>回收站文件", 960, 842, 250, 110, NEUTRAL)
p.box("data/uploads/<br>(预留，一并备份)", 1240, 842, 250, 110, NEUTRAL)
p.box("不备份 backups/ 自身<br>(避免递归膨胀)", 1520, 842, 200, 110, WARN)

p.note(
    "<b>灾备边界（§28 锁定决策）</b><br>"
    "不做异地/双机/离机灾备拷贝。应用内自动备份 + 同机保留策略即最终方案。<br>"
    "配套纪律：磁盘健康纳入日常巡检（启动巡检 + 备份前硬闸门）。",
    70, 1020, 820, 160, "#fff8e6", "#d9b45a",
)
p.note(
    "<b>已知限制</b><br>"
    "· 显式设 DATABASE_URL 时，备份仍取 DATA_DIR/database/app.db，<br>"
    "  可能与 engine 实际连接的库不是同一个（建议保持留空）<br>"
    "· 进程内锁：多 worker / 多实例部署会失效（必须单实例）",
    920, 1020, 800, 160, "#fdf0dc", "#d2983f",
)

p.edge(b1, b2)
p.edge(b2, b3)
p.edge(b3, b4)
p.edge(b4, b5)
p.edge(b5, b6, exit_xy=(0.5, 1), entry_xy=(0.75, 0))
p.edge(b6, b8)
p.edge(b8, b9)
p.edge(r1, r2)
p.edge(r2, r3)
p.edge(r3, r4)
p.edge(r4, r5)
p.edge(r4, r7, exit_xy=(0.5, 1), entry_xy=(0.5, 0))
p.edge(r7, r8)
pages.append(p)


# =====================================================================
# 7 接入点与部署
# =====================================================================
p = Page("p7", "07 接入点与部署运维", 1800, 1400)
p.title("外部接入点与部署运维", "系统与外界交互的全部边界")

p.band("接入点清单", 40, 80, 1720, 470)
for lbl, body, x, y, st in [
    ("① HTTP 唯一入口", "浏览器 → uvicorn<br>所有流量经 SecurityMiddleware<br>内网防火墙 + IP 白名单是第一道闸门", 70, 122, PRIMARY),
    ("③ 文件系统 DATA_DIR", "documents / notices / recycle_bin<br>backups / uploads / logs / database<br>必须本地磁盘（SQLite 不支持网络盘）<br>路径全部由配置派生，无硬编码", 750, 122, NEUTRAL),
    ("④ SQLite 单文件库", "data/database/app.db<br>17 张表；不存文件二进制<br>备份用 online backup API<br>单进程写入", 1090, 122, DATA),
    ("⑤ 内网 pip 源", "requirements.txt 全量 pin<br>部署时 pip install<br>公司镜像源或官方源<br>注意 shell 里的 HTTP_PROXY", 1430, 122, NEUTRAL),
    ("⑥ 进程常驻", "Linux: systemd unit<br>Windows: 任务计划程序<br><b>必须单实例</b><br>多实例会破坏缓存/调度/锁", 70, 340, PRIMARY),
    ("⑦ 自动备份调度", "app/core/scheduler.py<br>BACKUP_SCHEDULE (sat/daily HH:MM)<br>进程内线程，启动补跑<br>留空 = 关闭", 410, 340, ACCENT),
    ("⑧ 磁盘与日志", "log_startup_disk_health()<br>assert_disk_headroom()<br>logs/app.log<br>DISK_MIN_FREE_MB / PERCENT", 750, 340, WARN),
    ("⑨ 时间与时区", "服务器本地时间决定<br>待生效/有效/已失效<br>历史查询 as_of_date<br>部署时确认时区正确", 1090, 340, NEUTRAL),
    ("⑩ 运维审计出口", "/admin/audit-logs<br>查阅 / 删除<br>删日志本身写 audit_delete<br>（可追溯但可逐条擦除）", 1430, 340, NEUTRAL),
]:
    p.box(f"<b>{lbl}</b><br>{body}", x, y, 320, 190, st)

p.band("部署流程（AGENTS §37/§38）", 40, 570, 1720, 330)
for lbl, body, x, y in [
    ("1. 准备服务器", "Python 3.10+<br>本地磁盘 ≥ 业务量×3<br>内网 IP / 域名", 70, 612),
    ("2. 代码与 venv", "拉代码<br><b>不拷 data/ 与 .venv</b><br>python -m venv .venv", 410, 612),
    ("3. 依赖", "pip install -r requirements.txt<br>内网镜像源<br>核对 pin 无漂移", 750, 612),
    ("4. 配置 .env", "APP_ENV=production<br>SECRET_KEY 强随机<br>DATA_DIR 绝对路径<br>COOKIE_SECURE=false(http)", 1090, 612),
    ("5. 预检", "scripts/check_env.py<br><b>退出码必须 0</b><br>只读，不建目录", 1430, 612),
    ("6. 建库", "scripts/init_db.py<br>空库建 16 表 + 初始管理员<br><b>升级时必须跑 migrate.py</b>", 70, 782),
    ("7. 常驻", "systemd / 任务计划<br>--host --port<br>单进程", 410, 782),
    ("8. 网络边界", "防火墙只放行业务端口<br>IP 白名单<br>反代则 HOST=127.0.0.1", 750, 782),
    ("9. 验收", "健康检查 /health<br>完整业务走查<br>备份 → 恢复演练", 1090, 782),
    ("10. 上线后", "首登改密（当前）<br>移除 INITIAL_ADMIN_PASSWORD<br>开启自动备份", 1430, 782),
]:
    p.box(f"<b>{lbl}</b><br>{body}", x, y, 320, 150, PRIMARY_L)

p.note(
    "<b>安全边界（不可让步）</b><br>"
    "· 权限必须后端校验，前端隐藏按钮不算权限<br>"
    "· data/ 绝不静态暴露；文件访问必须过后端鉴权<br>"
    "· 上传：扩展名 + 魔数 + MIME + 大小 + 文件名清洗 + 防穿越<br>"
    "· 生产关闭 /docs /redoc /openapi.json<br>"
    "· APP_ENV 非法直接拒绝启动（不静默降级）<br>"
    "· 密钥/密码不写代码、不入 Git",
    940, 930, 820, 240, "#f0f6ec", "#6b9a68",
)
pages.append(p)


out = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<mxfile host="app.diagrams.net" agent="claude-code" version="24.7.17">',
]
for pg in pages:
    out.append(pg.xml())
out.append("</mxfile>")

path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docs", "diagrams", "项目全景-流程与接入点.drawio",
)
os.makedirs(os.path.dirname(path), exist_ok=True)
with io.open(path, "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(out))
print("written:", path)
print("size:", os.path.getsize(path), "bytes,", len(pages), "pages")
