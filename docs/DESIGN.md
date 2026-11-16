# 系统架构设计（DESIGN.md）

> 项目：公司内部资料与通知管理系统
> 版本：v1 设计基线
> 关联文档：[DATABASE.md](./DATABASE.md)、[API.md](./API.md)
> 需求依据：项目根目录 `项目规范`

---

> ⚠️ 2026-11-11 Agent 上下文整洁：本文件是**历史架构参考**，不是当前 UI 契约或需求真源。
> 根目录 `DESIGN.md` 才是 UI / 样式 / 交互唯一真源。
> 旧文件白名单、draft 状态、旧测试数量等段落均为历史记录，不代表当前实现。

## 1. 设计总览

### 1.1 一句话定位

面向公司内部员工与管理员，通过浏览器**查看资料、在线预览/下载文件、阅读通知**，以及由管理员**维护资料、通知、分类、标签、用户、备份**的 Web 业务系统。**不是** AI 知识库、RAG 或对外公开系统。

### 1.2 技术选型（遵循 项目规范 §6）

| 项 | 选择 | 说明 |
|---|---|---|
| 语言 | Python 3.9+ | 公司环境主用 Python（**实测基线 3.9.12**：`.venv` 即 3.9；2026-09-29 勘误，原文写 3.11+ 与实现不符） |
| Web 框架 | FastAPI | 成熟、自带 OpenAPI 文档、类型安全 |
| ORM | SQLAlchemy 2.0（ORM + Core） | 声明式模型、关系与约束完整 |
| 数据库 | SQLite（配置文件数据库文件路径，项目规范 §27） | 单文件、零运维、可备份 |
| 模板 | Jinja2 | 服务端渲染页面，无重前端框架 |
| 前端 | 原生 HTML + CSS + JavaScript（fetch 调 REST） | 不引入大型框架 |
| 认证 | 自建登录 + JWT（HS256）+ HttpOnly Cookie | 项目规范 §23；
| 密码哈希 | **bcrypt**（`passlib[bcrypt]`，`CryptContext(schemes=["bcrypt"])`，cost=`BCRYPT_ROUNDS` 默认 12） | 不可逆、抗 GPU 爆破（2026-09-29 勘误，原文写 Argon2id/pwdlib 与实现不符） |
| 迁移 | **自研 `scripts/migrate.py` + `scripts/migrations/NNN_描述.py`** | 检测式幂等、按序号执行、不引入版本表（SQLite 小系统的最小机制；2026-09-29 勘误，原文写 Alembic 与实现不符） |
| 预览 | PDF 浏览器内联 / DOCX 文本提取 / Excel 只读（见 §6） | 项目规范 §12 |
| 上传/类型检测 | 扩展名白名单 + **内置魔数判定**（`app/core/file_safety.py`：PDF `%PDF-`+pypdf、XLS OLE2+xlrd、DOCX/XLSX ZIP+`[Content_Types].xml`） | 项目规范 §11（2026-09-29 勘误，原文写 python-magic/filetype，实际未引入这两个库） |
| 配置 | `pydantic-settings` 读环境变量 / `.env`（提供 `.env.example`） | 项目规范 §29 |
| 测试 | `pytest` + `httpx`（TestClient）+ `tmp_path` 临时数据目录 | 项目规范 §30–33 |

> 2026-09-29 勘误：本段原建议引入 Alembic，**实际未采用**。落地做法是
> `Base.metadata.create_all()`（测试库/新建库，模型即最新 schema）+ `scripts/migrate.py`
> （既有库的增量演进，检测式幂等）。见 `docs/DATABASE.md` §迁移。

### 1.3 设计原则（项目规范 §3）

正确性 > 数据安全 > 可维护性 > 稳定性 > 易用性 > UI 美观。

据此作出的关键取舍：

- 状态**一律由系统计算**，不存库、不手工填写（§14）。
- 业务数据**不物理删除**，一律先入回收站/归档（§26）；永久删除必须二次确认且仅限管理员。
- 旧文件**永不覆盖、永不移出前先入回收站**（§8、§26）。
- 权限**由后端强制校验**，前端隐藏按钮只是 UI 优化（§33）。
- 所有路径、密钥、端口**通过配置管理**，禁止硬编码（§29）。

---

## 2. 总体架构

```
┌──────────────────────────────────────────────────────────────┐
│                         浏览器 (员工/管理员)                    │
│   Jinja2 页面（服务端渲染）  +  原生 JS 通过 fetch 调用 REST       │
└───────────────┬───────────────────────────────┬──────────────┘
                │ Cookie: access_token (HttpOnly)│ Authorization: Bearer <JWT>
                ▼                               ▼
┌──────────────────────────────────────────────────────────────┐
│                     FastAPI 应用 (app/)                       │
│  ┌────────────┐  ┌──────────────┐  ┌───────────────────────┐ │
│  │ routers    │→ │ services     │→ │ models (SQLAlchemy)   │ │
│  │ (页面+API) │  │ 业务逻辑    │  │ core (auth/security) │ │
│  └────────────┘  └──────────────┘  └───────────────────────┘ │
│        │  config.py (env/.env)         │ database.py (session)│
└────────┼───────────────────────────────┼──────────────────────┘
         ▼                               ▼
   data/ 业务数据                  SQLite 数据库
   （文件系统，禁止静态暴露）        data/database/app.db
```

分层职责：

1. **routers**（app/routers/）：HTTP 层。只做参数解析、鉴权依赖注入、调用 service、组织响应。不写业务规则。
2. **services**（app/services/）：业务规则所在（时效计算、冲突检查、回收站流转、通知投递物化、备份恢复等）。可独立单测。
3. **models + database**（app/models/、app/database.py）：ORM 模型与会话管理。
4. **core**（app/core/）：密码哈希、JWT、当前用户依赖、角色校验、文件安全校验工具。
5. **web**（app/web/static + templates）：静态资源与 Jinja2 模板。

页面路由与 REST API **共用同一套 services 与权限依赖**，避免逻辑分叉。

---

## 3. 目录结构（数据与程序分离，项目规范 §7）

```
文件管理系统/
├── 项目规范                 # 需求书与协作约定
├── README.md                 # 部署与使用
├── 交接记录                # 交接文档（页面与语义现状）
├── DESIGN.md                 # UI 设计契约（架构设计见 docs/DESIGN.md）
├── .env.example              # 配置样例（真实密钥不入库/不提交）
├── requirements.txt / requirements-dev.txt
├── docs/                     # 设计与验证文档
│   ├── DESIGN.md DATABASE.md API.md TESTING.md
│   ├── DEPLOY.md DEPLOY_WINDOWS.md ADMIN_LAUNCH_CHECKLIST.md EMPLOYEE_GUIDE.md
│   └── history/              # 冻结的历史归档（故意不随代码更新）
├── app/                      # ★ 程序代码
│   ├── main.py               # FastAPI 入口：lifespan 建表引导、挂路由/中间件、异常处理
│   ├── core/
│   │   ├── config.py         # pydantic-settings 配置 + ensure_dirs（路径唯一来源）
│   │   ├── security.py       # bcrypt 哈希 / JWT 签发校验
│   │   ├── security_middleware.py  # 安全响应头 + 登录/敏感操作限速（RATE_LIMITED 429）
│   │   ├── deps.py           # get_current_user / require_roles(...)（Cookie 或 Bearer）
│   │   ├── errors.py         # AppError + 统一错误码 → HTTP 映射
│   │   ├── file_safety.py    # 扩展名/魔数/大小校验、文件名清洗、内部随机名
│   │   └── cache.py          # 进程内列表/工作台缓存与统一失效入口 invalidate_lists
│   ├── db/
│   │   ├── session.py        # engine / SessionLocal / Base / get_db / total_count
│   │   └── init_db.py        # 建表 + 初始 admin + 通知类型种子（幂等）
│   ├── models/               # SQLAlchemy 模型（对应 DATABASE.md）
│   │   ├── user.py           # User / Department / user_departments
│   │   ├── category.py       # Category / Tag / document_tags
│   │   ├── document.py       # Document / FileRecord
│   │   ├── notice.py         # Notice / NoticeTarget / NoticeRead / NoticeType / notice_documents
│   │   ├── audit.py          # AuditLog
│   │   └── backup.py         # BackupRecord / RecycleBin
│   ├── schemas/              # Pydantic 请求/响应模型
│   │   ├── common.py auth.py user.py department.py category.py tag.py
│   │   ├── document.py notice.py notice_type.py
│   ├── services/             # 业务规则层（权限/校验不依赖前端）
│   │   ├── auth_service.py user_service.py department_service.py
│   │   ├── category_service.py tag_service.py notice_type_service.py
│   │   ├── document_service.py file_service.py validity_service.py preview_service.py
│   │   ├── notice_service.py notice_linkage.py recycle_service.py audit_service.py backup_service.py
│   ├── routers/              # HTTP 端点（前缀 /api/v1；pages.py 为 Jinja2 页面）
│   │   ├── pages.py auth.py users.py departments.py categories.py tags.py
│   │   ├── documents.py notices.py notice_types.py recycle_bin.py audits.py
│   │   ├── backups.py dashboard.py
│   ├── web/
│   │   ├── templating.py     # Jinja2 环境
│   │   ├── templates/        # *.html（base + 用户页 + admin/*）
│   │   └── static/           # css/ js/（js/admin/* 为管理页脚本）
├── tests/                    # 自动化测试（项目规范 §30–33；分批规则见 docs/TESTING.md）
│   ├── conftest.py helpers.py files/（真实文件生成器）
│   └── test_*.py             # 525 项（2026-10-20 --collect-only 实测）：validity/files/documents/categories/notices/
│                             # permissions/users/audit/recycle_backup/backup_*/e2e_flows/…
├── scripts/
│   ├── init_db.py            # 手动建表引导（等价调 app.db.init_db）
│   ├── migrate.py            # 幂等迁移执行器（--check 预览）
│   ├── migrations/           # 001 部分唯一索引 / 002 统计种子 / 003 backups.source / 004 墓碑清理
│   ├── migrate_partial_unique.py
│   ├── seed_demo.py          # DEMO 数据灌入/清理（--clean，按「DEMO-」前缀）
│   └── *_check.py / *_journey.py / bench_pagination.py   # 走查与基准脚本
└── data/                     # ★ 业务数据（不提交 Git，运行时生成；路径来自 DATA_DIR）
    ├── database/app.db       # SQLite
    ├── documents/            # 当前资料文件（按内部名前 2 位分片：documents/ab/xxx）
    ├── notices/              # 通知文件
    ├── uploads/              # 上传暂存/校验区（临时，完成后移入 documents|notices）
    ├── recycle_bin/          # 回收站文件
    └── backups/              # 备份集（含数据库快照 + 文件包 + manifest）
```

要点：

- `app/` 只含程序，`data/` 只含数据；两者路径全部来自配置（`DATA_DIR`），禁止写死绝对路径。
- SQLite 数据库**不存储文件二进制**（§27）：文件落盘于 `data/documents|notices|recycle_bin`，数据库仅存元数据与关系。
- `data/` 目录**绝不通过静态文件方式对外暴露**（§11、§19）；一律经鉴权接口下载/预览。

---

## 4. 认证与权限设计（项目规范 §23、§24、§33）

### 4.1 登录与令牌

- 用户自建账号登录；登录成功签发 **JWT（HS256）**。
- JWT 载荷：
  ```json
  { "sub": "12", "username": "zhangsan", "role": "admin",
    "iss": "company-doc-notice-system", "aud": "company-doc-notice-system",
    "iat": 1690000000, "exp": 1690007200, "jti": "uuid4-hex" }
  ```
- 过期时间：默认 **12 小时**（`JWT_EXPIRE_MINUTES`，可配）。第一版**不实现刷新令牌**：过期后重新登录。
- 传递方式（二选一，均被后端 `get_current_user` 接受）：
  - `Authorization: Bearer <jwt>`（REST 外部客户端）；
  - `Cookie: access_token=<jwt>`，**HttpOnly + SameSite=Lax + Secure(生产)**：Jinja2 页面与页面内 fetch 自动携带，JS 无法读取，降低 XSS 窃取风险。
- 登录/登出记录审计日志（§25：登录）。连续登录失败也记录 `login_failed`。

### 4.2 角色（RBAC）

| 角色 | 可访问 |
|---|---|
| `user`（普通员工） | 查看/搜索资料、在线预览、下载默认文件（可见即可下载）、查看/阅读通知（仅发给自己的） |
| `admin`（管理员） | user 的全部 + 资料/通知/分类/标签/通知类型/部门管理、阅读统计、操作日志、备份/恢复、用户管理（含授予 `admin` 角色）、回收站永久删除 |

> 权限模型 v3（2026-08-29 定稿）：**只有 `user` 与 `admin` 两个角色**，原超级管理员已并入
> `admin`；`ck_users_role` CHECK 只允许这两个值。

权限实现：

- **后端依赖强制**（§33）：受保护端点注入 `require_roles("admin")`；**前端隐藏按钮不作为权限依据**。
- 用户 `is_active=false` 视为停用：登录被拒、已签发令牌在 `get_current_user` 中复核角色与激活状态。
- 自我保护：不能停用自己；不能停用最后一个启用的 `admin`（防系统无管理员）；不能通过改角色把自己降级导致系统无管理员（service 层校验）。

## 5. 文件安全策略（项目规范 §10、§11、§19）

### 5.1 上传校验管线（`file_service` / `core/file_safety.py`）

1. **扩展名白名单**：仅 `.pdf .docx .xlsx .xls`。
2. **魔数校验（真实类型）**（防伪造扩展名）：

   | 类型 | 魔数/特征 |
   |---|---|
   | PDF | 文件头 `%PDF-` |
   | DOCX/XLSX | ZIP 容器（`PK\x03\x04`）且内部含 `[Content_Types].xml`；用 `python-docx`/`openpyxl` 打开验证为对应 OOXML |
   | XLS | OLE2 复合文档（`D0 CF 11 E0 A1 B1 1A E1`），可用 `xlrd` 验证 |

   扩展名与魔数不一致 → 拒绝（`FILE_TYPE_NOT_ALLOWED` / `FILE_TYPE_MISMATCH`）。
3. **大小限制**：默认 `MAX_UPLOAD_SIZE_MB=50`（可配）；超出返回 `413 FILE_TOO_LARGE`。
4. **文件名清洗**：取 `Path(original).name`，剔除路径分隔符与 `/ \ .. :` 等，保留原始名用于展示/下载（中文正常，§10）；若清洗后为空则拒绝。
5. **内部存储名**：随机 `uuid4().hex + 合法扩展名`，如 `2fcd0b9f....pdf`。**存储路径由服务端生成**，用户不可指定（§11.7）；原始文件名绝不做服务器唯一标识（§10）。
6. **防路径穿越**：存储名只含 hex+扩展名；所有目录拼接基于配置的 `DATA_DIR` 子目录并做 `Path.resolve().is_relative_to(DATA_DIR)` 断言。
7. **哈希**：上传即计算 **SHA-256** 入库（去重检测、备份校验、替换时指纹比对）。

### 5.2 下载 / 预览授权

- 所有下载、预览**必须携带有效令牌**，后端逐请求做：
  - 资料类：文档存在、非“已归档”（普通用户不可访问归档资料）；
  - 通知类：通知状态为 `published`、且当前用户属于其阅读对象（见 §7.6）；
- 使用 `FileResponse`/`StreamingResponse` 定点返回单个文件，**禁用 StaticFiles 挂载 `data/`**（§11、§19）。

### 5.3 替换与回收链路（不覆盖、不物理删，§8、§26）

```
上传新文件(资料 id=X)
   → 校验通过 → 新文件落盘 data/documents/<new>
   → 若 X 已有当前文件:
       · 旧文件 复制/移动 → data/recycle_bin/<old>
       · 旧 file_record.status='recycled'；写入 recycle_bin 记录(reason=replaced)
       · documents.current_file_id 指向新 file_record
   → 审计 replace_file
```

- 永久彻底删除：仅 `admin`；UI 为单次确认弹窗、请求体须带 `confirm=true`（§26；用户/部门/分类的物理删除按 §26.1 免确认）。

---

## 6. 预览方案（项目规范 §12）

> 原则：预览**不替代**原始文件；一律提供原始文件下载；不做会改动原始文件的操作（如另存为）。复杂格式无法完全还原属正常，不允许修改原始文件。

| 类型 | 预览方式 | 说明 |
|---|---|---|
| PDF | 浏览器内联：`GET .../preview/pdf` 返回 `Content-Type: application/pdf` + `Content-Disposition: inline`；前端 `fetch` 后以 `blob:` URL 交给 `<iframe>` | 浏览器自带 PDF 查看器；走 blob 是为了避开 PDFium 二次请求不带 Cookie 的 401 |
| DOCX | 服务端用 `python-docx` 提取段落 + 表格文本 → 渲染为 HTML（`GET .../preview/docx`） | 图片/复杂版式不回退；返还原文件 |
| XLSX | `openpyxl` 只读（`read_only=True`）→ 分表分页 HTML 表格 | 公式只读缓存值，不渲染宏 |
| XLS | `xlrd`（.xls 分支）→ 同上 | 同只读 |
| 图片 | 浏览器内联原图：`GET .../preview/image` 返回原始 `image/*`；前端 `fetch` 后以 `blob:` 交给 `<img class="image-preview">` | jpg/jpeg/png/gif/webp；**服务端不转码**（§12 预览不替代原件） |

- 预览接口对超大文件/超多行做**截断**（默认每表前 N 行、前 N 个 sheet，配置可调），避免内存峰值。
- 预览失败（文件损坏）→ 返回可读错误提示 + 同一枚「默认下载：…」按钮，不影响原始文件（测试：文件损坏场景，项目规范 §32）。
- **CSP 与预览的耦合（2026-10-29 实测缺陷）**：PDF 预览用 `blob:` iframe，而 CSP
  未声明 `frame-src` 时会回退 `default-src 'self'`，`blob:` 不属于 `'self'` → iframe
  被拦，页面显示「已阻止此内容」。因此 CSP 必须显式包含
  `frame-src 'self' blob:`（只放行同源与本页 JS 自建 blob，不放行外部站点），
  同时保留 `frame-ancestors 'none'`。注意 `X-Frame-Options: DENY` 不作用于
  `blob:`，所以 blob 方案本身可用，**不要**为绕过它改回直连 URL。
  回归测试：`tests/test_xss_defense.py::TestSecurityHeaders::test_csp_frame_src_allows_blob_for_pdf_preview`。
- **图片预览同样依赖 blob:**（2026-11-03）：`<img>` 走 `fetch → blob:`，因此
  CSP 的 `img-src` 也必须包含 `blob:`，否则图片被静默拦成空白。
  回归测试：`tests/test_files.py::TestImageAsDocumentFile::test_csp_allows_blob_for_image_preview`。

---

## 7. 核心业务设计

### 7.1 资料与文件模型（§8、§13、§14、§15）

- 一个“资料”（document）当前只对应**一个当前文件**（`documents.current_file_id`）。
- 资料含 `effective_from`（必填）与 `effective_to`（可空）两个 DATE；`effective_to` 为空表示“目前无已知失效日期”。
- **状态为纯函数计算、不存库**（§14）：

```
compute_status(effective_from, effective_to, as_of_date, archived):
    if archived:                    return "archived"   # 已归档（管理员移入回收站的资料）
    if as_of_date < effective_from: return "pending"    # 待生效
    if effective_to is None or as_of_date <= effective_to: return "active"  # 有效
    return "expired"                # 已失效
```

- 日期**闭区间**：`2026-01-01 ~ 2026-06-30` 中 `2026-06-30` 有效、`2026-07-01` 失效。
- 历史日期查询：任何 `as_of_date` 传入都按该日重算（`GET /documents/{id}/status?date=`，列表也可带 `as_of_date`），不得只按“今天”。

#### 7.1.1 资料可见范围（2026-11-03）

资料支持「全体 / 指定部门 / 指定人员」三种可见范围，与通知的阅读对象**结构同构、
语义不同**：

| | 通知 `notice_targets` | 资料 `document_targets` |
|---|---|---|
| 含义 | 谁**必须读** | 谁**可以看** |
| 副作用 | 物化快照、算未读、算统计 | 不产生任何阅读记录 |
| 越权响应 | `403` | `404`（不泄露"存在但无权限"） |

- 数据：`documents.audience_scope ∈ {all, department, user}` + `document_targets` 目标行
  （`scope=all` 不写行）。
- 判定：`app/services/document_visibility.py` 的 `visibility_condition()` / `is_document_visible()`。
- **管理员恒可见**，不受范围限制（与通知一致）。
- 过滤必须覆盖**全部读取路径**：列表、关键词搜索、分页 `total`、详情、文件下载、
  四类预览端点。列表缓存键必须带 `user.id`，否则 A 的列表会被 B 命中造成越权。
- 存量迁移：`009_document_audience.py` 把已有资料全部回填为 `all`，升级后普通用户
  不会突然看不到东西。

### 7.2 有效期冲突检查（§16）

“同一资料不能出现互相冲突的有效期；发现冲突先警告，**第一版默认阻止保存**，禁止静默覆盖”。具体落地为两条可测试规则：

- **R1（最基本范围冲突）**：`effective_from` 必填；若同时给 `effective_to`，必须 `effective_from <= effective_to`，否则 `403/409 VALIDITY_CONFLICT` 阻止保存并在响应 `detail` 中给出中文警告。
- **R2（不静默覆盖既有时效）**：资料已有 `effective_from/effective_to`，再次修改时效（包括“替换文件并同时改时效”）时，若新区间与旧区间**重叠/包含**且非“明确为空开始的首次设置”，接口要求调用方显式传 `confirm_overwrite: true`；否则默认返回 `409 VALIDITY_CONFLICT` + 明确警告。传了 `confirm_overwrite` 才允许保存，且强制写入一条 `modify_validity` 审计（记录新旧值、操作人、原因）。

> 说明：由于第一版“一个资料一个当前文件”，同一资料同时不会存在两条并存时效记录，因此冲突主要形态是 R1（日期倒挂）与 R2（修改既有窗口时静默覆盖）——这两条规则共同满足“先警告、默认阻止、不静默覆盖”。

### 7.3 通知导致失效 / 延期（§31：通知导致失效）

- 发布“**废止 / 替换**”类通知并关联资料时：`notice_service` 在创建即发布成功的事务内，将所关联资料的 `effective_to` **自动置为通知的生效日**（`notice.effective_at` 的日期部分），并写 `modify_validity` 审计（操作对象=通知，说明=废止/替换导致失效）。若因此与 R1 冲突（如通知生效日早于资料 effective_from），则**回滚发布**并返回冲突警告。
- 发布“**延期**”类通知：**不自动改日期**；由管理员另行执行“修改时效”（带 `confirm_overwrite`，走审计）。通知正文承载延期说明。

### 7.4 通知（§17–§22）

- 通知组成：标题 + 正文（纯文字）/（原始文件）/（两者皆有）；含 `published_at`（创建时间）、`effective_at`（生效时间）、`notice_type`（**管理员自定义词表** `notice_types`，**可空**，种子六类：发布/修订/替换/废止/延期/其他；其中「废止/替换」驱动资料时效联动且禁止改名）、阅读对象（全体/部门/用户）、关联资料（多对多）。
- 状态机：`draft`（草稿）→ `published` → `cancelled`（已取消，软删除）。草稿已下线；创建即 `published`，修改需留痕（见 §7.5）。
- **阅读对象快照（§21）**：创建时把“全体/指定部门（展开部门下用户）/指定用户”解析为具体用户集合，**物化**为 `notice_reads` 行（`read_at` 为空）。**管理员不进快照、不进统计分母**（2026-11-03）：管理员能看全部通知是管理权限，不等于被要求阅读。此后阅读统计只看这些行 → 只统计“实际需要阅读的人”；部门人员变动不再影响既有 `published` 通知的应阅读基数。
- **可见性**：普通用户仅在通知 `status='published'` 且（`effective_at` 为空或 `<= now`）时可见；`admin` 可见全部。

### 7.5 published 状态通知的修改（§22）

- 若已有任何 `read_at`：`PUT` 必须带 `confirm_modify: true` + `modified_reason`（非空），并在通知上记录 `updated_by/updated_at/modified_reason` + 审计 `update_notice`；缺任一项 → `409` 并提示。
- 若无阅读记录：发布态可改，但同样写审计。
- 修改后若变更 target 范围：不对既有 `notice_reads` 做删除（保留已阅读证据），如需扩大范围新增行；统计口径以快照为准（文档中说明）。

### 7.6 阅读（§20）

> 2026-11-03 产品决策：**确认功能整体下线**，只保留「已查看 / 未查看」单一维度。
> 历史迁移已删除 `notices.requires_confirmation`、`notice_reads.confirmed_at`、`confirm_ip`
> 三列（迁移 `005_drop_confirm_columns.py`）；`POST /notices/{id}/confirm`
> 接口同步移除。当前阅读统计只含「应阅读 / 已查看 / 未查看」。

- `GET /notices/{id}`：查看详情（不自动标记）。
- `POST /notices/{id}/read`：记录 `read_at`（首次覆盖；幂等）。触发条件：通知状态为 `published` 且目标包含当前用户。
  管理员调用时静默成功且不写库（他不是阅读对象）。
- 阅读统计只有「已查看 / 未查看」两态，分母 = 快照 ∩ 启用用户 ∩ 非管理员。

### 7.7 分类与标签（§9）

- 分类：**一级~四级树**（`categories.parent_id` 自引用，深度 ≤ 4，service 校验 `MAX_DEPTH`）；一个资料归属**一个分类**（任意层级节点，含非叶子）；管理员维护。
- 分类的筛选与计数为**文件夹语义**：选中某分类时含其**全部子孙**分类下的资料（`category_service.subtree_ids`），树上 `document_count` 与点进该分类看到的资料数同口径。
- 标签：与分类无关的扁平词表，资料多对多，用于搜索与筛选。

---

## 8. 错误处理设计

### 8.1 统一响应/错误契约（与 API.md 一致）

- 成功：直接返回业务 JSON。
- 失败：HTTP 状态码 + JSON：
  ```json
  { "error": { "code": "VALIDITY_CONFLICT", "message": "生效日期倒挂：effective_from 晚于 effective_to", "detail": { ... } } }
  ```
- 全局异常处理器（`main.py`）统一转换：
  - `AppError`（业务异常，携带稳定 `code`）→ 4xx；
  - `RequestValidationError` → `400 VALIDATION_ERROR`（含字段明细）；
  - `HTTPException` → 标准 401/403/404；
  - 未捕获异常 → `500 INTERNAL_ERROR`，服务端 logging 记录完整堆栈，**不回显敏感细节**。

### 8.2 统一错误码

| code | HTTP | 场景 |
|---|---|---|
| `UNAUTHORIZED` | 401 | 未登录 / 令牌无效过期 |
| `FORBIDDEN` | 403 | 角色不足、越权访问 |
| `PASSWORD_CHANGE_REQUIRED` | 403 | 首登/重置改密未完成时调用白名单外的 `/api/`（2026-10-16 起） |
| `NOT_FOUND` | 404 | 资源不存在 |
| `VALIDATION_ERROR` | 400 | 参数/字段非法 |
| `VALIDITY_CONFLICT` | 409 | 时效冲突（R1/R2，阻止保存） |
| `NOTICE_MODIFY_REQUIRES_REASON` | 409 | 已读通知修改缺原因/确认 |
| `FILE_TYPE_NOT_ALLOWED` | 415 | 扩展名不在白名单 |
| `FILE_TYPE_MISMATCH` | 415 | 扩展名与魔数不符（伪造） |
| `FILE_TOO_LARGE` | 413 | 超过大小限制 |
| `DUPLICATE` | 409 | 用户名/分类名/标签名重复等 |
| `IN_USE` | 409 | 分类/标签仍被引用，禁止删除 |
| `CONFLICT` | 409 | 其他状态冲突（如目标部门有成员） |
| `RATE_LIMITED` | 429 | 登录/敏感操作频率超限（`core/security_middleware.py`） |
| `BAD_REQUEST` | 400 | 一般请求错误 |
| `INTERNAL_ERROR` | 500 | 未捕获服务端异常 |

---

## 9. 操作日志设计（§25）

- 由 `audit_service.log(...)` 统一写入 `audit_logs`；**与业务操作同一事务提交**（保证回滚时日志一致；登录失败等无事务场景单独写入）。
- 审计字段：`user_id/username`（操作人快照）、`action`、`object_type/object_id/object_label`、`detail`(JSON)、`ip`、`user_agent`、`created_at`。
- **action 常量表**（第一版必需至少覆盖）：

  `login` `login_failed` `logout` `change_password` `create_document` `update_document` `replace_file` `delete_document` `archive_document` `restore_document` `modify_validity` `create_notice` `update_notice` `publish_notice` `cancel_notice` `read_notice` `confirm_notice`（历史动作，功能已下线；仅保留日志兼容） `create_category` `update_category` `delete_category` `create_tag` `update_tag` `delete_tag` `create_notice_type` `update_notice_type` `delete_notice_type` `manage_user` `create_department` `update_department` `delete_department` `backup_create` `backup_restore` `backup_delete` `recycle_restore` `recycle_purge` `audit_delete`

- 查询接口限 `admin`（§25、§33）。

---

## 10. 备份与恢复方案（§28）

### 10.1 备份（full）

- 每次备份生成一个时间戳目录：
  ```
  data/backups/backup_20260825_103000/
    ├── manifest.json        # schema 版本、创建人/时间、文件数、各类 sha256、说明
    ├── app.db               # SQLite 快照（可用 sqlite3 .backup API 或 file copy，先 checkpoint）
    └── files/               # 归档后的业务文件（documents + notices + recycle_bin）
  ```
- `manifest.json` 记录每一类文件的 `sha256` 清单，供恢复校验。
- `backup_records` 表登记：类型（`full`/`database`）、路径、大小、文件数、`db_sha256`、状态、创建人/时间、备注。
- `database` 类型（可选）仅备份数据库快照，用于轻量/高频场景。

### 10.2 恢复

1. 选择备份记录（`GET /backups/{id}`，校验 `status=completed`、manifest/校验和）。
2. 调用 `POST /backups/{id}/restore`（admin；要求 `confirm: true` + 原因）：
   - **先自动生成“当前状态”安全备份**（防误恢复）；
   - 校验备份完整性（manifest + sha256 清单）；
   - 以生产目录“临时目录就绪 → 切换 → 留旧”方式执行（尽可能原子，避免半替换）；
   - 写审计 `backup_restore`；`backup_records.status='restored'`，记录 `restored_at/by`。

   > **关于恢复前安全备份的审计（已知行为，如实记录）**
   > - 安全备份通过 `create_backup` 创建，**会**正常写入一条 `backup_create` 审计
   >   （含操作人/时间/指纹），与其他手动备份完全同路径；
   > - 但该审计写入的是**恢复前的旧数据库**，随后旧库被备份快照替换，
   >   因此这条 `backup_create` 审计在恢复后的活跃库中**不再可见**（随旧库归档于
   >   `.pre_<ts>` 备份副本与安全备份目录中，未丢失）；
   > - 恢复流程在**恢复后的库**中：a) 补录一条安全备份 `backup_records` 行
   >   （`status='completed'`，note 注明“恢复前自动安全备份，由恢复流程补录”，
   >   管理员可查看/清理该备份目录）；b) 在 `backup_restore` 审计的 `detail`
   >   中记录 `safety_backup_id`，可据此追溯本次恢复的安全备份。
   > - 结论：安全备份**有**审计链（旧库 backup_create + 新库 backup_restore.safety_backup_id），
   >   但“创建瞬间”的 backup_create 条目不进恢复后活跃库，属预期行为。
3. 文档明确限制：恢复期间应避免并发写入；SQLite 单写者 + 应用层锁，恢复前停写保护（v1 以“管理员确认且系统空闲提示”实现）。

---

## 11. 配置（§29）与 .env.example

```ini
# .env.example —— 复制为 .env 后按需修改；真实密钥不入 Git
APP_NAME=company-doc-notice-system
APP_ENV=development
HOST=0.0.0.0
PORT=8000

# 数据目录与数据库（禁止硬编码绝对路径）
DATA_DIR=./data
DATABASE_URL=sqlite:///./data/database/app.db

# 安全
SECRET_KEY=change-me-to-a-long-random-string
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=720

# 文件
MAX_UPLOAD_SIZE_MB=50
PREVIEW_DOCX_MAX_PARAS=5000
PREVIEW_XLSX_MAX_ROWS=2000
PREVIEW_XLSX_MAX_SHEETS=20

# 身份认证（local：系统自建账号体系）
AUTH_PROVIDER=local

# 初始管理员（首次引导建表时创建；生产禁用占位/弱密码）
INITIAL_ADMIN_USERNAME=admin
INITIAL_ADMIN_PASSWORD=please-change-me
```

- 配置类用 `pydantic-settings`：**环境变量优先，.env 兜底**；`SECRET_KEY` 缺省时报错（不允许默认密钥运行）。
- 数据/文件路径全部派生自 `DATA_DIR`；服务启动时自动创建 `data/*` 目录。

---

## 12. 事务、并发与安全基线（§5、§27）

- SQLite 开启 `PRAGMA foreign_keys=ON`、`journal_mode=WAL`、`busy_timeout`；写事务用 `db.commit()/rollback()` 集中管理，长事务拆小。
- CSRF：管理端写操作端点校验 `Origin/Referer`（同源）+ 页面表单 携带 CSRF token（可选但推荐）；鉴权以 JWT(HttpOnly) 为主。
- XSS：Jinja2 默认自动转义；用户写入内容（标题、正文、说明）渲染时转义；预览 HTML 为受控生成。
- 上传即扫描类：无“任意文件上传”（§18 白名单+魔数）；无路径穿越（§5.1.6）。
- 依赖最小化、升级固定版本；N+1 等查询优化在实现阶段用 SQLAlchemy joinedload/eager 处理。

---

## 13. 测试策略映射（§30–33）

| AGENTS 章节 | 测试落点 |
|---|---|
| §30 核心业务 | `test_documents / test_notices / test_recycle / test_backup` |
| §31 时效边界 | `test_validity.py`：生效前一天/当天/后一天、失效前一天/当天/后一天、无失效日期、未来生效、已失效、通知导致失效 |
| §32 文件 | `test_files.py`：中文文件名、重复文件、四类格式、大文件、非法/伪造扩展名、路径穿越、文件不存在、文件损坏 |
| §33 权限 | `test_permissions.py`：普通用户访问管理端（API 与页面）一律 403/302；无权限文件/通知不可读；改资料/通知/时效/看日志均拒绝 |

- 测试使用 `tmp_path` 作为临时 `DATA_DIR` + `--database-url` 指向测试库，**不污染真实数据**。
- 时效计算 `validity_service.compute_status` 为纯函数，重点单测。

---

## 14. 页面路由与 REST 的分工

- `routers/pages.py`：`GET /login`、`GET /`（工作台）、`GET /documents`、`GET /documents/{id}`、`GET /notices`、`GET /notices/{id}` 以及 `/admin/*` 管理页面 —— 均返回 Jinja2 HTML；页面内 JS 通过 `fetch` 调用 REST 获取动态数据并渲染局部。
- `routers/*`：`/api/v1/*` 全部 REST（契约见 API.md），接受 `Authorization: Bearer` 或 Cookie。
- 两者共用 `get_current_user` 与 `require_roles`，保证权限一致（§33）。

### 14.1 外壳与导航高亮（2026-09-14 换肤后）

- 外壳单点定义在 `templates/base.html`：`左侧栏（.side）+ 面包屑顶条（.crumbbar）+ .page-main > .container`。**禁止任何子模板自带导航**——早期静态参照稿是每页各写一份侧栏的，照搬会导致同一组件多处实现漂移（该漂移正是历史缺陷的来源）。
- `_page_context()` 额外注入 `active_nav`，由 `_nav_key(request.url.path)` 按路径派生，供侧栏逐项高亮；`active_page` 保留（管理页统一为 `"admin"`，无逐项区分度，故导航不再用它）。
- 前端**身份**统一经 `ui.isAdmin()` / `ui.currentUser()`（只读 `#user-data`）；**禁止用 DOM 是否存在反推权限**（2026-09-14 已因此产生一次 P0 回归，详见 DESIGN.md「Do's and Don'ts」）。
- 视觉层回归守卫：`scripts/theme_check.py`（需先启动 uvicorn）。

---

## 15. 第一版明确不做（§2、§36 之外不扩产）

AI 知识库/RAG/向量库/AI 问答、第三方账号登录、多租户、对外公开、审批流、消息推送、邮件、历史版本浏览、复杂工作流。本文不为其设计功能/表。
