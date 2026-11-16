# API 契约（API.md）

> 项目：公司内部资料与通知管理系统 · v1
> 基础路径：`/api/v1`；页面路由用 Jinja2 模板（附录 A）
> 实现即本契约：字段名/状态码/错误码与下述逐一对应。
>
> **2026-11-08 锁定口径**：通知确认功能正式下线；资料下载按「有附件→原稿附件，无附件→当前主文件」；`effective_to` 为空统一表述为「暂无失效日期」。
>
> **2026-11-11 安全加固口径**：所有带 `Origin` / `Referer` 的非安全方法请求执行 CSRF 同源校验；`Host` / `X-Forwarded-Host` 不直接作为可信来源，只有命中 `TRUSTED_HOSTS` 且（转发头场景）显式开启 `TRUST_REVERSE_PROXY_HEADERS=true` 才允许。登录 `next` 与详情 `return_to` 只允许同源 `/documents`、`/notices` 及其 query；文件下载、附件与预览响应统一为 `Cache-Control: private, no-store`。

---

## 0. 通用约定

### 0.1 认证

- 认证方式：**Bearer token**（JWT，HS256）。
  - REST 客户端：请求头 `Authorization: Bearer <jwt>`。
  - 浏览器页面（同源）：登录成功后同时下发 HttpOnly Cookie `access_token=<jwt>`，页面与 fetch 自动携带；后端 `get_current_user` 同时接受请求头与 Cookie。
- 失败统一返回 `401 {"error":{"code":"UNAUTHORIZED","message":"..."}}`。
- **改密闸门（2026-10-16 起）**：`must_change_password=true` 的账号（首登/被重置密码）调用白名单（`/auth/me`、`/auth/change-password`、`/auth/logout`）以外的 `/api/` 端点，一律返回 `403 PASSWORD_CHANGE_REQUIRED`——持有效 JWT 也不能绕过改密。页面路由不受影响（浏览器侧由 base.html 的 meta refresh 跳转改密页）。
- 角色标注使用 `[user]/[admin]`（权限模型 v3：superadmin 已并入 admin）；未标注即任意登录用户。

### 0.2 响应/错误

- 成功：直接返回业务 JSON（成功码 200/201 等）。
- 失败：
  ```json
  { "error": { "code": "VALIDITY_CONFLICT", "message": "中文可读信息", "detail": { } } }
  ```
- 错误码全集见 DESIGN.md §8.2（`UNAUTHORIZED/ FORBIDDEN/ NOT_FOUND/ VALIDATION_ERROR/ VALIDITY_CONFLICT/ NOTICE_MODIFY_REQUIRES_REASON/ PASSWORD_CHANGE_REQUIRED/ FILE_TYPE_NOT_ALLOWED/ FILE_TYPE_MISMATCH/ FILE_TOO_LARGE/ DUPLICATE/ IN_USE/ CONFLICT/ BAD_REQUEST/ INTERNAL_ERROR/ DB_BUSY/ MAINTENANCE_MODE`）；登录/敏感操作限速返回 `RATE_LIMITED`（HTTP 429）；数据库连接池排队超时返回 `DB_BUSY`（HTTP 503，带 `Retry-After: 5`，客户端可重试）。
- **恢复维护闸门（2026-11-11）**：备份恢复执行期间，除 `/health` 外的 `/api/` 请求统一返回 HTTP 503：
  ```json
  { "error": { "code": "MAINTENANCE_MODE", "message": "系统正在恢复备份，请稍后重试",
               "detail": { "retry_after_seconds": 5 } } }
  ```
  响应头 `Retry-After: 5`。`/health` 始终放行；恢复入口在进入维护状态前已通过闸门，不会被自身拦截。

### 0.3 分页

列表接口统一支持 `?page=1&page_size=20`（`page>=1`，`1<=page_size<=100`，默认 20）。响应：

```json
{ "items": [ ... ], "total": 137, "page": 1, "page_size": 20, "pages": 7 }
```

### 0.4 时效状态（重复使用）

```json
{ "status": "active", "effective_from": "2026-01-01", "effective_to": null, "as_of_date": "2026-05-01" }
```
`status ∈ {pending, active, expired, archived}`。规则见 DESIGN.md §7.1（闭区间、可空 effective_to、按 as_of 重算）。

### 0.5 用户对象（出战）

```json
{ "id": 3, "username": "zhangsan", "display_name": "张三", "email": "z@example.com",
  "role": "user", "is_active": true, "department_ids": [1], "last_login_at": null }
```

---

## A. 页面路由（Jinja2 渲染，GET HTML）

| 方法/路径 | 角色 | 说明 | AGENTS |
|---|---|---|---|
| GET /login | 匿名 | 登录页（表单 POST 到 /api/v1/auth/login，成功写 Cookie 跳 /） | §23 |
| GET / | user+ | 工作台：待读通知数、有效资料数、快捷入口 | §1 |
| GET /dashboard | 任意 | **301 永久重定向到 `/`**（工作台别名；2026-09-15 加，此前为 404 裸 JSON）。外部常见猜法、登录页 `next` 参数会带它 | §1 |
| GET /documents | user+ | 资料列表页（筛选/搜索/状态） | §1、§14、§15 |
| GET /documents/{id} | user+ | 资料详情+预览入口+下载+关联通知（归档资料仅 admin） | §8、§12 |
| GET /notices | user+ | 通知列表（仅本人可见） | §17、§21 |
| GET /notices/{id} | user+ | 通知详情（打开即置读；确认功能已于 2026-11-03 下线） | §20 |
| GET /admin/documents/new、/admin/documents/{id}/edit | admin | 资料新建/编辑页 | §1 |
| GET /admin/notices/new、/admin/notices/{id}/edit | admin | 通知新建/编辑页 | §17 |
| GET /admin/notices/{id}/stats | admin | 阅读统计页 | §21 |
| GET /admin/users、/admin/departments | admin | 用户/部门管理页 | §23 |
| GET /admin/categories、/admin/tags、/admin/notice-types | admin | 分类/标签/通知类型管理页 | §9、§18 |
| GET /change-password | 任意已登录 | 强制/主动改密页（首登 must_change_password 时跳此页） | §23 |
| GET /admin/recycle-bin | admin | 回收站页 | §26 |
| GET /admin/audit-logs | admin | 操作日志页 | §25 |
| GET /admin/backups | admin | 备份恢复页 | §28 |

页面权限与对应 API 一致（后端强制，§33）；页面内 JS `fetch` 调 REST 渲染动态数据。

### A.1 列表返回导航 `return_to`（2026-11-11）

资料/通知详情页支持 `?return_to=<列表地址>`，详情页返回按钮据此恢复来源列表及其筛选条件：

- 仅允许同源相对路径 `/documents` 或 `/notices`，可保留 query；拒绝绝对 URL、协议相对 URL、fragment、控制字符、其他路径和超长值（>2048）。
- 非法值不报错，静默回落到资料列表 `/documents` 或通知列表 `/notices`；后端校验是开放重定向边界，前端不得自行放宽。
- 列表页进入详情时自动写入当前 `pathname + search`；返回时恢复 query 与滚动位置。滚动快照存于 `sessionStorage`，有效期 30 分钟；点击返回后最多等待 7 秒，让异步列表渲染到可恢复高度。
- `Ctrl/Cmd/Shift/Alt` 点击或新标签页打开不接管导航，不写返回快照。

---

## 1. 认证 Auth

### POST /api/v1/auth/login — 登录（§23、§25 登录审计）

权限：匿名
请求：
```json
{ "username": "zhangsan", "password": "secret" }
```
响应 200：
```json
{ "access_token": "<jwt>", "token_type": "bearer", "expires_in": 43200,
  "must_change_password": false,
  "user": { "id": 3, "username": "zhangsan", "display_name": "张三", "role": "user" } }
```
- `must_change_password=true`（初始/被重置密码未改）时，页面路由会强制 302 到 `/change-password`，改密前无法进入其它页面。
- 同源于浏览器：同时 `Set-Cookie: access_token=<jwt>; HttpOnly; Path=/; SameSite=Lax; Max-Age=43200`（生产加 `Secure`）。
- 校验：用户存在、`is_active=true`、密码匹配。失败：审计 `login_failed` + `401 UNAUTHORIZED`（不区分“用户不存在/密码错”）。
- 成功：写 `login` 审计，更新 `last_login_at`。停用用户：`403 FORBIDDEN` 并提示联系管理员。
- **单点登录（2026-09-03 拍板）**：每次登录自增 `users.token_version` 并写入 JWT
  `ver`；请求校验 `ver` 与列值不等 → `401 UNAUTHORIZED`（「账号已在其他设备登录，
  请重新登录」）——同账号旧设备全部下线。改密/管理员重置密码同样自增（改密
  接口会为当前设备重签新 Cookie，不掉线）。

### POST /api/v1/auth/logout — 登出（§25）

权限：登录
请求空；响应 200 `{"ok": true}`。
- 服务端无状态：清除 Cookie（`Set-Cookie: access_token=; Max-Age=0`）+ 审计 `logout`。

### GET /api/v1/auth/me — 当前用户（§23）

权限：登录
响应 200：见 §0.5 用户对象。

### POST /api/v1/auth/change-password — 修改本人密码（§23）

权限：登录
```json
{ "old_password": "old", "new_password": "new-strong-123" }
```
- 新密码强度校验（≥8 位含字母数字）。校验旧密码；`old 错 → 401`。成功改哈希、置 `must_change_password=0`、审计 `change_password`。响应 200 `{"ok": true}`。

---

## 2. 部门 Departments（§21、§25）

### GET /api/v1/departments — 部门列表
权限：登录（user 亦可读，用于选择）
Query：`is_active`、`keyword`、`ids`（可重复，如 `?ids=1&ids=2`）、`page/page_size`
响应 items：`{id, name, description, is_active, member_count?}`（2026-11-03 起移除 code）
- `ids` 用于按已知 id 小批量回填名称；和其他筛选条件同时传入时取交集。

### POST /api/v1/departments — 新建部门
权限：admin
```json
{ "name": "财务部", "description": "" }
```
- 重名 → `409 DUPLICATE`。成功 201 返回部门对象，审计 `create_department`。

### PUT /api/v1/departments/{id} — 修改部门
权限：admin
请求：同上（可部分）。
- 审计 `update_department`。

### DELETE /api/v1/departments/{id} — 删除部门（2026-11-09 用户确认）
权限：admin；管理页删除前有确认弹窗。
- 不再因有启用成员而拒绝；删除部门会解除全部 `user_departments` 关联，用户没有其他部门时显示「未分配部门」。
- 通知中该部门的 `notice_targets` 行摘除；资料可见范围中该部门的 `document_targets` 行也摘除（不移除资料、不把资料改成全体可见，避免权限被静默放大）。
- 通知本身、用户、资料、标签均不删除；响应含 `affected_member_count`、`notice_targets_removed`、`document_targets_removed`；部门名释放，可重建同名。
- 审计 `delete_department`（detail 含三个受影响计数）。

---

## 3. 用户管理 Users（§23、§25、§33）

### GET /api/v1/users — 用户列表
权限：admin
Query：`role`、`is_active`、`department_id`、`keyword`、`ids`（可重复，如 `?ids=3&ids=7`）、`page/page_size`
响应：`{items:[用户对象], total, ...}`（不含密码哈希；含 department_ids）
- `ids` 用于按已知 id 小批量回填姓名；和其他筛选条件同时传入时取交集。

### POST /api/v1/users — 新建用户
权限：admin
```json
{ "username": "lisi", "password": "init-pass-1", "display_name": "李四",
  "role": "user", "email": "l@example.com", "department_ids": [1,2] }
```
- `username` 重复 → `409 DUPLICATE`。
- 密码哈希入库，初始可置 `must_change_password=1`。审计 `manage_user`(详情)。

### GET /api/v1/users/{id} — 用户详情
权限：admin（本人可经 /auth/me）
响应：用户对象 + `departments:[{id,name}]`

### PUT /api/v1/users/{id} — 修改用户
权限：admin（不可改自己角色）
```json
{ "display_name": "李四四", "email": "...", "department_ids": [1], "is_active": true, "role": "admin" }
```
- 规则：不能停用自己；不能改自己角色；不能停用/降级最后一个启用的 admin。违者 `409 CONFLICT`。
- 审计 `manage_user`。

### DELETE /api/v1/users/{id} — 停用用户（软删除）
权限：admin
- 置 `is_active=false`；同上保护规则。审计。

### POST /api/v1/users/{id}/reset-password — 重置密码
权限：admin
```json
{ "new_password": "newpass123" }
```
- 成功后置 `must_change_password=1`。审计 `manage_user`(重置密码)。

---

## 4. 分类 Categories（§9、§25）

### GET /api/v1/categories — 分类列表（树/扁平）
权限：登录
Query：`parent_id`（省略=全部树形；指定=该父级下的平级列表，不含 `children`）、`with_document_count`
响应：
```json
{ "items": [ { "id":1, "name":"规章制度", "parent_id":null, "sort_order":0,
                "children":[ {"id":2,"name":"人事制度","parent_id":1,"sort_order":0,
                              "children":[ … 递归至四级 ] } ],
                "document_count": 5 } ] }
```
- `document_count`（`with_document_count=1` 时返回）：**自身 + 全部子孙**分类下的未归档资料数（文件夹语义），与 `GET /api/v1/documents?category_id=` 的命中集合同口径。
  **按当前登录用户的可见范围统计**（2026-11-04）：普通用户看到的数字只含
  audience 命中的资料，管理员为全库口径——否则会出现「计数 1 份、点进去为空」。

### POST /api/v1/categories — 新建分类
权限：admin
```json
{ "name": "考勤制度", "parent_id": 2, "sort_order": 0 }
```
- 深度 ≤4（一级~四级，2026-09 放宽）：沿祖先链计算，超限 `400 VALIDATION_ERROR`。
- 同级重名 → `409 DUPLICATE`。审计 `create_category`。

### PUT /api/v1/categories/{id} — 修改分类
权限：admin
请求：`{name, parent_id?, sort_order}`
- 重挂 parent 必须满足深度 ≤4 且**不成环**（不能挂到自身或其子孙之下）→ 违者 `400`。
- 审计 `update_category`。

### DELETE /api/v1/categories/{id} — 删除分类（删除整棵子树）
权限：admin；管理页删除前有确认弹窗（2026-11-09 用户要求；API 本身只校验 admin）
- 删除选中分类及其全部子孙分类（整棵子树，含历史停用行）；**不提升/保留子分类**。
- 树内所有资料（**含已归档资料**）的 `category_id` 置空为「未分类」；资料、文件、附件、
  标签（`document_tags`）、通知、通知类型（`notices.notice_type`）、通知-资料关联均不删除。
- **不做历史分类名快照**：已归档资料删除后同样显示「未分类」。
- 分类行物理删除，名字立即释放可重建同名。
- 响应新增：`deleted_category_ids`、`deleted_category_count`、`unclassified_document_count`。
- 审计 `delete_category`，`detail` 含 `root_id/root_name/parent_id/deleted_category_ids/`
  `deleted_category_count/unclassified_document_count`。
- 原 `DELETE /{id}/purge` 端点已随三段式语义一并移除（404）。
- 历史软删行由启动迁移清理：零引用行物理删除；仍有引用的行保留为不可见
  （列表只返回 `is_active=1`），同样不占名。删除分类时整棵子树内的历史停用行也一并删除。

---

## 5. 标签 Tags（§9、§25）

### GET /api/v1/tags — 标签列表
权限：登录
Query：`keyword`、`with_count`
响应 items：`{id, name, document_count?}`

### POST /api/v1/tags — 新建标签
权限：admin
```json
{ "name": "薪酬" }
```
- 重复 → `409 DUPLICATE`。审计 `create_tag`。

### PUT /api/v1/tags/{id} — 修改标签
权限：admin
请求：`{name}`；重名 → `409 DUPLICATE`。审计 `update_tag`。

### DELETE /api/v1/tags/{id} — 删除标签（2026-11-09 用户确认）
权限：admin；管理页删除前有确认弹窗。
- 不再因被资料引用而拒绝；删除会移除该标签的 `document_tags` 关联，资料进入「未打标签」状态。
- 资料本身、分类、文件、附件、通知、通知类型均不删除。
- 响应含 `affected_document_count`；审计 `delete_tag`（detail 含受影响资料数）。

---

## 6. 资料 Documents（§8、§10、§11、§12、§13、§14、§15、§16、§19、§25）

### GET /api/v1/documents — 资料列表 / 搜索（§1、§14、§15）
权限：登录
Query：
- `keyword`（title/summary LIKE）；2026-11-03 起 `doc_no` 已移除  - `summary` 是管理员手工维护的资料说明 / 内容摘要，可粘贴正文关键词或摘要用于搜索；系统**不自动解析 PDF/DOCX/XLSX/XLS 正文，不建立全文内容索引，不引入 AI/RAG/向量检索**（2026-11-11）。
- `category_id`（**含全部子孙分类**：文件夹语义，深度 ≤4，`category_service.subtree_ids`）、`tag_id`
- `uncategorized` / `untagged`（bool，整理视角：仅看未分类 / 未打标签）
- `status`：`pending|active|expired|archived|expiring`（归档资料仅 admin 可查到；默认用户层面**排除** archived）
  - `expiring`=即将到期：已生效且 `effective_to` 落在 `[as_of_date, as_of_date+30]`（窗口天数 `document_service.EXPIRING_WITHIN_DAYS`，与工作台「30天内失效」卡同一真值）
- `as_of_date: YYYY-MM-DD`（默认今天）——按该日重算 status（§15；资料列表高级筛选 UI 不暴露，但 API 与详情页保留）
- `cover_from` / `cover_to: YYYY-MM-DD`——资料有效期完整覆盖所填起止日期；只填一端表示该日落在有效期内；两端都填时必须 `cover_from <= cover_to`；`effective_to IS NULL` 按暂无失效日期处理（§13）。
- 列表治理筛选（2026-11-11）：`audience_scope=all|department|user`。已移除 `has_attachment/file_type/missing_main_file` 的前端入口；API 参数暂保留仅用于兼容既有链接，不作为界面承诺。
- `sort`：`id_desc`（默认）、`created_desc`、`updated_desc`、`title_asc`、`effective_from_desc`、`effective_to_asc`；不在白名单 → `400 VALIDATION_ERROR`
- `page/page_size`（`1<=page_size<=100`，默认 20；前端治理列表可选 15/30/50/100）
响应 items：
```json
{ "id": 1, "title": "员工手册2026", "summary": "...",
  "category_id": 2, "category_path": ["规章制度","人事制度"],
  "tag_ids": [3], "tags": [{"id": 3, "name": "人事"}],
  "effective_from": "2025-01-01", "effective_to": "2026-12-31",
  "has_file": true, "file_type": "pdf", "file_size_bytes": 204800,
  "has_attachment": false,
  "status": "active", "as_of_date": "2026-05-01",
  "audience_scope": "all", "audience_label": "全体可见",
  "archived": false, "updated_at": "2026-11-11T09:30:00",
  "created_by": 1, "created_at": "..." }
```
- `audience_scope/audience_label` 用于治理徽章；`has_attachment` 只由附件槽是否非空派生，列表不为 20 条额外加载附件关系。`updated_at`（2026-11-11 新增）供“最近更新”排序与变更核对。

### POST /api/v1/documents — 新建资料（元数据，可稍后传文件）（§13、§14、§16）
权限：admin
```json
{ "title": "员工手册2026", "category_id": 2, "summary": "...",
  "effective_from": "2025-01-01", "effective_to": "2026-12-31",
  "tag_ids": [3,5],
  "audience_scope": "all",              // all | department | user（2026-11-03 新增）
  "audience_department_ids": [],        // scope=department 时必填非空
  "audience_user_ids": [] }             // scope=user 时必填非空
```
- `effective_to` 可空或省略（=暂无失效日期；不表示永久有效，管理员仍可后续设置失效日期）。- `summary` 最多 10000 字；超过 → `400 VALIDATION_ERROR`。它是管理员手工维护的资料说明 / 内容摘要，不是系统自动抽取的文件正文。
- `audience_scope` 决定**普通用户是否能在列表/搜索中看到该资料**（2026-11-03）：
  - `all`（默认）：全体可见；
  - `department`：仅 `audience_department_ids` 内部门的成员可见；
  - `user`：仅 `audience_user_ids` 内的人可见；
  - 管理员**始终可见全部**，不受该字段限制（与通知的阅读对象一致）。
  - `scope=user` 的 `audience_user_ids` 只允许启用普通用户；传入管理员 ID → `400 VALIDATION_ERROR`。管理员默认可见全部资料，不作为可见目标写入。
  - 越权访问详情/文件返回 `404`（不泄露"存在但无权限"）；搜索与分页
    `total` 同样按可见性过滤。
- 校验 R1：`effective_from > effective_to` → `409 VALIDITY_CONFLICT`（消息含明确警告）。
- 成功 201：`{"id":1,"...":详情字段}`，审计 `create_document`（若带时效=create_document，不改时效不单独记）。

### GET /api/v1/documents/{id} — 资料详情（§1、§8、§19）
权限：登录（归档资料仅 admin → 否则 `404`/`403`）
响应：
```json
{ "id":1, "title":"员工手册2026", "summary":"...",
  "category": {"id":2,"name":"人事制度","parent":{"id":1,"name":"规章制度"}},
  "tags":[{"id":3,"name":"薪酬"}],
  "effective_from":"2025-01-01","effective_to":"2026-12-31",
  "current_file": { "id":10, "original_filename":"员工手册2026.pdf", "file_type":"pdf",
                    "size_bytes":204800, "sha256":"...", "uploaded_at":"..." } | null,
  "status": "active", "as_of_date": "2026-05-01",
  "linked_notices": [ { "id":5, "title":"手册废止通知", "notice_type":"废止",
                        "effective_at":"2026-12-31T00:00:00", "status":"published" } ],
  "rev_count": 2, "created_by":1, "created_at":"..." }
```
（`current_file=null` 表示无文件。）

### PUT /api/v1/documents/{id} — 修改资料元数据（§14、§16、§25）
权限：admin
```json
{ "title": "...", "category_id": 2, "summary": "...",
  "audience_scope": "department", "audience_department_ids": [1, 2],
  "effective_from": "2025-01-01", "effective_to": "2027-06-30",
  "confirm_overwrite": true, "modify_reason": "规则延长一年" }
```
- **时效改动规则（§16 R2）**：资料已存在时效窗口时修改：
  - 仅当新区间与旧区间重叠/包含，且未传 `confirm_overwrite` → `409 VALIDITY_CONFLICT` + 警告；
  - 传 `confirm_overwrite=true` → 允许，并强制审计 `modify_validity`（detail 含新旧值、reason）；
  - `effective_from > effective_to` → 同上，默认 `409`（R1）。
- 其余字段直改；审计 `update_document`。- `summary` 最多 10000 字；超过 → `400 VALIDATION_ERROR`；修改后会刷新列表缓存，搜索立即使用新说明 / 摘要。

### POST /api/v1/documents/{id}/file — 上传/替换文件（§8、§10、§11、§25）
权限：admin
请求：`multipart/form-data`
- 字段：`file`（文件；必填）。**同名 `file` 可重复出现**（多选上传）。
- 校验：扩展名白名单（.pdf/.docx/.xlsx/.xls + .jpg/.jpeg/.png/.gif/.webp）→ 魔数真实类型 → 大小 ≤ `MAX_UPLOAD_SIZE_MB` → 文件名清洗 → 服务端生成内部名 → SHA-256 → 落盘 `data/documents/<internal>`。
- 非法扩展名 `415 FILE_TYPE_NOT_ALLOWED`；伪造扩展名 `415 FILE_TYPE_MISMATCH`；超限 `413 FILE_TOO_LARGE`。
- **多图合并**（2026-11-04）：一次上传多个 `file` 时，**必须全部是图片**，服务端按上传顺序合成一份多页 PDF（每张图一页，页面尺寸跟随原图，长边 >2400px 等比缩小）作为当前文件。一份资料仍只有一个当前文件（§8）。混合类型 → `400 VALIDATION_ERROR`；单次上限 30 张；合并后超过大小上限 → `413 FILE_TOO_LARGE`；批次中任一图片无法解码 → `400`，整批拒绝、不留半成品。
  - 合并后的文件名（2026-11-06 起）：`<资料标题>（N张）.pdf`；标题清洗后为空时回退到 `首图名等N张图.pdf`。
- **主文件白名单不含 `.pptx`**（2026-11-06）：系统无法预览 PPTX，允许作主文件只会得到「上传成功但详情页无法预览」的坏状态。PPTX 请作为附件上传。
- **替换语义**：资料已有当前文件 → 旧文件移入 `data/recycle_bin/`、`file_records.status='recycled'`、写 `recycle_bin`（reason=replaced）；新文件成为 current_file；`rev_count+1`（§8 不覆盖、不物理删）。
- 响应 200：`{"current_file":{...}, "rev_count": 2, "recycled_file_id": 9}`，审计 `replace_file`。
  多图合并时额外返回 `{"merged": true, "merged_count": 3, "merge_note": "已把 3 张图片按顺序合成一份多页 PDF（每张一页）"}`。

### GET /api/v1/documents/{id}/file — 下载默认文件（§12、§33）
权限：**下载权限 = 查看权限**（2026-11-04 起）。能打开该资料的人即可下载默认文件；
不在阅读对象内 / 已归档（非 admin）→ `404`；归档资料仅 admin。

**取哪个文件（2026-11-08 锁定为方案 A，行为不变）**：
- 该资料有 active 附件 → 返回**原稿附件**（Word/PPT/Excel 等原件，供下载）；
- 无附件 → 返回当前主文件（行为与改动前完全一致，存量资料零影响）；
- 预览端点永远指向主文件，附件不参与在线预览；
- 不增加「下载当前预览文件」并列入口；如未来需要，必须重新走需求确认单。

- 返回 `FileResponse`：`Content-Disposition: attachment; filename*=UTF-8''<原始名>`；两个文件都没有 → `404 NOT_FOUND`。
- 预览端点（pdf / docx / xlsx / image）**永远指向主文件**，不受附件影响。
- 预览端点同样受归档与权限校验约束，预览不替代下载。

### 附件端点（2026-11-06）

资料可挂 **最多 1 个附件**。附件是「额外文件槽」，与主文件类型无关、**永不预览**、
不承担「必须与主文件对应」的语义。通知**不新增任何附件字段**（继续走关联资料）。

#### POST /api/v1/documents/{id}/attachment — 上传/替换附件
权限：admin
请求：`multipart/form-data`，字段 `file`（单文件）。
- 白名单：主文件 9 种 + `.pptx`（`ATTACHMENT_EXTENSIONS`）。
- 已有 active 附件 → 旧附件进回收站（`reason='replaced'`，落在
  `recycle_bin.attachment_file_record_id`），新附件成为 `documents.attachment_file_id`。
- 响应 200：`{"attachment_file": {...}, "recycled_file_id": 12}`，审计 `set_document_attachment`。
- 已归档资料 → `409 CONFLICT`。

#### DELETE /api/v1/documents/{id}/attachment — 删除附件
权限：admin
- 附件进回收站，`reason='attachment_deleted'`（**刻意不复用 `deleted`**：那个分支在恢复时
  会连带取消资料归档，删个附件把整份资料恢复出来是事故）。
- `documents.attachment_file_id` 置空；**资料归档状态不变**。
- 从回收站恢复 `attachment_deleted` 记录时，附件文件回库并重新挂回原资料；若资料已归档或已有当前附件，返回 `409 CONFLICT`，不静默覆盖。
- 无附件 → `404`；响应 200 `{"ok": true, "recycle_entry_id": 33}`，审计 `delete_document_attachment`。

#### GET /api/v1/documents/{id}/attachment — 下载原稿附件
权限：与主文件同口径（登录 + 可见性）。
- 强制取附件本身（不回落主文件）；无附件 → `404`。

> **2026-11-04 变更**：原 `documents.allow_download` 开关（控制普通用户「仅预览」）
> 已整体移除——它和「资料阅读对象」是两套并行的权限概念，同一份资料会出现
> 「谁能看」由 audience 决定、「谁能下」又由另一个开关决定的情况，两个入口语义打架。
> 现在统一为：**可见即可下载**。不想让人拿到原文，就不把他放进阅读对象。
> 数据库列保留（历史数据不销毁），代码不再读写；请求体里再传 `allow_download` 会被忽略。

### GET /api/v1/documents/{id}/preview/image — 图片浏览器内联（§12）
权限：登录
- 返回原始 `Content-Type`（`image/jpeg` / `image/png` / `image/gif` / `image/webp`）+ `inline`。
- 服务端**不做转码/缩放**（§12 预览不替代原件），浏览器直接渲染原图。
- 文件非图片 → `400 不可预览`；无文件 → `404`。

### GET /api/v1/documents/{id}/preview/pdf — PDF 浏览器内联（§12）
权限：登录
- 返回 `Content-Type: application/pdf`，`Content-Disposition: inline`。（浏览器内置查看器）
- 文件非 pdf → `400 不可预览` 提示；无文件 → `404`。

### GET /api/v1/documents/{id}/preview/docx — DOCX 结构化预览（§12）
权限：登录
- 响应（`text` 为兼容兜底，前端优先渲染 `blocks`）：
```json
{
  "content_type": "text",
  "text": "纯文本兜底（段落 + [表] 行）",
  "blocks": [
    {"type": "heading", "level": 1, "text": "标题", "runs": [{"text": "标题", "bold": true}]},
    {"type": "paragraph", "text": "正文", "runs": [{"text": "正文"}]},
    {"type": "list", "ordered": false, "level": 0,
     "items": [{"text": "条目", "runs": [{"text": "条目"}]}]},
    {"type": "table", "rows": [["A", "B"], ["1", "2"]]}
  ],
  "truncated": false
}
```
- 服务端 `python-docx` 提取；只返回文本/结构，**不返回 HTML**。前端用 DOM +
  `textContent` 渲染，runs 的 `bold/italic/underline` 仅作样式标记。
- `PREVIEW_DOCX_MAX_PARAS` 截断，`truncated=true`；原始文件不受影响，下载始终可用。
- 通知文件的 `/api/v1/notices/{id}/preview/docx` 使用同一契约。

### GET /api/v1/documents/{id}/preview/xlsx — Excel 只读预览（§12）
权限：登录
- Query：`sheet`（默认 0）、`max_rows`（默认 `PREVIEW_XLSX_MAX_ROWS`）
- 响应：
```json
{ "content_type":"table", "sheet_names":["Sheet1","Sheet2"], "active_sheet":0,
  "headers":["A","B"], "rows":[["v1","v2"]], "total_rows":123, "truncated":false }
```
- `.xls` 走同契约（`xlrd` 只读）。

### GET /api/v1/documents/{id}/status — 指定日期状态（§14、§15）
权限：登录
Query：`date=YYYY-MM-DD`（默认今天）
响应：`{"status":"expired","effective_from":"2025-01-01","effective_to":"2026-12-31","as_of_date":"2027-01-01"}`

### GET /api/v1/documents/{id}/notices — 关联通知（§19）
权限：登录
响应：`{items:[{id,title,notice_type,status,effective_at}]}`

### DELETE /api/v1/documents/{id} — 归档/删除资料（软，§26、§25）
权限：admin
- 请求体可选：`{"reason":"不再使用"}`。
- 行为：置 `archived_at/by/reason`；当前文件 `status='recycled'` 移入回收站，写 `recycle_bin`(reason=archived或deleted)；普通用户列表中不可见、不可访问。
- 审计 `archive_document`/`delete_document`。**不物理删除**。

### POST /api/v1/documents/{id}/restore — 恢复归档资料（§26、§25）
权限：admin
- 行为：清 `archived_at`、文件 `status='active'` 移回 `data/documents/`、`recycle_bin.restored_at/by` 置位。
- 审计 `restore_document`。

### POST /api/v1/documents/batch-actions/{action_key}/preview — 预览批量动作（2026-11-11）

权限：admin。预览只解析选择范围并计算影响，不写业务数据、不写审计。

`action_key`：

| action_key | `params` | 说明 |
|---|---|---|
| `set_category` | `{"category_id": 3}` | 统一归入一个未停用分类 |
| `clear_category` | `{}` | 清为未分类 |
| `add_tags` | `{"tag_ids": [1,2]}` | 幂等追加多个标签 |
| `remove_tags` | `{"tag_ids": [1,2]}` | 只解除资料标签关联，不删标签 |
| `set_audience` | `{"scope":"all|department|user","department_ids":[],"user_ids":[]}` | 设置可见范围；department/user 的 id 列表须非空；`scope=user` 不接受管理员 ID |
| `archive` | `{"reason":"可选"}` | 批量软删到回收站，主文件与附件共用一条回收站记录 |
| `restore` | `{"reason":"可选"}` | 只恢复已归档资料；未归档项逐条跳过 |

选择范围 `selection`：

```json
{ "mode": "ids", "ids": [1, 2], "filters": {}, "exclude_ids": [] }
```

```json
{ "mode": "filter",
  "ids": [],
  "filters": {
    "keyword": "手册", "category_id": 2, "tag_id": 1,
    "status": "active", "as_of_date": "2026-11-11",
    "cover_from": null, "cover_to": null,
    "uncategorized": false, "untagged": false,
    "audience_scope": "all"
  },
  "exclude_ids": [8] }
```

- `mode=ids`：服务端去重并排序，只保留仍符合动作可用状态（归档动作排除已归档；恢复动作只保留已归档）。
- `mode=filter`：只接受上述筛选键；服务端用资料列表同一套筛选条件解析全集，再扣除 `exclude_ids`。未知筛选键 → `400 VALIDATION_ERROR`。
- `expected_count` 可选；预览时若与后端计算的 `affected_count` 不一致 → `409 CONFLICT`。

请求与响应示例：

```json
{ "selection": {"mode": "ids", "ids": [1,2]},
  "params": {"category_id": 3}, "expected_count": 2 }
```

```json
{ "preview_token": "opaque-token",
  "action_key": "set_category",
  "matched_count": 2, "affected_count": 2, "skipped_count": 0,
  "skipped": [], "warnings": [], "old_counts": {"未分类": 2},
  "expires_in": 300 }
```

- `preview_token` 只在当前进程内存中保存，**5 分钟**有效；服务重启后失效。`skipped`/`warnings` 最多回传前 50 条供界面展示。
- `set_audience` 造成全体→受限或受限→全体时返回 `warnings`；`warnings` 不阻塞执行，但提示权限范围变化。
- 非 admin → `403 FORBIDDEN`；参数/分类/标签/可见范围非法 → `400 VALIDATION_ERROR`。

### POST /api/v1/documents/batch-actions/{action_key}/execute — 执行批量动作（2026-11-11）

权限：admin。请求必须携带同一动作、同一选择、同一参数和预览返回的 token：

```json
{ "selection": {"mode": "ids", "ids": [1,2]},
  "params": {"category_id": 3}, "expected_count": 2,
  "preview_token": "opaque-token" }
```

响应：

```json
{ "batch_id": "opaque-token", "action_key": "set_category",
  "matched_count": 2, "affected_count": 2,
  "skipped": [], "failures": [] }
```

- 执行前重新解析选择集合；token 被使用/过期、用户、动作、选择、参数或集合变化、`expected_count` 变化 → `409 CONFLICT`（“预览已失效/预览后资料集合发生变化，请重新预览”）。前端必须重新预览，不得直接重试执行。
- 某一项在逐条执行时失败进入 `skipped`，不中断其他项；当前实现 `failures` 固定为空数组，保留字段兼容后续扩展。
- 归档/恢复走既有单条语义：归档时资料与主文件/附件共用回收站记录；恢复时两者一起恢复，不能只恢复其中一个。
- 审计：改分类/标签/可见范围执行成功后写一条 `update_document` 汇总审计，`detail` 含 `bulk=true`、`batch_id`、`action_key`、`selection`、`params`、`requested_count`、`matched_count`、`affected_count`、`skipped`、`changes`（最多 200 条）；预览不写审计。归档/恢复为保留档案语义，继续逐条复用 `archive_document` / `restore_document` 审计。
- **无迁移**：本节端点、筛选和审计字段不新增表/列，不需要执行迁移脚本，也没有历史数据回填。

---

## 7. 通知 Notices（§17、§18、§19、§20、§21、§22、§25）

### GET /api/v1/notices — 通知列表（§17、§20、§21）
权限：
- user：返回**状态为 `published` 且（effective_at 为空或 ≤ now）**且 **target 包含本人**的通知；每项附本人 `read_at`（`null`=未查看）。
- admin：全部（含 cancelled；草稿状态已于 2026-11-04 下线）。
Query：`type`（`__unclassified__` = 未分类通知）、`unread_only`（仅看未读，2026-11-03 起这是唯一的「待处理」维度）、
`status`、`keyword`（`title/content LIKE`，匹配标题或纯文字正文；2026-11-12）、`date_from`/`date_to`（按创建时间，**date_to 为纯日期时含当日全天**）、`page/page_size`
- `keyword` 只查通知表里管理员手填的标题与正文；通知文件内的文字不解析、不建立全文内容索引、不引入 AI/RAG/向量检索。普通用户仍只能搜到自己是阅读对象且已生效的通知，范围外通知不会因正文命中被泄露。
响应 items：
```json
{ "id":5, "title":"废止员工手册旧版", "notice_type":"废止", "status":"published",
  "published_at":"2026-05-01T09:00:00", "effective_at":"2026-12-31T00:00:00",
  "has_file": true,
  "my_read_at": null,
  "my_read_status": "unread",
  "read_stats": {"target": 32, "viewed": 18} }
```
`my_read_status ∈ {unread, read}`（2026-11-03 起确认功能下线）；`is_target`：当前用户是否该通知阅读对象（快照行存在）；`read_stats` 仅 admin 响应附带（本页通知的一次聚合，供列表行内已查看率条）。
- **阅读口径三处一致**：`read_stats.target` = 统计页 `target_user_count` = 点名册 `/audience` 条数，
  均只计**启用且非管理员**的应阅读用户（§21 + 2026-11-03 决策）。停用账号与管理员都不计入分母，
  也不出现在名单里——管理员能看全部通知是权限，不代表被要求阅读。

### POST /api/v1/notices — 新建通知（§17、§18、§21、§25）
权限：admin
请求：`multipart/form-data` 或 JSON：
```json
{ "title": "废止员工手册旧版",
  "content": "即日起废止……",              // 允许空（纯文件）或与文件并存
  "notice_type": "废止",                    // 可选；省略或 null = 不选类型
  "effective_at": "2026-12-31T00:00:00",  // 生效时间，可空=创建即生效
  "document_ids": [1, 2],
  "target": { "scope": "all" }            // 或 {"scope":"department","department_ids":[1]}
                                          // 或 {"scope":"user","user_ids":[3,7]}
  "file": <可选文件>                        // 通知原始文件（走同一文件安全管线，owner_type='notice'）
}
```
- `notice_type` **非必填**（2026-11-03 决策）：省略/null/空串表示不选类型，不触发废止·替换联动。
  填写时必须存在于自定义词表 `notice_types` 且未停用（种子六类：发布/修订/替换/废止/延期/其他，可由管理员增删），否则 `400 VALIDATION_ERROR`。
- target 校验：scope 需 `all|department|user` 且对应 id 列表非空（department/user 时）；不能同时为空（标题+（正文|文件）至少其一）。
- **创建即发布**：状态为 `published`，`published_at=now`，同一事务内物化阅读快照
  并执行废止/替换联动；任一步失败整体回滚（不会留下半成品通知）。
  阅读对象为空 → `400 VALIDATION_ERROR`。审计 `create_notice` + `publish_notice`。

### GET /api/v1/notices/{id} — 通知详情（§20、§21）
权限：user+（admin 可见全部；user 仅本人 target 内且已到生效时间，否则 `403 FORBIDDEN`）
响应：
```json
{ "id":5, "title":"...", "content":"...", "notice_type":"废止", "status":"published",
  "published_at":"...", "effective_at":"...",
  "has_file":true, "file_type":"pdf",
  "documents":[{"id":1,"title":"员工手册旧版","archived":false}],
  "audience_meta":{"scope":"all","department_ids":[],"user_ids":[],
                   "department_names":[],"user_names":[]},
  "my_read_status":"unread", "in_audience":true }
```
- `documents[].archived`：该关联资料是否已进回收站（admin 视角返回全量，普通用户只见未删除的）。
  编辑页据此**只剪已失效的关联**——不得按"不在候选列表里"就丢弃选中项（候选池分页有限，那样会在保存时静默丢关联）。
  提交携带已删除资料 → `400 VALIDATION_ERROR`（提示先在回收站恢复）。
- `audience_meta`：`*_ids` 供编辑表单回填；`*_names` 为展示用姓名/部门名
  （display_name 优先），已删除对象查不到时对应名称缺位、前端按 ID 兜底。
- `in_audience`：当前用户是否该通知的阅读对象（快照行存在**或**按对象规则命中，与
  `_require_user_target` 门禁同口径；仅详情响应附带）。非对象用户（如范围外管理员）
  打开详情不置读、不显示「未查看」徽章。
- 此接口**不自动**置读（阅读在 POST /read 显式记录，§7.6 DESIGN）。

### PUT /api/v1/notices/{id} — 修改通知（§22、§25）
权限：admin
```json
{ "title":"...", "content":"...", "notice_type":"...", "effective_at":"...",
  "document_ids":[1,2],
  "target": {...}, "confirm_modify": true, "modified_reason": "正文纠正错别字" }
```
- 草稿状态已下线（2026-11-04），不存在「发布前可随意修改 target」的阶段。
- **published** 且存在任何 `read_at`：
  - 必须传 `confirm_modify: true` **且** `modified_reason` 非空，否则 `409 NOTICE_MODIFY_REQUIRES_REASON`；
  - 记录 `updated_by/updated_at/modified_reason` + 审计 `update_notice`（含修改摘要）。
  - `published` 状态通知**禁止**直接改 `target.scope` 收窄范围（已产生阅读快照）；如需扩范围请官方“发送补充通知”（第一版不支持，文档记录限制）。
- 审计 `update_notice`。

### POST /api/v1/notices/{id}/cancel — 取消通知（§26 软取消、§25）
权限：admin
请求：`{"reason":"已用新通知替代"}`
置 `status='cancelled'`、`cancel_reason`；用户不可见。审计 `cancel_notice`。发布态取消不删 `notice_reads`（保留统计证据）。

### POST /api/v1/notices/bulk-cancel — 批量移入回收站（2026-11-11）

权限：admin。

```json
{ "notice_ids": [5, 6, 6], "reason": "批量整理" }
```

响应：

```json
{ "requested": 2,
  "succeeded": [{"ok": true, "id": 5, "title": "通知 A", "status": "cancelled"}],
  "skipped": [{"id": 6, "reason": "通知已在回收站"}] }
```

- id 去重且保留首次出现顺序；逐条使用 savepoint，单条不存在/已在回收站等失败不中断其他条，进入 `skipped`。
- 每条复用单条取消语义：废止/替换联动回滚、保留阅读快照；成功后另写一条 `cancel_notice` 汇总审计（`detail.batch=true`、`via=notice_bulk_cancel`、`requested/succeeded/skipped/reason`）。
- 空 `notice_ids` → `400 VALIDATION_ERROR`；仅 admin，普通用户 → `403 FORBIDDEN`。

### POST /api/v1/notices/{id}/read — 标记已查看（§20）
权限：user+（admin 阅读自己也算；仅限 target 内 + `published` 状态）
请求：`{}`
- 置 `read_at`（首次覆盖，幂等）；审计 `read_notice`（轻量，可批量/异步，但第一版写库即可）。
响应 200：`{"my_read_at":"2026-05-01T12:00:00"}`；非目标/未发布 → `403 FORBIDDEN`。

### GET /api/v1/notices/{id}/statistics — 阅读统计（§21）
权限：admin
响应：
```json
{ "notice_id":5,
  "target_user_count":100,
  "viewed_count":82, "unviewed_count":18,
  "viewed_rate":0.82,
  "by_department":[ {"department_id":1,"name":"财务部","target":20,"viewed":18,"unviewed":2} ] }
```
- 仅统计 `notice_reads` 快照行（"实际需阅读人员"，§21），并**排除管理员**（2026-11-03 决策）。
- 【2026-11-08 锁定】确认相关字段（`requires_confirmation`/`confirmed_count`/`unconfirmed_count`/`confirmed_rate`）
  已随通知确认功能正式下线移除；阅读统计只含「应阅读 / 已查看 / 未查看」。

### GET /api/v1/notices/{id}/audience — 应阅读名单（§21）
权限：admin
Query：`page/page_size`、`read_status: all|read|unread`、`keyword`（按姓名/账号/部门模糊搜索）、`department_id`
响应：`{items:[{user_id,username,display_name,department_names,read_at}], department_id, ...分页字段}`
- `department_id`（2026-11-11 新增）：按部门下钻，只返回该部门成员；多部门用户会在各对应部门名单中出现。过滤在分页前完成，并回显在响应的 `department_id`。
- 统计页默认以 `read_status=unread` 打开（管理员最关心"谁还没看"），大数字卡可直接切换筛选。
- `keyword` 在分页前过滤，保证 `total` 与页内容一致。

### GET /api/v1/notices/{id}/documents — 关联资料（§19）
权限：登录（user 可见，资料归档则按资料规则）
响应：`{items:[{id,title,status(as_of_date)}]}`
> **2026-11-16**：非管理员返回的关联资料还须满足**资料自身可见范围**
> （`audience_scope` / `document_targets`），与 `/documents/{id}` 一致。
> 此前只过滤归档，导致「通知发给全体、关联了仅某部门/某人可见的资料」时，
> 其他普通用户能从关联列表拿到该资料的 id/标题/状态，而直接访问该资料是 404。
> 管理员保持全量视角。

### GET /api/v1/notices/{id}/file — 下载通知原始文件（§17、§12、§33）
权限：user+（同上可见性校验）
返回附件下载；无文件 `404`。
其他预览端点（/preview/pdf|docx|xlsx|image）与资料同构，鉴权同可见性规则。

> **2026-11-04 变更**：`DELETE /api/v1/notices/{id}` 已移除——草稿状态下线后，
> 删除通知统一走 `POST /api/v1/notices/{id}/cancel`（进回收站，可恢复）。

---

## 8. 回收站 Recycle Bin（§26、§25）

### GET /api/v1/recycle-bin — 回收站列表
权限：admin
Query：`reason`(replaced|archived|deleted)、`page/page_size`
响应 items：
```json
{ "id":1, "reason":"replaced", "document_id":1, "document_title":"员工手册旧版",
  "file_record_id":10, "original_filename":"员工手册2020.pdf", "file_type":"pdf",
  "size_bytes":123456, "deleted_at":"...", "deleted_by":1, "deleted_username":"admin",
  "restored_at": null }
```

### POST /api/v1/recycle-bin/{id}/restore — 恢复（§26）
权限：admin
- reason=replaced：文件回 `data/documents/`、`status='active'`（覆盖语义：恢复的文件成为“历史”，不顶替当前 current_file——设计决策：**恢复只回文件库供管理员另行处理，不回设为当前文件**，避免与当前版本冲突）。
- reason=archived/deleted：恢复资料整条（清 archived_at、文件回 documents/、current_file_id 恢复指向）。
- 审计 `recycle_restore`。

### DELETE /api/v1/recycle-bin/{id} — 永久删除（§26，二次确认，仅 admin）
权限：admin
```json
{ "confirm": true }
```
- 不含 `confirm: true` → `400` 要求二次确认；物理删除文件副本 + 软标记（`purged_at/by`）+ 审计 `recycle_purge`。
- 这是**唯一**允许物理删除文件的入口。

---

## 8b. 批量整理 / 通知类型词表 / 回收站补记（§9、§18、§26）

### POST /api/v1/documents/bulk — 批量整理资料
权限：admin
请求：`{ "document_ids": [1,2], "set_category_id": 3, "clear_category": true, "add_tag_id": 5, "archive": true }`
- 至少给一项操作；`set_category_id` 与 `clear_category` 互斥 → `400 VALIDATION_ERROR`。
- 逐条判定、不因个别失败中断：改分类/加标签返回 `{ "updated": n, "skipped": [{"id":7,"reason":"已删除（回收站中），请先恢复"}] }`；
  `archive=true` 返回 `{ "archived": n, "skipped": [...] }`。审计 `update_document`（`detail.bulk=true`，含 ids 与操作参数）。

### 通知类型词表 `/api/v1/notice-types`
- `GET`（登录）：`?keyword=`、`?with_count=1` → `items: [{id,name,sort_order,is_active,notice_count?}]`。
  `notice_count` **含回收站中的通知**；删除普通类型后，这些通知会变为未分类。
- `POST`（admin）：`{name, sort_order}`；名称重复 → `409 DUPLICATE`；审计 `create_notice_type`。
- `PUT /{id}`（admin）：`{name?, sort_order?}`。
  - **改名在同一事务内级联**更新 `notices.notice_type`（该列存名称字符串），审计
    `update_notice_type` 的 `detail` 记录 `rename_from` 与 `cascaded_notices`（受影响条数），可据此人工回写。
  - 内置联动类型「废止/替换」按名称驱动资料时效联动，**改名一律 `409 CONFLICT`**（否则会静默断开联动）。
- `DELETE /{id}`（admin）：管理页删除前有确认弹窗；普通类型物理删，已有通知的 `notice_type` 置空为「未分类」，响应含 `affected_notice_count`；通知本身不删。
- 「废止 / 替换」是资料时效联动类型，删除 → `409 CONFLICT`，避免破坏自动失效联动。审计 `delete_notice_type`。

### 回收站 `/api/v1/recycle-bin`（全部仅 admin）
- `GET`：文件条目列表，`?reason=replaced|archived|deleted&page&page_size`。
- `POST /{entry_id}/restore`：恢复条目（文件回到在用）。审计 `recycle_restore`。
- `DELETE /{entry_id}`：永久删除单条，请求须带 `{"confirm": true}`（单次确认）。审计 `recycle_purge`。
- `GET /notices`：已删除通知列表；item 的 `read_count` = **已查看人数**（只算启用用户，与阅读统计同口径）。
- `POST /notices/{notice_id}/restore`：恢复通知（恢复为 published，保留原创建时间与阅读统计；废止/替换重新联动资料失效）。
- `POST /notices/bulk-restore`（2026-11-11）：批量恢复回收站通知。
  ```json
  { "notice_ids": [5, 6, 6] }
  ```
  响应 `{"requested":2,"succeeded":[{"ok":true,"id":5,"title":"...","status":"published"}],"skipped":[{"id":6,"reason":"仅回收站中的通知可恢复"}]}`。
  id 去重；每条使用 savepoint，单条失败不影响其他条；恢复成功后重新应用废止/替换联动。写一条 `recycle_restore` 汇总审计（`detail.via=notice_bulk_restore`、requested/succeeded/skipped）。
- `DELETE /notices/{notice_id}`：彻底删除通知（`confirm=true`）。
- `POST /purge-all`：清空**全部**未处理条目（**不认列表的 reason 筛选**——前端确认框须显示未筛选总数）。
  请求须带 `{"confirm": true}`，缺失 → `400 BAD_REQUEST`（2026-10-29 补齐：此前仅靠前端弹窗）。
  响应 `{ "files_purged": n, "notices_purged": m }`；写一条汇总审计 `recycle_purge`（`object_label="purge_all"`）。

---

## 9. 操作日志 Audit Logs（§25、§33）

### GET /api/v1/audit-logs — 日志查询
权限：admin
Query：`user_id`、`action`、`object_type`、`date_from/date_to`、`keyword`(object_label/detail)、`page/page_size`
- `date_to` 为纯日期（`YYYY-MM-DD`）时按**含当日全天**处理；显式带时间则按该时刻截断（与通知列表同口径）。
- `object_type` 实际取值：`document/notice/user/department/category/tag/backup/recycle_bin/notice_type/audit_log`。
响应 items：
```json
{ "id": 100, "user_id": 1, "username": "admin", "action": "publish_notice",
  "object_type": "notice", "object_id": 5, "object_label": "废止员工手册旧版",
  "detail": "{\"target_user_count\":100}", "ip": "10.0.0.8",
  "user_agent": "Mozilla/5.0", "created_at": "..." }
```

### GET /api/v1/audit-logs/{id} — 单条日志
权限：admin

### DELETE /api/v1/audit-logs/{id} — 删除单条日志（物理删）
权限：admin
- 删除动作本身写一条汇总审计（`action=audit_delete`，含被删 id 清单与操作人）→ 非无痕（§25 补充决策）。

### POST /api/v1/audit-logs/batch-delete — 批量删除日志
权限：admin
请求：`{ "ids": [1,2,3] }`
- 单次确认覆盖整批；同样写 `audit_delete` 汇总审计。
- **不提供**"按当前筛选全删/清空全部"入口（防误删整本日志）。

---

## 10. 备份/恢复 Backup & Restore（§28、§25）

### POST /api/v1/backups — 新建备份
权限：admin
```json
{ "type": "full" }   // full=数据库+文件（默认）；database=仅数据库快照
```
- 同步执行（第一版数据量小）：生成 `data/backups/backup_<ts>/`（manifest.json + app.db + files/），写 `backup_records`（`source=manual`）。
- 响应 201：`{"id":1,"backup_type":"full","backup_path":"backups/backup_...","size_bytes":...,"file_count":12,"db_sha256":"...","status":"completed","source":"manual","created_at":"..."}`
- 失败：`500` + 记录 `status='failed'`；审计 `backup_create`。

- **磁盘闸门（2026-10-26 加）**：可用空间不足（`DISK_MIN_FREE_MB` 绝对下限或 `DISK_MIN_FREE_PERCENT` 相对下限任一不满足）→ `400 VALIDATION_ERROR`，且不创建任何备份目录。
- `400` 磁盘空间不足；`409 CONFLICT` 恢复正在执行（备份与恢复共用互斥锁）；巡检结果写日志。

### GET /api/v1/backups — 备份列表
权限：admin
Query：`type`、`page/page_size`
响应 items：同上（不含敏感内容），每条含 `source`（`manual`=手动 / `auto`=调度自动备份）。
每条另含 `created_by_username` / `restored_by_username`（操作人用户名解析结果；
对应用户已被物理删除时为 `null`，前端回退显示原 id）。
响应另含 `policy`（保留策略回显）：
```json
{
  "policy": {
    "schedule": "sat 03:00",          // null=自动备份未启用
    "next_run_at": "2026-09-05T03:00:00",
    "keep_days": 90,                   // null=不按天数清理
    "keep_count": null,                // null=不限份数
    "pre_keep_count": 3
  }
}
```
保留清理语义：每次备份/恢复成功后执行——删除超过 `BACKUP_KEEP_DAYS` 天或超出
`BACKUP_KEEP_COUNT` 份的备份集（**记录行真删除**，目录删除，审计 `backup_delete`
带 `auto_cleanup`）；`*.pre_<时间戳>` 恢复留旧目录按组保留最近
`BACKUP_PRE_KEEP_COUNT` 组。清理只碰 `data/backups/backup_<数字时间戳>` 与
`*.pre_<数字时间戳>` 白名单目录；备份动作停止时清理随之停止（宁多留不误删）。
调度配置：`BACKUP_SCHEDULE`（如 `sat 03:00`/`daily 03:00`，留空=关闭）。

**孤儿备份目录认领（2026-09-15）**：恢复会用快照覆盖整个 `app.db`，因此"快照之后
创建的备份"其记录行随回滚消失、磁盘目录仍在 → 成为列表看不见、保留策略也管不到的
孤儿。启动迁移 `_migrate_reclaim_orphan_backups` 自动**认领**（非删除）：扫描
`backups/` 下严格匹配 `backup_<时间戳>` 白名单的目录，从 `manifest.json` 重建
`completed` 记录，使其重回列表、重回保留策略管辖、可再次用于恢复。审计
`backup_reconcile`（detail 含 `reclaimed` 与 `skipped` 清单）。
安全边界：已被引用的目录不动；manifest 损坏 → 跳过；`app.db` 缺失或为空 → 跳过
（认领出来也不能恢复，只会造成"可恢复"假象）。幂等，每次启动兜底。

### GET /api/v1/backups/{id} — 备份详情
权限：admin
响应：记录 + `manifest.json` 摘要（文件数、各类 sha256、schema 版本）。

### POST /api/v1/backups/{id}/verify — 只读校验备份（2026-11-11）

权限：admin。校验备份是否可用于恢复，不恢复、不修改备份目录；每次校验写 `backup_verify` 审计（含 `ok/errors/checks`）。

响应 200（即使 `ok=false` 也返回 200，由界面展示失败项）：
```json
{ "id": 12, "backup_type": "full", "backup_path": "backups/backup_20261111093000",
  "checked_at": "2026-11-11T10:00:00",
  "ok": false,
  "checks": {
    "manifest": {"ok": true, "message": "manifest 校验通过", "errors": []},
    "db_sha256": {"ok": true, "message": "数据库 sha256 校验通过", "actual": "...",
                   "expected_manifest": "...", "expected_record": "...", "errors": []},
    "file_sha256": {"ok": false, "message": "文件 sha256 校验失败",
                     "checked": 12, "failed": ["documents/ab/xx.pdf"]},
    "sqlite_integrity_check": {"ok": true, "message": "SQLite 完整性校验通过", "result": "ok"}
  },
  "errors": ["校验失败: documents/ab/xx.pdf"] }
```
- 固定检查项：manifest 结构/版本/类型、数据库与清单 sha256、全部文件 sha256、SQLite `PRAGMA integrity_check`（只读打开）。
- 备份记录不存在 → `404 NOT_FOUND`；记录路径越界或非备份目录白名单 → `400 VALIDATION_ERROR`。
- 普通用户 → `403 FORBIDDEN`。本端点不参与“恢复消费备份”流程，也不会删除备份。

### POST /api/v1/backups/{id}/restore — 恢复（§28）
权限：admin（强烈建议先自动备份）
```json
{ "confirm": true, "reason": "误操作回滚" }
```
流程（DESIGN §10.2）：
1. `confirm` 缺省/false → `400`；
2. **先自动生成当前状态安全备份**；
3. 校验 manifest + sha256（库指纹）；
4. 以“临时目录就绪 → 数据库在线回写（不换文件 inode，兼容 Windows 句柄占用）
   + 业务目录切换 → 留旧”恢复库与文件；
5. **恢复完成后已用备份直接删除**（记录行 + `backups/backup_*` 目录一并清理，
   2026-09-03 拍板——该备份已被消费无再次恢复价值；回滚依赖恢复前安全备份
   与 `*.pre_<ts>` 留旧组）；审计 `backup_restore`（`detail.used_backup_deleted=true`）。
- 校验失败 → `500` 且**不进行恢复**（保持现状）。
- **并发保护（2026-10-26 加）**：恢复是「换库 + 切业务目录」的多步破坏性操作，
  同一时刻只允许一个恢复执行。已有恢复在跑时，本次请求 → `409 CONFLICT`
  （"已有恢复操作正在执行"）。此前无互斥，实测两个并发恢复**都返回 200**，
  会交错执行导致数据处于混合状态（静默损坏）。
- **维护闸门（2026-11-11）**：拿到恢复互斥锁后进入维护状态，恢复期间除 `/health` 外所有 `/api/` 请求返回 `503 MAINTENANCE_MODE` + `Retry-After: 5`；恢复入口本身在进入闸门前已通过，不会被自锁。成功/失败均在 `finally` 退出维护并释放锁。
- 响应 `{"ok":true,"id":...,"status":"restored","restored_at":...,"safety_backup_id":...}`。

### DELETE /api/v1/backups/{id} — 删除备份（可选）
权限：admin；`{ "confirm": true }` 二次确认 → **真删除**（物理删备份目录 + 备份记录行，
列表不再显示；2026-08-30 拍板，不做 purged 软标记）；审计 `backup_delete`。
重复删除同一 id → `404`（记录已不存在）。保留策略清理（见上）同样真删除记录行。

---

## 10.5 工作台 Dashboard

### GET /api/v1/dashboard — 工作台聚合统计（5 秒 TTL 缓存，写操作显式失效）
权限：登录
响应（全员字段）：
```json
{ "document_count": 156, "active_document_count": 140,
  "notice_count": 8, "my_unread_notices": 2 }
```
- `my_unconfirmed_notices` 已随通知确认功能正式下线移除；「待处理」只剩 `my_unread_notices`。
- `document_count` / `active_document_count` **按当前用户可见范围统计**（2026-11-04）：
  普通用户只计 audience 命中的资料，与资料列表 `total` 严格相等；管理员为全库口径。

响应（仅 admin 追加）：
```json
{ "last_backup_at": "2026-08-27T20:53:30", "backup_stale_days": 7 }
```
- `last_backup_at`：最近一次成功备份时间（`status ∈ {completed, restored}`），`null`=从未备份。
- `backup_stale_days`：判定备份"过期"的阈值（调度周期 + 1 天）。
- **2026-11-03 移除**：`uncategorized_count` / `untagged_count` / `expiring_30d_count` /
  `draft_notice_count` 已随草稿状态一并移除（2026-11-04）。

---

## 11. API 与 项目规范 章节映射速查

| 端点组 | AGENTS 章节 |
|---|---|
| /auth（登录/登出/me/改密） | §1(员工登录)、§23、§24(预留)、§25(登录审计) |
| /users、/departments | §21(部门对象)、§23、§25(用户管理)、§33(权限) |
| /categories、/tags | §1(分类/标签查找)、§9、§25 |
| /documents（CRUD/列表/搜索） | §1、§13、§14、§15、§16、§25 |
| /documents/*/file（上传/替换） | §8、§10、§11、§12、§25、§26 |
| /documents/*/preview/*（预览） | §12（PDF 内联/DOCX 结构化/Excel 只读 + 下载并存） |
| /documents/*/status | §14、§15（历史日期重算、闭区间） |
| /documents 删除/restore | §26（回收站/归档）、§25 |
| /notices（CRUD/发布/取消） | §17、§18、§19、§22、§25 |
| /notices/*/read | §20（2026-11-03 起只记录查看） |
| /notices/*/statistics、*/audience | §21（仅统计实际需阅读人员） |
| /notices/*/documents | §19（多对多） |
| /recycle-bin | §8、§26 |
| /audit-logs | §25、§33 |
| /backups（create/restore/delete） | §25、§28 |

---

## 12. 实现注意事项（后端强制，§33）

1. 所有“登录即可”的端点也须在 `get_current_user` 复核 `is_active` 与角色；管理端点一律 `require_roles("admin")`。
2. 预览/下载逐请求鉴权 + 可见性校验（归档资料、非目标通知），**不允许前端隐藏按钮代替**。
3. 文件相关端点走统一 `file_safety`（白名单/魔数/大小/清洗/内部名/防穿越），见 DESIGN §5。
4. 分页、错误码、日期格式（`YYYY-MM-DD`）务必与本文一致，便于实现与测试对齐。

---

## 13. 勘误（实现校正记录）

> 实现即契约；以下为契约与实际实现的差异记录（t28 回收站 + 备份恢复模块落地时确认），
> 以后续需求/评审如需回改，以本节为准。

| # | 位置 | 契约原文 | 实际实现 | 说明 |
|---|---|---|---|---|
| 1 | §10 POST /api/v1/backups/{id}/restore | 权限 `admin`（强烈建议 superadmin） | **仅 `admin`**，且 `confirm=true` 必填（缺省/缺失 body → `400 BAD_REQUEST`） | 恢复属高风险覆盖操作；权限模型 v3 下 admin 专属 + 二次确认 |
| 2 | §8 DELETE /api/v1/recycle-bin/{id} | 权限 admin + `{"confirm": true}` | 同左；**缺失 body 或未传 confirm → `400`**（不返回 422） | 二次确认为硬性前置条件 |
| 3 | §8 GET /api/v1/recycle-bin 列表项 | 示例不含 purged 字段 | 额外返回 `purged_at`（及 `purged_by`），用于标识已物理删除的记录 | 回收站记录软保留（不物理删行），列表含已恢复/已永久删除条目供审计追溯 |
| 4 | §10 GET /api/v1/backups/{id} | “manifest.json 摘要” | 返回 `manifest`（schema_version/backup_type/created_at/created_by/db_sha256/file_count/files）；manifest 损坏时 `manifest=null` | — |
| 5 | §10 POST /api/v1/backups | 备份失败 `500` + `status='failed'` | 同左，且失败同样写 `backup_create` 审计（detail 含 error） | 失败留痕 |

### 2026-11-11 本轮无数据库迁移

- 批量资料动作、通知批量取消/恢复、备份校验、筛选参数、`ids` 查询、`updated_at` DTO、返回导航和维护闸门均**不新增数据表、列或索引**。
- `preview_token` 只存进程内存；批量审计使用既有 `audit_logs.detail` JSON；无需运行 `scripts/migrate.py`，也不需要对历史数据回填或重建。
- 部署前提不变：当前为单进程部署。若未来改为多 worker，预览 token 与维护闸门需要改为跨进程存储/协调；本轮未实现多 worker。

---

### DELETE /api/v1/users/{id}/purge — 彻底删除用户 [admin]
物理删除账号行，**免二次确认**（§26.1 锁定决策）。前置（内容责任链，任一命中
返回 `409 CONFLICT`）：创建/编辑/归档过资料、创建/编辑过通知——此类账号只能
停用（责任人不可置空）。成功后：阅读快照随账号删除（`published` 通知的历史
统计回溯减少，删除前端文案已告知）、操作日志行保留且 `user_id` 置 NULL、
用户名即释放可重建。
> **2026-11-16**：账号若被资料的可见范围引用（`document_targets.user_id`），
> 删除时**移除该授权行**（资料保留，其他可见对象不受影响），不再抛
> `IntegrityError` → 500。授权行不是内容责任链，因此不阻断删除。

（部门已无独立 purge 端点：删除即物理删除，见上节。）

---

## 附录：路由总表（2026-11-11 由应用导出，共 102 条）

> 由 `app.main.app.routes` 程序化导出，非手工整理。新增/删除端点时重新导出本表，
> 可避免"代码有、文档无"的漂移。权限与字段语义见上文各节。

### API（81 条）

> 2026-09-29 勘误：当时 `app.main.app.routes` 的 `/api/*` 路由为 74 条（原标题误写 75）；
> 2026-11-11 新增 5 个批量/校验端点后为 81 条。行内容与
> `tests/test_infra_smoke.py::test_api_doc_table_matches_routes` 保持一致。

| 方法 | 路径 |
|---|---|
| GET | `/api/v1/audit-logs` |
| POST | `/api/v1/audit-logs/batch-delete` |
| DELETE | `/api/v1/audit-logs/{log_id}` |
| GET | `/api/v1/audit-logs/{log_id}` |
| POST | `/api/v1/auth/change-password` |
| POST | `/api/v1/auth/login` |
| POST | `/api/v1/auth/logout` |
| GET | `/api/v1/auth/me` |
| GET | `/api/v1/backups` |
| POST | `/api/v1/backups` |
| DELETE | `/api/v1/backups/{backup_id}` |
| GET | `/api/v1/backups/{backup_id}` |
| POST | `/api/v1/backups/{backup_id}/restore` |
| POST | `/api/v1/backups/{backup_id}/verify` |
| GET | `/api/v1/categories` |
| POST | `/api/v1/categories` |
| DELETE | `/api/v1/categories/{category_id}` |
| PUT | `/api/v1/categories/{category_id}` |
| GET | `/api/v1/dashboard` |
| GET | `/api/v1/departments` |
| POST | `/api/v1/departments` |
| DELETE | `/api/v1/departments/{dep_id}` |
| PUT | `/api/v1/departments/{dep_id}` |
| GET | `/api/v1/documents` |
| POST | `/api/v1/documents` |
| POST | `/api/v1/documents/batch-actions/{action_key}/execute` |
| POST | `/api/v1/documents/batch-actions/{action_key}/preview` |
| POST | `/api/v1/documents/bulk` |
| DELETE | `/api/v1/documents/{doc_id}` |
| GET | `/api/v1/documents/{doc_id}` |
| PUT | `/api/v1/documents/{doc_id}` |
| GET | `/api/v1/documents/{doc_id}/attachment` |
| POST | `/api/v1/documents/{doc_id}/attachment` |
| DELETE | `/api/v1/documents/{doc_id}/attachment` |
| GET | `/api/v1/documents/{doc_id}/file` |
| POST | `/api/v1/documents/{doc_id}/file` |
| GET | `/api/v1/documents/{doc_id}/notices` |
| GET | `/api/v1/documents/{doc_id}/preview/docx` |
| GET | `/api/v1/documents/{doc_id}/preview/image` |
| GET | `/api/v1/documents/{doc_id}/preview/pdf` |
| GET | `/api/v1/documents/{doc_id}/preview/xlsx` |
| POST | `/api/v1/documents/{doc_id}/restore` |
| GET | `/api/v1/documents/{doc_id}/status` |
| GET | `/api/v1/notice-types` |
| POST | `/api/v1/notice-types` |
| DELETE | `/api/v1/notice-types/{notice_type_id}` |
| PUT | `/api/v1/notice-types/{notice_type_id}` |
| GET | `/api/v1/notices` |
| POST | `/api/v1/notices` |
| POST | `/api/v1/notices/bulk-cancel` |
| GET | `/api/v1/notices/{notice_id}` |
| PUT | `/api/v1/notices/{notice_id}` |
| GET | `/api/v1/notices/{notice_id}/audience` |
| POST | `/api/v1/notices/{notice_id}/cancel` |
| GET | `/api/v1/notices/{notice_id}/documents` |
| GET | `/api/v1/notices/{notice_id}/file` |
| GET | `/api/v1/notices/{notice_id}/preview/docx` |
| GET | `/api/v1/notices/{notice_id}/preview/image` |
| GET | `/api/v1/notices/{notice_id}/preview/pdf` |
| GET | `/api/v1/notices/{notice_id}/preview/xlsx` |
| POST | `/api/v1/notices/{notice_id}/read` |
| GET | `/api/v1/notices/{notice_id}/statistics` |
| GET | `/api/v1/recycle-bin` |
| GET | `/api/v1/recycle-bin/notices` |
| POST | `/api/v1/recycle-bin/notices/bulk-restore` |
| DELETE | `/api/v1/recycle-bin/notices/{notice_id}` |
| POST | `/api/v1/recycle-bin/notices/{notice_id}/restore` |
| POST | `/api/v1/recycle-bin/purge-all` |
| DELETE | `/api/v1/recycle-bin/{entry_id}` |
| POST | `/api/v1/recycle-bin/{entry_id}/restore` |
| GET | `/api/v1/tags` |
| POST | `/api/v1/tags` |
| DELETE | `/api/v1/tags/{tag_id}` |
| PUT | `/api/v1/tags/{tag_id}` |
| GET | `/api/v1/users` |
| POST | `/api/v1/users` |
| DELETE | `/api/v1/users/{user_id}` |
| GET | `/api/v1/users/{user_id}` |
| PUT | `/api/v1/users/{user_id}` |
| DELETE | `/api/v1/users/{user_id}/purge` |
| POST | `/api/v1/users/{user_id}/reset-password` |

### 页面路由（21 条）

| 方法 | 路径 |
|---|---|
| GET | `/`（工作台） |
| GET | `/dashboard`（301 → `/`，别名） |
| GET | `/login` |
| GET | `/change-password`（首登强制） |
| GET | `/documents`、`/documents/{doc_id}` |
| GET | `/notices`、`/notices/{notice_id}` |
| GET | `/admin/documents/new`、`/admin/documents/{doc_id}/edit` |
| GET | `/admin/notices/new`、`/admin/notices/{notice_id}/edit`、`/admin/notices/{notice_id}/stats` |
| GET | `/admin/categories`、`/admin/tags`、`/admin/notice-types` |
| GET | `/admin/users`、`/admin/departments` |
| GET | `/admin/recycle-bin`、`/admin/backups`、`/admin/audit-logs` |
