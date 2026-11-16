# 作业资料系统

> 资料与通知管理 Web 系统：面向公司内部员工与管理员。
> 员工查资料、看通知、确认资料是否仍然有效；管理员维护资料、分类、标签、用户与通知。

在 **Python 3.10+ / FastAPI / SQLite / 原生 HTML+CSS+JS** 上实现。数据与程序完全分离，
权限由后端强制校验，前端隐藏按钮不作为权限依据。

---

## 目录

- [功能](#功能)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [数据存储](#数据存储)
- [角色与权限](#角色与权限)
- [备份与恢复](#备份与恢复)
- [测试](#测试)
- [运维脚本](#运维脚本)
- [部署](#部署)
- [文档](#文档)
- [已知限制](#已知限制)

---

## 功能

### 认证与权限

- JWT（HS256）+ HttpOnly Cookie；两种角色 `user` / `admin`
- 自助修改密码；管理员重置密码后强制对方改密
- 权限逐接口在后端强制校验；管理员越权写操作会被拒绝并写入拒绝审计

### 资料

- 元数据 CRUD；**PDF / DOCX / XLSX / XLS / 图片** 上传
  （扩展名 + 魔数 + 大小 + 文件名清洗四重校验）
- 在线预览：PDF 内联 / DOCX 文本 / Excel 只读表格 / 图片原样
- 原始文件下载，中文文件名按 RFC 5987 编码
- **文件替换**：旧文件进回收站，不覆盖
- 可见范围：`all` / `department` / `user` 三种，后端过滤
- 批量治理：按当前筛选批量选择 → 预览 → 执行（分类 / 标签 / 可见范围 / 归档 / 恢复）
- 一个资料 = **1 个主文件 + 最多 1 个附件**。附件只供下载、永不预览；
  下载规则：有附件下载附件，无附件下载主文件。`.pptx` 只允许作附件。

### 时效

- 状态自动计算：`pending` / `active` / `expired` / `archived`
- 闭区间处理；`effective_to` 可为空，界面显示「暂无失效日期」
- 支持**按任意历史日期重算**状态

### 通知

- 纯文字 / 文件 / 文件 + 文字三种形态
- 发布前确认弹窗：展示阅读对象人数与不可逆警示
- 三类阅读对象：全体 / 部门 / 指定用户；发布即物化阅读快照
- 阅读状态只有「未查看 / 已查看」两种，打开详情时记录
- 阅读统计：未查看优先、大数字可点筛选、可按姓名/账号/部门搜人、可按部门下钻；
  **管理员不计入统计**
- **废止 / 替换**通知发布时自动截断关联资料有效期；通知删除回滚联动、恢复时重新应用

### 回收站

替换 / 归档的文件进入回收站，可恢复；通知删除进回收站，支持批量恢复与永久删除。

### 备份与恢复

- 全量备份（库快照 + 业务文件 + manifest 指纹）与仅库备份
- 只读完整性校验：manifest / 数据库 SHA-256 / 文件 SHA-256 / `integrity_check`
- 恢复前自动生成当前状态安全备份；清单校验失败则不恢复
- 恢复期间业务 API 进入 `503 MAINTENANCE_MODE` 维护闸门
- 每周 / 每日自动备份调度 + 保留策略自动清理（天数 / 份数 / 留旧目录）
- **磁盘闸门**：空间不足时拒绝创建备份，且不产生任何备份目录

### 操作日志

32 类动作常量，登录 / 资料 / 通知 / 审计 / 备份 / 恢复 / 回收站等关键操作全留痕。
资料批量变更含 `batch_id` / `selection` / `affected` / `skipped` / `changes` 汇总字段。

---

## 技术栈

| 层 | 选型 |
|---|---|
| 语言 / 运行时 | Python 3.10+ |
| Web 框架 | FastAPI + Uvicorn |
| ORM / 数据库 | SQLAlchemy 2.0 + SQLite（WAL、外键强制；库中只存元数据） |
| 认证 | PyJWT（HS256）+ passlib[bcrypt] |
| 文件处理 | pypdf / python-docx / openpyxl / xlrd（只读预览） |
| 前端 | 服务端 Jinja2 模板 + 原生 CSS/JS（无重型框架） |
| 测试 | pytest + pytest-xdist + httpx + Playwright |

---

## 快速开始

### Linux / macOS

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt

cp .env.example .env
# 编辑 .env：至少设置一个足够长的随机 SECRET_KEY 与强 INITIAL_ADMIN_PASSWORD
.venv/bin/python scripts/check_env.py          # 部署前配置预检，退出码 0/1

.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### Windows

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt

copy .env.example .env
.venv\Scripts\python scripts\check_env.py
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

浏览器访问 <http://127.0.0.1:8000>，用 `.env` 中的 `INITIAL_ADMIN_USERNAME` /
`INITIAL_ADMIN_PASSWORD` 登录。

> 首次启动会自动创建数据目录、建表、执行增量迁移并创建初始管理员（约 7 秒）。
> `uvicorn` **不会**读取 `.env` 里的 `HOST` / `PORT`（那两项只是部署约定值），
> 监听地址与端口以命令行显式传入为准。

---

## 数据存储

业务数据全部位于 `data/`（与程序 `app/` 分离，路径由 `DATA_DIR` 配置）：

```
data/
├── database/app.db         # SQLite 数据库（元数据与关系，不含文件二进制）
├── documents/              # 资料当前文件（内部随机名，非原始文件名）
├── notices/                # 通知文件
├── uploads/                # 上传暂存区
├── recycle_bin/            # 回收站文件（替换/归档的旧文件，不物理删除）
├── backups/                # 备份集（backup_<ts>/ 含 app.db + files/ + manifest.json）
└── logs/                   # 应用日志
```

`data/` 与 `.env` 已在 `.gitignore` 中，**绝不提交 Git**。
数据库不存放大文件二进制，文件保存到文件系统。

---

## 角色与权限

| 能力 | 普通用户 | 管理员 |
|---|---|---|
| 查询、查看资料（分类 / 标签 / 关键词 / 时间段） | ✅ | ✅ |
| 在线预览 PDF / DOCX / Excel | ✅ | ✅ |
| 下载默认文件 | 可见即可下载 | ✅ |
| 查看通知、标记已查看 | ✅（仅阅读对象内的通知） | ✅ |
| 资料 / 通知 / 分类 / 标签 / 通知类型 / 部门 / 用户管理 | — | ✅ |
| 通知发布与阅读统计 | — | ✅ |
| 回收站（恢复 / 永久删除）、操作日志、备份 / 恢复 | — | ✅ |

保护规则（服务层强制）：

- 不能停用或删除自己
- 不能删除最后一个启用的管理员（防系统锁死）
- 普通用户不可见归档资料
- 已删除数据只出现在回收站，不出现在任何业务列表
- 下载权限 = 查看权限（可见即可下载）

---

## 备份与恢复

- **自动备份（默认开启）**：调度线程按 `BACKUP_SCHEDULE`（默认每周六 03:00，
  可改 `daily 03:00`，留空关闭）执行全量备份；启动时若距上次成功备份超过一个
  调度周期会自动补跑。
- **保留策略（默认开启）**：`BACKUP_KEEP_DAYS=90`（超期清理）、
  `BACKUP_KEEP_COUNT`（份数兜底）、恢复留旧目录 `*.pre_<时间戳>` 保留最近 3 组
  （`BACKUP_PRE_KEEP_COUNT`）。清理只碰 `data/backups/` 白名单目录并写审计日志。
- **创建备份**：管理端「备份恢复」页，或 `POST /api/v1/backups`：

  ```bash
  curl -X POST http://127.0.0.1:8000/api/v1/backups \
    -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' -d '{"type":"full"}'
  ```

- **恢复**：仅管理员，需 `confirm=true`；恢复前自动生成当前状态安全备份，
  并全量校验清单指纹，校验失败不恢复。同一时刻只允许一个恢复执行，
  并发请求返回 `409 CONFLICT`。

---

## 测试

```bash
# 全量（默认并行，pytest-xdist -n auto）
.venv/bin/python -m pytest

# 串行（排查偶发问题时用）
.venv/bin/python -m pytest -n 0

# 统计当前用例数
.venv/bin/python -m pytest --collect-only -q -n 0
```

测试隔离：`tests/conftest.py` 使用会话级临时 `DATA_DIR`，并对真实 `data/` 做快照守卫，
**测试绝不污染业务数据**；并行模式下每个 worker 有独立临时库。

浏览器回归脚本：

```bash
.venv/bin/python scripts/e2e_full_check.py       # 全量浏览器回归
.venv/bin/python scripts/admin_journey.py         # 管理员全流程走查
.venv/bin/python scripts/user_journey.py          # 普通用户权限走查
.venv/bin/python scripts/theme_check.py           # 配色契约走查
.venv/bin/python scripts/modal_a11y_check.py      # 弹窗键盘可达性
.venv/bin/python scripts/a11y_dom_check.py        # 全站可访问性 DOM 检查
```

更详细的运行规则见 [`docs/TESTING.md`](docs/TESTING.md)。

---

## 运维脚本

| 脚本 | 用途 | 何时跑 |
|---|---|---|
| `scripts/check_env.py` | 部署前配置预检（退出码 0/1） | **每次部署前** |
| `scripts/init_db.py` | 手动初始化数据库 / 初始管理员 | 首次部署 |
| `scripts/migrate.py` | 执行增量迁移 | 升级版本时 |
| `scripts/seed_demo.py` | 灌入演示数据（`--clean` 先清） | 演示前 |
| `scripts/package_for_deploy.py` | 生成生产部署包（`--zip` 同时出 ZIP + SHA256） | 发布前 |
| `scripts/bench_pagination.py` | 大表分页性能压测 | 改列表 / 索引后 |

提交守门（一次安装，每次 commit 自动跑 ruff + mypy）：

```bash
bash scripts/git-hooks/install.sh
```

---

## 部署

生产部署见：

- [`docs/DEPLOY.md`](docs/DEPLOY.md) —— Linux（venv + systemd + 安全加固）
- [`docs/DEPLOY_WINDOWS.md`](docs/DEPLOY_WINDOWS.md) —— Windows 服务器

**上线第一步**：`python scripts/check_env.py`，有阻塞项则退出码 1，先修再上线。
**部署后**：把正式访问域名 / IP 写入 `.env` 的 `TRUSTED_HOSTS`。

版本回退步骤见 [`deploy/ROLLBACK.md`](deploy/ROLLBACK.md)。

生成部署包：

```bash
python scripts/package_for_deploy.py --zip
# 输出在 release/；包内不含 .env / data / .venv / tests / 原型 / 审计证据，
# 并生成逐文件 SHA256 清单、ZIP SHA256 与部署说明
```

---

## 文档

| 文档 | 内容 |
|---|---|
| [`docs/API.md`](docs/API.md) | REST API 契约（末尾以应用导出的路由总表校验） |
| [`docs/DATABASE.md`](docs/DATABASE.md) | 数据库表结构与迁移机制 |
| [`docs/DESIGN.md`](docs/DESIGN.md) | 架构设计 / 错误码 / 安全方案 |
| [`DESIGN.md`](DESIGN.md) | **UI 设计系统契约**（样式与组件的权威） |
| [`docs/TESTING.md`](docs/TESTING.md) | 测试运行规则与走查脚本清单 |
| [`docs/SECURITY.md`](docs/SECURITY.md) | 安全设计与风险评估 |
| [`docs/DEPLOY.md`](docs/DEPLOY.md) / [`docs/DEPLOY_WINDOWS.md`](docs/DEPLOY_WINDOWS.md) | 部署手册 |
| [`docs/EMPLOYEE_GUIDE.md`](docs/EMPLOYEE_GUIDE.md) | 员工使用指南（一页纸） |
| [`docs/ADMIN_LAUNCH_CHECKLIST.md`](docs/ADMIN_LAUNCH_CHECKLIST.md) | 管理员上线首周清单 |
| [`docs/diagrams/`](docs/diagrams/) | 项目全景图（drawio 源文件，`scripts/gen_project_diagram.py` 为唯一真源） |

---

## 已知限制

第一版**不提供**：AI 知识库 / RAG、第三方账号登录、多租户、对外公开访问、
审批流、消息推送、邮件、历史版本浏览、复杂工作流。

其他边界：

- 通知发布后不可收窄阅读对象范围（需补发新通知）。
- 恢复采用「在线回写 + 留旧」：数据库经 SQLite online backup 原地恢复，
  备份后新增的物理文件移出活动目录（旧状态保留在 `*.pre_<时间戳>` 目录与自动安全备份中）；
  恢复期间请暂停写操作。
- 恢复会回滚「备份创建」审计与备份记录本身（快照时序所致，源备份记录按清单重建）。
- 账号体系为自建 local 账号 + 密码；不接入外部身份认证。
- 单进程运行。不要扩成多 worker——批量预览 token 和恢复维护闸门是进程内状态。

---

## 许可证

本仓库代码按 MIT 许可证发布，详见 [`LICENSE`](LICENSE)。
