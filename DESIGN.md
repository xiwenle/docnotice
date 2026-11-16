---
version: alpha
name: 资料通知系统-档案文献风
description: 公司内部资料与通知管理系统的设计系统契约。克制、无图标装饰、语义色驱动；签名语言是「左侧脊线 = 异常/待办程度」+「文头双细线」。2026-09-29 配色改版为「F · 石青」（中性微凉画布 + 沉蓝主色；源 docs/design-tokens/palette-f.json），本文件是样式的唯一权威。
colors:
  # 2026-09-29 配色改版：F 石青（中性微凉画布 + 沉蓝主色 + 衬线题）
  # 用户 2026-09-29 在 2026-09-29 评估的 6 个配色方向里选定 F。
  # 本块与 palette-f.json 必须一致（改颜色改 JSON 再回写，别只改一边）。
  primary: "#2b5d8c"
  on-primary: "#ffffff"
  primary-hover: "#224b73"
  primary-active: "#1a3c5d"
  primary-soft: "#e6edf5"
  primary-faint: "#eff4f9"
  ink: "#1f2529"
  ink-secondary: "#4c545c"
  muted: "#5d656f"
  mut-strong: "#4f575f"
  separator: "#767e86"
  disabled-ink: "#737b83"
  canvas: "#f4f5f3"
  surface: "#ffffff"
  surface-hover: "#eef0ec"
  table-head: "#e9ece6"
  hairline: "#dfe3e6"
  hairline-strong: "#cbcfd3"
  # 状态色与主色解耦：success 不再与 primary 同值（否则主色一改"成功"跟着变色）；
  # info 由冷蓝转中性（蓝已是主色，info 会让位）。
  success: "#2f6b4f"
  success-light: "#eaf2ed"
  warning: "#7d580f"
  warning-light: "#f7efdb"
  danger: "#a63b2a"
  danger-light: "#f9ece7"
  info: "#586068"
  info-light: "#eef0ec"
  purple: "#5f4a68"
  purple-light: "#f1ecf3"
  slate: "#586068"
  slate-light: "#eef0ec"
typography:
  page-title:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif"
    fontSize: 22px
    fontWeight: 600
    lineHeight: 1.35
    letterSpacing: 0px
  heading-lg:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif"
    fontSize: 19px
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: 0px
  card-title:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif"
    fontSize: 15px
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: 0px
  body-md:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif"
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.6
    letterSpacing: 0px
  body-sm:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif"
    fontSize: 13px
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: 0px
  caption:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif"
    fontSize: 12px
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: 0.02em
  stat-number:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif"
    fontSize: 28px
    fontWeight: 700
    lineHeight: 1.15
    letterSpacing: -0.01em
  button-md:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif"
    fontSize: 13px
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: 0px
rounded:
  none: 0px
  # 2026-09-29 勘误：原写成 4 / 6 / 8 / 10，与 style.css 实际值（--radius-xs:3px、
  # --radius-sm:5px、--radius:6px、--radius-lg:8px）及本文正文 Shapes 一节都不符。
  # 以 CSS 与正文为准（正文本来是对的，是 YAML 抄错了）。
  xs: 3px
  sm: 5px
  md: 6px
  lg: 8px
  full: 999px
spacing:
  xxs: 4px
  xs: 8px
  sm: 12px
  md: 16px
  lg: 20px
  xl: 24px
  xxl: 32px
  section: 48px
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.button-md}"
    rounded: "{rounded.sm}"
    padding: "8px 16px"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-secondary}"
    typography: "{typography.button-md}"
    rounded: "{rounded.sm}"
    padding: "8px 16px"
  button-ghost:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-secondary}"
    typography: "{typography.button-md}"
    rounded: "{rounded.sm}"
    padding: "8px 16px"
  button-sm:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-secondary}"
    typography: "{typography.caption}"
    rounded: "5px"
    padding: "5px 10px"
  text-input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.body-md}"
    rounded: "{rounded.sm}"
    padding: "8px 12px"
  text-input-focused:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.body-md}"
    rounded: "{rounded.sm}"
    padding: "8px 12px"
  card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "20px"
  card-header:
    backgroundColor: "{colors.surface-hover}"
    textColor: "{colors.ink}"
    typography: "{typography.card-title}"
    rounded: "{rounded.md}"
    padding: "12px 20px"
  todo-banner:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.card-title}"
    rounded: "{rounded.md}"
    padding: "16px 20px"
  stat-todo-card-danger:
    backgroundColor: "{colors.danger-light}"
    textColor: "{colors.danger}"
    typography: "{typography.stat-number}"
    rounded: "{rounded.md}"
    padding: "18px 20px 18px 24px"
  stat-todo-card-warning:
    backgroundColor: "{colors.warning-light}"
    textColor: "{colors.warning}"
    typography: "{typography.stat-number}"
    rounded: "{rounded.md}"
    padding: "18px 20px 18px 24px"
  stat-todo-card-ok:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-secondary}"
    typography: "{typography.stat-number}"
    rounded: "{rounded.md}"
    padding: "18px 20px 18px 24px"
  cat-tree-node:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-secondary}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.sm}"
    padding: "7px 10px"
  cat-tree-node-active:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.sm}"
    padding: "7px 10px"
  filter-tab:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-secondary}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.full}"
    padding: "4px 14px"
  filter-tab-active:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.full}"
    padding: "4px 14px"
  badge-success:
    backgroundColor: "{colors.success-light}"
    textColor: "{colors.success}"
    typography: "{typography.caption}"
    rounded: "{rounded.xs}"
    padding: "2px 8px"
  badge-warning:
    backgroundColor: "{colors.warning-light}"
    textColor: "{colors.warning}"
    typography: "{typography.caption}"
    rounded: "{rounded.xs}"
    padding: "2px 8px"
  badge-danger:
    backgroundColor: "{colors.danger-light}"
    textColor: "{colors.danger}"
    typography: "{typography.caption}"
    rounded: "{rounded.xs}"
    padding: "2px 8px"
  badge-info:
    backgroundColor: "{colors.info-light}"
    textColor: "{colors.info}"
    typography: "{typography.caption}"
    rounded: "{rounded.xs}"
    padding: "2px 8px"
  badge-neutral:
    backgroundColor: "{colors.hairline}"
    textColor: "{colors.ink-secondary}"
    typography: "{typography.caption}"
    rounded: "{rounded.xs}"
    padding: "2px 8px"
  notice-item:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.body-md}"
    padding: "14px 20px"
  notice-item-needs-action:
    backgroundColor: "#fffdf5"
    textColor: "{colors.ink}"
    typography: "{typography.body-md}"
    padding: "14px 20px"
  bulk-bar:
    backgroundColor: "{colors.primary-faint}"
    textColor: "{colors.ink}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.md}"
    padding: "10px 16px"
  adv-filter:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-secondary}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.sm}"
    padding: "8px 14px"
  tag-chip:
    backgroundColor: "{colors.primary-faint}"
    textColor: "{colors.primary-hover}"
    typography: "{typography.caption}"
    rounded: "{rounded.xs}"
    padding: "2px 8px"
  table-header:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.muted}"
    typography: "{typography.caption}"
    padding: "10px 14px"
  table-row:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.body-sm}"
    padding: "12px 14px"
  table-row-expired:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.muted}"
    typography: "{typography.body-sm}"
    padding: "12px 14px"
  page-btn:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-secondary}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.sm}"
    width: 32px
    height: 32px
  page-btn-active:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.sm}"
    width: 32px
    height: 32px
  page-jump:
    textColor: "{colors.ink-secondary}"
    typography: "{typography.body-sm}"
    gap: "{spacing.xs}"
  page-jump-input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.sm}"
    width: 56px
    height: 32px
    textAlign: center
    focus: "border {colors.primary} + ring 3px {colors.primary-soft}（与 form-control 焦点一致）"
---

# 资料通知系统 · 设计系统契约
## 0. 2026-11-11 安全交互契约补丁

- 登录页 `next` 与详情返回 `return_to` 的前端行为：仅允许同源 `/documents`、`/notices` 及其 query；`//host`、`/\host`、编码反斜杠、`javascript:`、`data:`、`#fragment`、CRLF、控制字符与超长值一律回落到 `/`。服务端必须再做一次 allowlist，不得只依赖前端隐藏或字符串前缀。
- 带 `Origin` / `Referer` 的非安全方法请求按 `TRUSTED_HOSTS` + scheme + host + port 精确校验；`*.localhost` 仅匹配本地测试子域，不能用任意通配放开生产域；多值 Origin、含路径 Origin、`Origin: null`、不同端口均视为跨站。
- 文件详情、附件卡与预览区的样式/交互契约保持「工具栏只有一个默认下载主 CTA；附件卡允许一个明确标注为附件/原稿附件的次级链接」；附件卡链接不得冒充第二个当前主文件下载按钮。
- 文件下载、附件下载与预览响应一律 `Cache-Control: private, no-store`；PDF 仍可 inline 预览，但不得让浏览器缓存敏感字节。
- 弹窗、确认框、toast、筛选 chips、返回导航的视觉契约不变；本补丁只收紧 URL/安全响应行为，不改变布局、配色、字号或动效。


## Overview

公司内部资料与通知管理系统（FastAPI + Jinja + 原生 HTML/CSS/JS）。使用者分两类：普通用户（查资料/看通知，即用即走）与管理员（内容维护 + 系统管理，防失控）。视觉基调为**档案文献风**：细边框、弱阴影、清晰字阶、克制配色、**无图标装饰**。

本文件由 design-system-capture 从 `app/web/static/css/style.css` 反推，并纳入 2026-08 UI 改版的设计决策（三个原型已获确认，样张不入库）。**本文件是样式与组件的权威**（2026-09-29 勘误：原文"改版实现期间…三层覆盖补丁在阶段 4 合并前以本契约为准"已过期——级联死代码合并 2026-08-30 就已完成，见文末 Known Gaps）。

## Colors

全部颜色必须来自上表 token（style.css `:root` 对应变量）。语义分工：

> **2026-09-29 勘误 —— 契约名与 CSS 变量名并非同名。** 原文写「同名变量」不准确，
> 30 个颜色 token 里有 20 个在 CSS 里用的是另一套命名。以下映射为**唯一对照口径**，
> 查代码/写代码都按右列取值：

| 契约名 | CSS 变量 | 值 |
|---|---|---|
| `canvas` | `--bg` | #f4f5f3 |
| `surface` | `--bg-card` | #ffffff |
| `surface-hover` | `--bg-hover` | #eef0ec |
| `table-head` | `--bg-th` | #e9ece6 |
| `ink` | `--text-1` | #1f2529 |
| `ink-secondary` | `--text-2` | #4c545c |
| `muted` | `--text-3` | #5d656f |
| `mut-strong` | `--text-mut` | #4f575f |
| `separator` | `--sep` | #767e86 |
| `disabled-ink` | `--disabled-ink` | #737b83 |
| `primary-soft` | `--primary-light` | #e6edf5 |
| `primary-faint` | `--primary-lighter` | #eff4f9 |
| `hairline` / `hairline-strong` | `--border` / `--border-strong` | #dfe3e6 / #cbcfd3 |
| `on-primary` | `--text-inverse` | #ffffff |

（其余 token —— primary 三态、success/warning/danger/info/purple/slate 及其 light
底色 —— 契约名与 CSS 变量名一致，即 `{colors.warning}` → `--warning`。）

- `{colors.primary}` 唯一品牌色（石青 `#2b5d8c`）：主操作、激活态、链接、表格行内链接。
  **主色一屏内的实心色块数量是本设计的头号风险**：换肤前那版专业蓝之所以被推翻，是因为它同时
  承担了 8 类语义（主按钮 / 侧栏选中 / 树选中 / 页签选中 / 行内按钮 / 链接 / 脊线 / 圆点），
  一屏 12 处以上实心块 —— **一个颜色背 8 个含义，等于没有层级**。
  验收时应按此口径复查（E2E 的 V 组用例已有同类断言）。
- 状态四色成对使用（实色文字 + light 底色）：success=生效，warning=即将到期/未查看，danger=异常/未读/错误，info=已读/中性提示。四色文字对各自 light 底对比度均 ≥4.5:1（满足 WCAG AA）。danger 按钮 hover 加深至 `#8c2f20`。
- **2026-09-14 换肤裁决（2026-09-29 复核保留）**：`{colors.separator}` 与 `{colors.disabled-ink}` 为**新增专用 token**——分隔符（`·` `/` `|`）与禁用态文字一律用它们，**禁止再借 `hairline-strong`**（现值 `#cbcfd3`，在浅底上对比度仅 1.3~1.7：做边框合适、做文字几乎看不见；原型阶段已踩过同类事故）。
- **换肤后必须复跑对比度扫描**：`muted` 最低 4.50、`mut-strong` 最低 6.00（底色取 canvas / surface / table-head 三者中最不利者）。
  **2026-09-29 实测（F 石青，最劣底均为 `table-head`）**：ink 12.99、ink-secondary 6.45、muted 4.95、
  mut-strong 6.15、separator 3.45、disabled-ink 3.60；主按钮白字 6.89；状态色对各自浅底最低 5.52。
  ⚠️ 本轮 `mut-strong` 初值 `#586068` 只有 5.35:1，**未达本条下限 → 把颜色压深到 `#4f575f`，而不是放宽下限**。
- **签名规则：左侧脊线 = 异常的紧迫程度**。`3px` 左边框，danger（红）= 必须处理，warning（黄）= 需要处理，hairline（灰）= 正常/无待办。该语言贯穿统计卡、待办条幅、通知行、管理待办卡。
- 禁止引入新色相。需要新状态时复用四色对。

## Typography

系统字体栈（不加载 Web 字体）。字阶：`page-title 22` → `heading-lg 19` → `card-title 15` → `body-md 14`（默认正文）→ `body-sm 13`（表格/树）→ `caption 12`（徽章/辅助）。数字（统计值、日期、计数徽章）必须 `font-variant-numeric: tabular-nums`。

**2026-09-14 换肤裁决（2026-09-29 复核不变）**：`h1/h2/h3`、`.page-header h1`、`.card-title`、`.stat-value`、`.todo-title`、登录页题一律走**衬线栈** `--font-serif`（`Noto Serif SC / Source Han Serif SC / Songti SC / STSong / SimSun`）并加 `letter-spacing .03em`。**正文、表格、表单仍为无衬线**——衬线只用于"题"与"大数字"，覆盖到正文会显著降低可读性。

## Layout

- **外壳：`左侧栏 192px（sticky, 100vh） + 主区`**（2026-09-14 由「顶栏 + 容器」改为三栏邮箱式，2026-11-03 收窄为 192px，源 2026-09 静态参照稿）。导航不再用下拉菜单：普通用户侧栏只有「常用」；管理员侧栏为「日常操作 / 基础配置 / 系统运维」三段（2026-11-08 分层），管理段由 `{% if is_admin %}` 守卫。
- 管理员侧栏分层（2026-11-08）：「日常操作」固定显示工作台、资料、通知、新建资料、新建通知；「基础配置」（分类/标签/通知类型/用户/部门）与「系统运维」（回收站/备份恢复/操作日志）用原生 `details.sb-group[data-sb-group]` 默认折叠。当前页命中分组时服务端渲染 `open`，用户手动展开态写入 `localStorage["ui.sideGroup"]`（JSON，按组名存 true/false）；两者都只是 UI 偏好，不参与权限判断，不改变路由、不改变 `active_nav`。窄栏沿用既有 64px 收起态与 `data-short` 单字标记。
- 侧栏收起态 `64px`（常用三项用细线图标，其余项用单字标记），偏好存 `localStorage`，**仅桌面端（≥901px）生效**；≤900px 侧栏为抽屉（`body.drawer` + 遮罩），由面包屑顶条的 `☰` 开关。
- 面包屑顶条 `50px` sticky，底部 **`3px double` 墨线**（签名元素「文头双细线」），右侧为快捷搜索（回车 / 按 `/` 聚焦 → `/documents?keyword=`）；页面已有页内搜索（`#f-keyword`）时顶条搜索隐藏，避免两个入口。
- 页面容器版心 `max-width 1360px`（`--content-max`；用户 2026-09-14 定版，不做动态切换），内容区上边距 `28px`、下边距 `56px`。**宽度必须收敛到 `--content-max` / `--card-max` / `--card-max-lg` 三个变量**，禁止页面各写 `max-width`（原型阶段因此出现 5 个页面漏写 `margin:auto` 而靠左）。
- 节奏：页头 → 状态 tabs →（工具条/筛选）→ 内容卡 → 分页；区块间距 `{spacing.xl}`。
- 资料列表页为两栏：分类导航侧边栏 `260px`（sticky）+ 主区自适应；`<1200px` 侧边栏移到顶部、单列（保证 640px 宽表放得下）。
  侧边栏内部三段固定：头部（标题 + 视图切换）→ 分类搜索 → 可滚动列表；底部常驻
  `N 个分类 · M 份资料` 计数。**整栏不再整体滚动**（2026-11-04）。

## Elevation & Depth

阴影仅三级，克制使用，**阴影色温必须跟随画布色温**：`--shadow-sm: 0 1px 2px rgba(31,37,41,.05)`；`--shadow-md: 0 3px 8px rgba(31,37,41,.08)`；`--shadow-lg: 0 10px 24px rgba(31,37,41,.12)`。卡片默认**无阴影**（只留 1px 细边框），仅浮层使用。

> 色温两次翻转的教训（2026-09-14 暖纸→`rgba(41,36,28,…)`；2026-09-29 中性→`rgba(31,37,41,…)`）：
> 暖调阴影压在冷底上会发黄、冷调阴影压在暖纸底上会发灰。**换色温时阴影、遮罩、光晕必须一起转**，
> 而且它们多数写在 `box-shadow` / `radial-gradient` 里、**不经过 `var()`**，最容易漏 ——
> 2026-09-29 就漏过两处 `rgba(37,99,235,…)`（最初那版专业蓝），已在同一轮清掉。

## Shapes

圆角四档（2026-09-14 换肤对齐原型）：`xs 3`（徽章/chip）、`sm 5`（按钮/输入/侧栏项）、`md 6`（树节点/卡片头）、`lg 8`（卡片/条幅）、`full`（tabs 胶囊/计数徽章）。
**表头底色 = `{colors.table-head}`（`#e9ece6`）**，行分隔为 `1px dashed {colors.hairline}`（虚线是「档案文献风」的行语言，实线会显得像表单网格）。
2026-09-29：`table-head` 相对上一版**加深过一档**（旧 `#f3f0e8` 与卡面 `#fffefa` 几乎同色，分区只能靠虚线撑）。
**表格列宽**：`.table` 默认 `table-layout: auto`，浏览器会**忽略 `th` 上的 `width` / `min-width`**——需要精确列宽的表必须加 `.table-fixed` 并用 `<colgroup>` 声明（`documents.html` 已按此改：不声明时复选框列被撑到 86px、标题列被挤到逐字换行、整表横向溢出容器 `21px`）。

## Components

- `button-primary/secondary/ghost/sm`：动词文案（"保存修改"而非"提交"）；禁用态用 `{colors.disabled-ink}` 文字 + `table-head` 底（**不用 `opacity`**，见下 Components 末条；本条原文"opacity .55"已于 2026-09-29 勘误）。
- `text-input` / `text-input-focused`：focus 为 `3px` 主色外圈（`box-shadow: 0 0 0 3px var(--primary-light)`）。
- `card` + `card-header`（surface-hover 底）+ `card-title`（左侧 4px 主色小色点，唯一允许的装饰点缀）。
- `todo-banner`：员工首屏待办条幅；左脊线 warning；零待办整条不渲染。
- `stat-todo-card-danger/warning/ok`：管理待办卡；ok 态显示"✓ 无待办"并灰化；点击直达筛选结果。
- `cat-drilldown`（2026-11-04 新增，取代长期使用的全量折叠树）：资料页分类导航。
  两种视图，同一个按钮组切换，**默认「逐级下钻」**：
  - **逐级下钻**：只渲染当前层的子分类，顶部完整面包屑（不截断、不出现 `…`，
    装不下时自然换行），任意一级可点回跳；`‹ 返回上级` 与 `Backspace` 退一层。
    行数始终等于当前层子分类数，分类涨到几千也不增长。
  - **展开全树**：只展开**文件夹（分类）**；资料永远不作为树节点，只在右侧列表出现。
    切进来默认折叠（只显示一级文件夹），工具栏 `展开全部 / 折叠全部` 一键开合；
    **收起的分支不渲染子节点**（点开哪支才生成哪支），820 个分类时首屏仍只有几十行。
  - 两种视图共用：`全部资料` / `未分类` 固定入口（任意层级都能一键回到全局）、
    跨层级分类搜索（结果显示父级路径 + `<mark>` 高亮）、`↑↓` 移动 / `Enter` 进入 / `/` 聚焦搜索。
  - 行仍是无图标文字行，选中为 primary 实底；层级在树视图里用缩进 + 左竖线表达。
  - `document_count` 一律为子树口径（自身 + 全部子孙），且**按当前用户可见范围统计**。
- 主区不再显示 `loc-bar / 当前范围 / 返回全部资料` 重复条（2026-11-11 删除）；
  分类位置只由左侧面包屑、分类树和底部筛选摘要表达，避免同一路径重复出现。
- `filter-tab`：胶囊；选中主色实底；计数数字用 `tabular-nums`。
- `badge-*`：时效/阅读/通知类型状态；过期资料行标题用 `{colors.muted}` + 删除线（`table-row-expired`）。
- `file-chip`（2026-09-29 修订）：**文件类型 chip 一律中性**（`table-head` 底 + `ink-secondary` 字 + `hairline-strong` 边框），
  **禁止再映射到状态色**。旧做法把 PDF 画成 danger 红、DOCX 画成 info 蓝、XLSX 画成 success 绿 ——
  文件格式被渲染成「危险/信息/成功」，一列里三色混排，是历史上一屏颜色过多的主因。
- `notice-item` / `notice-item-needs-action`：未查看行 = warning 脊线 + `#fffdf5` 底 + 行尾"查看"主按钮（2026-11-03：确认功能下线，待处理只剩未查看）。
- `bulk-bar`：管理员批量整理条，勾选 ≥1 行才滑出（max-height 过渡）。
- `adv-filter`（2026-11-09 用户修订）：资料页与通知页的高级筛选**始终展开**，不使用 `details`；左侧为窄标题轨，右侧字段共用同一标题/控件基线。桌面资料 4 列、通知 3 列，≤768px 两列，≤560px 单列。激活项数仍用计数标记。
- `rate-bar`：管理员统计页「已查看率」进度条（复用 progress 条语言；原确认率条已随通知确认功能下线）。管理员通知列表行内另有 `.rate-wrap` 展示「已查看 / 应阅读」进度，二者都不是确认率。
- `sidebar-item`（2026-09-14 新增）：侧栏导航项；选中 = `primary-soft` 底 + 左 `3px` 主色脊线；收起态只显示 `data-short` 单字。底部为当前用户与「修改密码 / 退出登录」。
  **2026-09-29 勘误：契约名是语义名，真实类名见 `base.html` —— `.side` / `.sb-brand` / `.sb-nav` /
  `.sb-sec` / `.sb-item`（`.on` 为选中）/ `.sb-foot` / `.sb-collapse`；外层 `.main` 内是 `.crumbbar`
  （即本文所称 `crumb-bar`）+ `.page-main > .container`。类名以 base.html 为准。**
- `crumb-bar`：面包屑顶条（真实类名 `.crumbbar`）；右侧快捷搜索。
- `confirmModal` 的 `message`（2026-09-29）：**先整体转义，再回填极简白名单标签**（只放行 `strong` / `br`），
  带属性的标签与其它标签保持转义。这样调用方可以写强调，但不会开 XSS 面（实现见 `ui.js::renderConfirmMessage`）。
- `badge-*`（2026-09-14 修订）：**前置 5px 状态圆点**（`::before`，`background: currentColor`），圆点即状态，文字可只作说明。
- 按钮禁用态：**不再用 `opacity`**（会连带压掉文字可读性）——改显式 `{colors.disabled-ink}` 文字 + `table-head` 底 + `hairline` 边框。
- `switch`（2026-09-14 修订）：滑块轨道 `36×20`，勾选态 `var(--primary)` 实底。**真实 `input` 必须"视觉隐藏但可聚焦"**（`position:absolute` + `opacity:0` 覆盖在轨道上，**不得用 `display:none`**——那样键盘无法 Tab 到、读屏读不到）；`:focus-visible + .track` 给 `2px` 主色焦点环。

## Do's and Don'ts

- **Don't 用 emoji 做图标**——本项目声明"无图标装饰"，层级/状态用缩进、脊线、字重、色彩表达。
- **Don't 为单页面写一次性硬编码色值/间距**——必须引用 token；发现 token 不够用时先补进 `:root` 并更新本文件。
- **Don't 用前端 `hidden` 做权限**——权限元素由 Jinja 服务端不渲染（`{% if is_admin %}`）；确需 `hidden` 时全局规则 `[hidden]{display:none!important}` 必须存在（display:flex 会覆盖 hidden 的 UA 默认值，已在原型阶段踩过）。
- **Don't 用「某个元素在不在」反推身份**——2026-09-14 换肤踩到：`documents.js` / `notices.js` / `document_detail.js` / `notice_detail.js` 四处曾用 `!!document.querySelector('#nav-admin-dropdown')` 判断管理员，顶栏换成左侧栏后该元素消失，**管理员被静默降级为普通用户**（资料列表不渲染选择列 → 表头 6 列、行 5 格 → 整行左移、标题逐字换行）。身份**只能**经 `ui.isAdmin()` / `ui.currentUser()`（内部只读 `#user-data`）获取。前端测试不校验 DOM，此类回归只能靠 `scripts/theme_check.py` 的列数断言守住。
- **Don't 在 ui.js 之外新增 innerHTML 列表渲染**——转义纪律依赖 `escapeHtml` 集中在 `ui.js`（存量 113 处 innerHTML 不动，有 50 条 XSS 测试对冲）；新代码的列表/行渲染一律走 `ui.js` 的 row 模板函数，把 innerHTML 收敛到单文件。review 新增 JS 文件时 grep innerHTML（评审 Q1 约定，2026-10-25 固化）。
- **Don't 在文案里说系统语言**——"闭区间自动计算"应写成"标记「已过期」的资料请停止使用"；空态必须给出下一步动作（"清除筛选"按钮）。
- **Do 让时效状态成为资料展示的第一视觉属性**（任何列表/卡片/详情页）。
- **Do 尊重 `prefers-reduced-motion`；键盘焦点可见（`a/button:focus-visible` 主色 outline）。**
- **Do 让长表单提交区留在表单末尾**：不使用 sticky 提交条覆盖首屏字段；保存/取消始终跟随表单内容。
- **Do 让资料列表的生效区间可换行**：完整显示「暂无失效日期」，并保留 `title` 作为悬停兜底；不要只靠省略号。
- **Do 区分管理员阅读视角**：管理员通知列表不渲染个人「未查看」徽章；工作台最新通知显示「已查看 X / 应阅读 Y」。
- **Do 统一文件词**：资料用「默认下载 / 主文件 / 附件」；通知详情用「下载通知文件」，不要混用「下载原文件」。

## Responsive Behavior

- ≤1024px：表单网格单列；通知详情侧栏纵向排列（阅读列全宽）。
- ≤900px：**全局左侧栏转抽屉**（移出视口 + 遮罩，面包屑顶条 `☰` 开关，`Esc` / 点遮罩 / 点链接均关闭）；顶条快捷搜索隐藏；窄栏（`body.rail`）在窄屏不适用；**资料列表由表格重排为卡片**（DOM 仍保留 table/td，仅 CSS 切换 display；显示「生效区间/状态/文件」标签与底部操作），不再依赖横向滚动。
- ≤768px：两栏布局转单列（分类树侧边栏置顶，`docs-layout` 子项 **stretch 撑满**，
  宽表只在 `.table-wrap` 内横向滚动，不得撑大布局视口）；工具条控件全宽；模态底部抽屉化。
- ≤480px：统计卡两列、间距收紧。
- ≥1200px：工作台「管理待办」固定五列（避免第 5 张卡落单）；资料表不再需要横向
  滚动，表头 sticky 在面包屑下方；表格空态使用紧凑 20px 垂直内距。
- 901–1199px：资料页分类导航置顶、表格获得整行宽度（实测 901px 主区 651px、
  1024px 776px、1199px 951px，均 ≥ 表格 640px）；表头 sticky 从 901px 起生效。
  1200px 起导航回到左侧，表格仍放得下。资料页不再需要横向滚动提示。
  置顶模式下分类列表区 `max-height:340px` 内部滚动，避免把表格推到屏幕外；
  逐级下钻与展开全树在同一断点下行为一致。
- 2026-10-19 断点依据：601/640/769/800/900 五档主区实测 495–628px，均小于宽表
  640px 最小宽度（旧 600px 卡片断点会留下 26–145px 表内横滚），因此卡片断点取 900px。

## Content States（2026-10-19）

- **0 条**：隐藏无意义的搜索/筛选/表头/零值卡；用空态引导（一个主操作 + 最多三步）。
  空态只使用真实信息与真实动作，不填假数据；loading skeleton 与 empty 严格分开。
- **≥1 条**：一律使用密集表格/列表；保留分页、批量整理与既有 DOM/选择器。

  > **2026-11-04 修订**：原「1–2 条改用 featured 档案卡/通知卡」的稀疏态已**整体移除**
  > （通知页 `.sparse-notice`、资料页 `.sparse-doc`、工作台 `#dashboard-sparse` 及
  > 对应 JS/CSS 全删）。原因：公司刚上线只有几条数据时，大卡片占满整屏、与侧栏
  > 密度割裂，且让刚部署的系统看起来"和演示时不一样"。现在条数少只是列表短，
  > 布局不再随数据量跳变。
  通知列表管理员行只保留主操作（统一为「查看」，草稿状态下线后不再有「编辑」主操作），其余动作
  收进 `⋯`（`details/summary`）；菜单允许溢出通知卡，卡片用首尾项圆角替代裁剪。
- **详情**：通知详情为 `minmax(0,1fr) + 320px` 阅读列布局，正文 `max-width:72ch`；
  侧栏承载我的状态/时间与对象/管理操作，短内容不拉伸整宽；未关联资料为
  compact 行。
- **筛选可回看**：资料/通知列表把 URL 中生效的筛选渲染为 `.filter-chip`，点击可
  单独移除，附「清除全部」；空库与“筛选无结果”继续区分。
- **反馈**：列表错误提供重试；结果数写入 `aria-live`；空查询结果不渲染空 pill。
- **搜索唯一入口**：列表页有 `#f-keyword` 时隐藏顶条输入框，保留一个紧凑的
  「搜索资料」链接（→ `/documents`）作为全局入口；`/` 依次聚焦页内搜索 →
  工作台搜索 → 顶条搜索。
- **侧栏图标**：全部 13 项使用 1.4px 细线 SVG（`aria-hidden`，文字仍是可访问名，
  链接同时有 `aria-label`）；窄栏 64px 下以图标替代 `data-short` 单字。
- **翻页焦点**：只在分页翻页后把焦点移到页面结果标题（`h1[tabindex="-1"]`），
  搜索/筛选不抢焦点；结果数继续由 `aria-live` 播报。

## DOCX Preview（2026-10-20）

- 后端返回 `text`（兼容兜底）+ `blocks`（heading / paragraph / list / table，
  runs 保留 bold/italic/underline），**不返回 HTML**。
- 前端 `ui.renderDocxBlocks` 用 DOM API + `textContent` 渲染；标题用衬线体，
  正文 15px / 1.9 行高，阅读列宽 `max-width:78ch`；表格 13px、细边框、单元格
  保留换行；列表支持有序/无序与最多 5 级缩进。
- DOCX 的 1 级标题映射为页面 `h2`（页面自身 `h1` 是资料/通知标题），避免
  详情页出现多个 `h1`；后续级别顺延，最深到 `h6`。
- 截断或文件损坏时在预览底部提供同一枚「默认下载：…」按钮，不影响原始文件。
- 资料详情与通知详情共用同一渲染器；无新增依赖。

## 资料附件区（2026-11-06）

附件是「不预览的额外文件槽」——存在意义是让人拿到原始文件，
所以它在视觉上**不能和预览抢主角**：

- **位置**：预览卡（`.preview-card`）之后、时效查询之前，独立一张
  `.attachment-card`。没有附件时，管理员的卡片退化为一行提示 + 「添加附件」按钮；
  普通用户**完全不渲染**（避免空区块噪声）。
- **有附件时的行**：类型徽章 → 文件名（单行省略，`max-width:420px`）→
  大小 + 上传时间 → 弹性空隙 → 「下载此附件（.扩展名）」；管理员额外「替换」「删除」。
  头部右侧固定一行小字「仅供下载，不做在线预览」，把语义直接写在界面上。
- **下载文案必须能区分拿到什么**（2026-11-08 收紧）：工具栏主按钮是方案 A 的**唯一默认下载**，统一以「默认下载：」开头，并写出实际会拿到的文件名：有附件时为「默认下载：原稿附件（原文件名）」，无附件时为「默认下载：当前主文件（原文件名）」。附件卡自己的链接写「下载此附件（.扩展名）」，路由仍是 `/attachment`。预览失败、未知格式、DOCX/XLSX 预览失败等兜底按钮写「下载默认文件」；工具栏主按钮才是「默认下载：原稿附件/当前主文件（真实文件名）」。
- 主文件区标注「当前用于在线预览：xxx」；附件区标注「仅供下载，不做在线预览」。不写清楚的话，用户看着主文件预览点下载，拿到原稿附件会困惑。按钮数量、端点与下载目标一律不变：预览永远主文件，`/file` 有附件返回附件、无附件返回当前主文件，附件不提升为第二个当前文件。
- **归档资料不显示任何附件管理按钮**（与「已归档资料不能改」的既有规则一致）。
- **回收站**：`attachment_deleted` 显示为「删除附件」；归档条目若同时带附件，
  在文件名下方以一行小字展示 `[徽章] 附件名（附件）`，避免与主文件混淆。
- **未知类型兜底**：预览区遇到非 pdf/docx/xlsx/xls/图片 的主文件时，
  显示「该格式（PPTX）不支持在线预览」+ 下载按钮，**不再**误走 Excel 渲染器
  （那会让用户看到「该文件不是 Excel」这种莫名其妙的报错）。

## 2026-09-29 配色改版（F · 石青）

**只换颜色，其余一律没动**：外壳结构 / 版心 / 圆角 / 间距 / 字体栈 / 组件类名全部沿用 2026-09-14 版。
令牌真源 `docs/design-tokens/palette-f.json`（配色改版决策记录不入库）。

改动面：

- `style.css`：`:root` 令牌全量替换（30 个）+ 阴影色温转冷中性；**同时并入 5 项配色缺陷修复**
  （见下）；登录页光晕/渐变/投影 8 处硬编码色归一。
- `favicon.svg` / `favicon.ico`：底色 `#22624d` → `#2b5d8c`；ICO 由真实浏览器渲染 SVG
  按 16/32/48/64 **原生尺寸**重打包（不是从大图缩放），并逐帧与现场渲染逐字节比对确认。
- `scripts/theme_check.py`：期望令牌同步（只改期望值，断言逻辑与用例数一字未动）；
  常量 `INK_GREEN` → `INK_PRIMARY`（主色已不是墨绿，旧名字会误导）。
- 模板与 JS：**零改动**（配色全部走令牌）。

并入的 5 项修复（均改在各自功能章节，未新增覆盖补丁层）：

| 缺陷 | 位置 | 处理 |
|---|---|---|
| 未读通知行 hover 仍是旧蓝 `#e0edff` | §13 通知 | 改 `--bg-hover` |
| 进度条中档误用 `--info`（旧值冷蓝） | §25 详情页 | 改 `--slate`；低/中/高三档语义写进注释 |
| `.vb-*` 状态条文字用自造深色硬编码 | §25 详情页 | 改回语义令牌 |
| 文件类型 chip 借状态色（PDF 红 / DOCX 蓝 / XLSX 绿） | §08 徽章 | 改中性（`table-head` 底 + `ink-secondary` 字 + 细边框） |
| `.btn-danger:hover` 同选择器重复定义 | §05 按钮 | 删除永不生效的那条 |

两处**与主色解耦**的关键决策（不照搬旧值）：

- `--success` 不再等于 `--primary`。上一版二者同为 `#22624d`，主色一改「成功色」会跟着变成蓝绿。
- `--info` 由冷蓝转中性 `#586068`。蓝已是主色，再保留一个 info 蓝会与主色抢同一语义
  （"主色" vs "信息/已读"）；状态提示改由明度与左脊线表达。

## 2026-09-14 整体换肤（素卷墨绿 + 左侧栏外壳）

设计源：2026-09 静态参照稿（不入库）。落地方式：**移植设计语言与外壳，不搬 L1 静态页的 HTML**（L1 是每页各写一份侧栏的静态稿，产品是 `base.html` 单点外壳）。

- 改动面：`style.css`（`:root` 令牌全换 + 03 布局层重写为左侧栏 + 新增 27 换肤层）、`base.html`（顶栏 → 侧栏 + 面包屑顶条）、`login.html`（无变化，未登录时不渲染外壳）、`nav.js`（侧栏收起 / 抽屉 / 顶条搜索；移除两处下拉菜单逻辑）、`pages.py`（新增 `active_nav`，按路径派生侧栏高亮）、`ui.js`（新增 `currentUser()` / `isAdmin()`）。
- 回归守卫：`scripts/theme_check.py`（真实浏览器断言令牌、侧栏逐页高亮、组件、交互、**表格列数一致与横向溢出**、控制台零 error；两种角色都验）。
- 未做（有意）：侧栏计数徽章（需接 dashboard 聚合接口）、分类树改 tree-table、回收站免二次确认（另见 2026-09-14 回收站决策记录）。

## Known Gaps

- style.css 三层覆盖补丁已完成**级联死代码合并**（2026-08-30）：删除 185 个被同选择器后文覆盖的声明（其中 23 个整块），合并前后 20 个代表页面截图字节级一致；跨章节同选择器的非冲突属性分工有意保留（62 处，属性不重叠、无级联冲突）。**后续新增样式一律写入对应功能章节，禁止再追加覆盖补丁层**。
- 登录页品牌区在 2026-11-09 晚些时候由用户重新指定恢复初版蓝色渐变、径向色晕与半透明圆形装饰；详见 §29。


---

## 29. UI 重审计收尾契约（2026-11-09）

本轮按 unslop UI 版审计结论做 light polish + 结构项收尾；不改变任何产品语义。
- **登录页**（2026-11-09 用户二次指定）：恢复初版蓝色视觉——浅色径向底色、深蓝品牌渐变、两层半透明圆形装饰和蓝色按钮阴影；文案继续使用当前口径，不恢复已经下线的「通知阅读确认」。
- **侧栏品牌小字**：`ARCHIVE` 改为中文「内部资料」，`letter-spacing: 0`。`--side-w: 192px` 的紧凑档案式侧栏保持不变。
- **工作台**：页头副标题改为「最近资料与通知」；不带人名、不带感叹号。双列改为资料主列略宽、通知次列略窄；900px 以下仍单列。
- **通用装饰**：`.page-header h1` 主色 2px 下划线改为中性 1px 细线；`.card-title::before` 主色竖条改为中性小方标；双细线面包屑 `.crumbbar` 不动。
- **类型 chip 与计数**：文件类型 chip 保留扩展名文字，取消 uppercase 与 0.5px 字距；`.tree-count` 从 999px 药丸改 4px keycap。语义状态色不变。
- **阅读统计**：三张数字卡结构保留（仍是筛选按钮、仍是「未查看/已查看/应阅读」），数字 700→600、内距略收；不删卡、不加 sparkline。
- **390px 资料页**：分类目录在 ≤768px 默认收起为一行，点「分类目录」展开；`documents.js` 只切换 `.cat-open` 类，不改数据、筛选状态或 URL。
- **窄屏管理员宽表**：≤1024px 把 `.table-wrap`（不含资料列表专属 `.docs-main .table-wrap`）转为卡片化展示；DOM 仍是 table，thead 视觉隐藏但保留可访问性，操作列仍可达，去掉「只能横向滚动」的移动端体验。
- **表单窄屏**：文件元信息（长 SHA / 长文件名）允许断行，避免 `innerWidth` 因长不可断内容漂移。
- **空态插画**：`.eg-art` 极浅中性渐变改为纯 `--bg-hover` 纸面；shimmer / skeleton / toast / spinner / 焦点环 / reduced-motion 均不动。

**保护契约不变**：三项锁定决策（通知阅读确认下线、方案 A 下载语义、`effective_to` 为空展示「暂无失效日期」）、`未查看/已查看`、`默认下载/主文件/附件`、语义状态色、档案式侧栏结构、双细线文头、衬线页题、中文业务文案、焦点环与 reduced-motion 一律不因「去 AI 味」改动。


---

## 30. 分类删除语义（2026-11-09 用户确认）

- **删除范围**：删除选中分类节点及其全部子孙分类（整棵子树，含历史停用行）；不提升、不保留子分类。
- **资料处理**：树内所有资料（含已归档资料）的 `category_id` 置空为「未分类」；资料行、文件、附件均不删除。
- **不改其他词表**：资料标签（Tag / `document_tags`）、通知类型（NoticeType / `notices.notice_type`）、通知与资料关联（`notice_documents`）均不删除、不修改。
- **不做历史分类名快照**：已归档资料也显示「未分类」；需要追溯时看备份或操作日志。
- **交互**：2026-11-09 用户重新确认，分类删除改为先弹确认窗；标签/通知类型保留既有确认窗，部门本次也加确认窗。分类名释放，可重建同名（详见 §31）。
- **审计**：`delete_category` 记录 `root_id/root_name/parent_id`、`deleted_category_ids`、`deleted_category_count`、`unclassified_document_count`。
- **前端文案**：分类管理页说明必须明确「删除整棵子树、资料变未分类、资料/文件/附件/标签/通知/通知类型不删除」。


---

## 31. 四套删除语义（2026-11-09 用户确认）

- **分类**：删除整棵子树；树内资料（含已归档）`category_id` 置空为「未分类」；前端删除前确认弹窗显示子树分类数与资料数。
- **标签**：删除标签与 `document_tags` 关联；资料保留，进入「未打标签」；资料分类、文件、附件、通知均不变。
- **通知类型**：普通类型删除后，已有通知 `notice_type` 置空并显示「未分类」；「废止 / 替换」是资料时效联动类型，禁止删除。
- **部门**：删除部门解除 `user_departments` 关联，用户无其他部门时显示「未分配部门」；通知 `notice_targets` 与资料 `document_targets` 的该部门目标行摘除（资料不删、不改成全体可见）；用户、通知、资料、标签均不删除。
- **共同交互**：四套管理页删除均先弹确认弹窗（标签/通知类型已有，分类/部门本次新增）；确认后才调用删除 API。确认弹窗不是权限边界，admin 校验仍在后端。
- **共同审计**：分类记录 `deleted_category_ids/deleted_category_count/unclassified_document_count`；标签记录 `affected_document_count`；通知类型记录 `affected_notice_count`；部门记录 `affected_member_count/notice_targets_removed`。
- **不做的**：不删除资料、文件、附件、通知、用户或标签词表之外的数据；不把标签/部门语义混称为「未分类」；不改通知发布、阅读、下载、备份恢复语义。
---

## 32. 容量、压缩与窄屏表格契约（2026-11-09）

- **数据库连接池**：文件型 SQLite 使用 `DB_POOL_SIZE=20` + `DB_MAX_OVERFLOW=20`，池等待 10 秒，连接回收 30 分钟。连接池等待超时必须是 `503 DB_BUSY` + `Retry-After: 5`，不得伪装成 500。
- **容量基线**：隔离验收为 1,000 份资料 / 100 个普通用户 / 100 条通知 / 10,000 条阅读快照；100 用户同时登录和 100 并发冷混合读取均 100/100 成功。
- **文本压缩**：HTML、JSON、CSS、JS、XML、SVG 使用选择性 gzip；PDF、DOCX、XLSX、XLS、PPTX 和图片响应保持不压缩，避免二进制下载 CPU 放大与流式响应风险。
- **窄屏宽表**：≤1024px 的管理员表格（用户、日志、备份等，不含资料列表的专属卡片规则）转为卡片化展示；只改显示，不改 table DOM、列数、操作按钮与权限。
- **反向代理**：反代部署必须保证真实客户端 IP（可信 `X-Forwarded-For` + `TRUST_REVERSE_PROXY_HEADERS=true`，且应用端口只对代理开放）；否则同一出口 IP 的集中登录会触发默认每 60 秒 10 次限速。

---

## 33. 管理员批量治理条与预览面板（2026-11-11）

### 33.1 批量条

- **只在管理员资料页出现**。普通用户不渲染选择列、批量条或治理高级筛选；权限仍由后端强制，前端隐藏不是边界。
- 至少选中 1 条后显示 `#doc-bulk-bar`。文案固定包含：已选总数、本页已选、其他页已选；当前筛选模式另显示“当前筛选全部”胶囊。
- 两个选择模型：
  - `ids`：逐条或本页勾选，跨页累计；批量条显示已选总数。
  - `filter`：点击“选择当前筛选全部（N 条）”进入；取消本页个别项写入 `exclude_ids`，显示“当前筛选全部 N 条，排除 M 条；有效选择 K 条”。
- 筛选条件或每页条数改变时清空选择，避免把旧筛选的全集误套到新条件。批量条提供“清空选择”和“批量操作”；危险动作不在条上直接执行。
- 长文案允许换行，数字使用等宽数字；≤768px 时选择摘要独占一行，三个按钮可伸展；≤560px 按钮可各占整行。不得造成横向溢出。

### 33.2 预览与执行面板

- 面板标题“批量操作”，顺序固定：本次操作范围 → 动作选择与说明 → 动作参数 → 预览摘要 → 操作按钮。
- 动作：设置分类、清除分类、追加标签、移除标签、设置可见范围、删除到回收站、从回收站恢复。动作或参数改变后，已有预览立即失效，“确认执行”重新禁用。
- 可见范围只约束普通用户：管理员始终可见全部资料，不受 `audience_scope` 限制；指定人员候选列表不展示管理员，后端也拒绝管理员 ID 写入可见目标。此规则写入 API/DESIGN/HANDOFF 文档，不增加页面文案。
- 标签、部门和人员的多选参数统一使用复选列表，不用原生 `select multiple`：普通单击即选中/取消，不要求 `Ctrl/Command`；列表上方提供即时搜索和“已选 X 项”计数，搜索隐藏项不会丢失已选状态。
- “预览影响”展示三格统计：匹配、预计变更、跳过；另显示旧分类/旧可见范围计数、权限变化 warning、逐条跳过清单（最多展示接口返回的前 50 条）。预览 5 分钟内有效；数据集合或数量变化时后端返回 409，界面必须提示重新预览。
- “确认执行”只在预览成功后启用；`archive` 使用危险动作样式与“确认批量删除”文案，其他动作使用主按钮“确认执行”。执行完成后原位变为结果摘要：匹配/实际变更/跳过/失败项，并自动清除选择、刷新列表。
- 批量结果不是“全部成功”承诺：必须如实列出跳过项与原因。危险语义（归档）不得用普通保存按钮伪装。

### 33.3 标题行治理元数据

- 资料列表不新增独立“可见范围/附件”大列；治理元数据以 20px 高小徽章放在标题下方 `.doc-meta-line`：
  - 可见范围徽章：全体可见 / 指定部门 / 指定人员；
  - 有附件时追加“附件”徽章，提示下载默认走附件；
  - 分类路径与标签继续在同一副行，标签最多展示 2 个，其余显示 `+N`。
- 徽章只做识别与筛选提示，不抢“状态”徽章第一视觉层级；不得让长路径撑破表格，路径允许任意断行。
- “无主文件”在文件列显示“暂无文件”，不伪造类型徽章；治理筛选可直接筛出这些记录。

---

## 34. 列表 → 详情 → 列表返回导航（2026-11-11）

- 详情页左上返回按钮使用 `.detail-back-nav` + `.detail-return-link`，文案为“返回资料列表”或“返回通知列表”；它是页面返回的唯一主入口，不与浏览器后退按钮竞争。
- 列表链接进入详情时自动补 `return_to=<当前 pathname + query>`。详情页由后端白名单校验：只接受同源 `/documents`、`/notices`，保留 query；拒绝绝对 URL、`//`、fragment、控制字符、其他路径和超长值，非法时回落对应列表。**禁止只靠前端校验开放重定向。**
- 返回时恢复来源 query 与滚动位置：点击详情链接的瞬间把 `window.scrollY` 存到 `sessionStorage`，键为列表 `pathname + search`；点击带 `data-return-scroll` 的返回按钮后标记 pending，列表异步渲染期间每 150ms 尝试恢复，最多 7 秒，成功后清理快照。快照 30 分钟过期。
- 新标签/新窗口、修饰键点击、外部地址、非列表来源不写快照。`sessionStorage` 不可用时静默降级，导航本身不受影响。

---

## 35. 备份恢复维护闸门（2026-11-11）

- 恢复拿到进程内恢复互斥锁后进入维护状态；恢复期间除 `/health` 外所有 `/api/` 请求返回：
  - HTTP `503`；
  - `error.code = MAINTENANCE_MODE`；
  - `error.detail.retry_after_seconds = 5`；
  - 响应头 `Retry-After: 5`。
- `/health` 永远 200，用于监控和负载均衡探活。恢复请求本身在进入维护状态前已通过闸门，不能被自锁。
- 成功或异常都必须在 `finally` 中退出维护状态并释放恢复锁；否则一次失败恢复会让系统永久不可用。
- 前端只展示后端可读错误，不自行判断“维护中”。单进程部署下这是有效的全局闸门；未来多 worker 必须改为跨进程协调，当前不得声称已支持多 worker。

---

## 36. 资料治理筛选与列契约（2026-11-11）

- 管理员高级筛选仍保持“始终展开”的左侧窄标题轨 + 右侧字段网格。宽屏 4 列，≤1200px 2 列，≤560px 1 列；所有 label 与控件同基线，任一控件不得撑破容器。
- 字段顺序（2026-11-11 简化）：标签、生效开始、生效结束；管理员另含可见范围、排序、每页。查询日期、附件、文件类型、仅看无主文件、30 天内到期不再出现在高级筛选 UI。
- 排序白名单：最新创建（默认）、最近更新、创建时间、标题 A–Z、生效开始（新→旧）、失效日期（近→远）。每页 15/30/50/100；排序和每页属于显示设置，不计入高级筛选计数或筛选摘要。
- 列表列保持：选择（仅 admin）、标题、生效区间、状态、文件、操作。管理员新增选择列，不新增“更新日期”独立列；`updated_at` 用于“最近更新”排序与核对，不扩张表格宽度。
- 无失效日期的生效区间统一显示“开始日期 ~ 暂无失效日期”；`cover_from/cover_to` 使用包含关系：资料有效期完整覆盖所填起止日期，只填一端表示该日落在有效期内。筛选条件来自 URL 时，通过可移除的筛选摘要展示并允许单项清除。

---

## 37. 资料说明低调折叠与检索摘要（2026-11-11）

- 资料详情页的 `summary` 不再显示在标题下方；放在「关联通知」卡片**下面**，使用 `.doc-summary-disclosure` 默认收起。
- 折叠入口文案为「资料说明」，小字、灰色、无卡片头、无徽章、无彩色强调、无 emoji；展开后内容使用 `white-space: pre-wrap`，经 `escapeHtml` 纯文本渲染，最大高度内滚动。
- 管理员与普通用户都能看到折叠入口并展开；折叠不是权限隐藏，也不是删除内容。无说明时不渲染该块，避免多一行噪声。
- `summary` 继续参与 `GET /api/v1/documents?keyword=` 的 `title/summary LIKE` 搜索。管理员可手工粘贴正文关键词或内容摘要，普通用户搜索这些关键词即可找到资料。
- 本契约不代表全文内容检索：系统不自动打开 PDF / DOCX / XLSX / XLS 读取正文，不建全文索引，不引入 AI / RAG / 向量数据库。文件正文若未由管理员写入说明 / 摘要，仍不会被内容关键词命中。
- 全站资料搜索框统一为「搜索资料：标题、说明、摘要…」。编辑页字段仍叫「资料说明」，不改成醒目功能名；仅在 placeholder 中低调说明可粘贴检索文本。`summary` 前后端均限制 10000 字，避免超长文本拖慢搜索与详情页。

---

## 38. 通知正文搜索与关联资料候选（2026-11-12）

- 通知列表搜索框文案为「搜索通知：标题、正文…」；`GET /api/v1/notices?keyword=` 同时匹配 `notices.title` 与 `notices.content`。
- 正文仅指管理员在通知表单手填的纯文字正文；不读取通知文件 / 资料文件内部文字，不建全文索引，不引入 AI / RAG / 向量库。
- 新建/编辑通知页「关联资料」搜索框文案为「搜索资料：标题、说明、摘要…」，与资料列表的 `title/summary LIKE` 口径一致。
- 关联资料候选只由 `/api/v1/documents?keyword=` 服务端筛选；前端仅渲染、已选项置顶与保留勾选，不再对 `title` 做本地二次过滤，避免说明命中但标题不含关键词的资料被藏掉。
- 通知正文命中不改变阅读对象权限：普通用户仍受快照/已生效条件约束；管理员可见全部已发布通知。
