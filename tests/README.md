# tests/ —— 自动化测试目录（t23 测试基建）

> 对齐 项目规范 §30–33（测试策略）与 DESIGN.md §13（测试映射）。

## 目录结构

```
tests/
├── conftest.py              # 全局夹具（环境隔离/DB 重建/用户/部门/分类/标签/登录）
├── helpers.py               # 公共 API helper（登录/认证头/建资料/传文件/建通知）
├── test_infra_smoke.py      # 基建自检（应用启动/登录链路/数据隔离/生成器可解析性）
├── test_validity.py         # 时效：纯函数+API 双路径（边界/闭区间/历史重算/R1/R2）
├── test_documents.py        # 资料：CRUD/搜索/四类型真实文件/替换/归档恢复/预览/下载
├── test_categories.py       # 分类：一~四级建链/超限与成环/子树筛选/计数同口径/删除保护
├── test_files.py            # 文件安全：中文名/重复/伪造扩展名/穿越/损坏/大文件/RFC5987
├── test_notices.py          # 通知：创建即发布/物化快照/打开即已查看/统计/废止联动/有痕修改
├── test_permissions.py      # 权限矩阵：401/403/停用/超管专属/内容可见性
├── test_users.py            # 用户/部门管理：CRUD/保护规则/重置密码
├── test_audit.py            # 操作日志：关键动作留痕/查询过滤/分页
├── test_recycle_backup.py   # 回收站 + 备份/恢复（t28 并行支线）
├── test_backup_zero_files.py# 零文件备份/恢复边界回归（t28 并行支线）
├── files/                   # 真实测试文件生成器包（不是伪造字节）
│   ├── __init__.py          # 便捷导出
│   ├── generators.py        # PDF/DOCX/XLSX/XLS/大文件/损坏/重复/伪造扩展名 + sha256
│   └── README.md
└── README.md
```

## 如何运行

```bash
.venv/bin/pip install -r requirements-dev.txt   # pytest/httpx/freezegun/xlwt
.venv/bin/pytest -q                              # 直接运行（conftest 已处理 sys.path）
.venv/bin/pytest tests/test_infra_smoke.py -v    # 只跑基建自检
```

不需要 `python -m pytest` 或 PYTHONPATH —— conftest 会把项目根加入 sys.path。

## 环境隔离（§7 数据与程序分离）

- `DATA_DIR` / `DATABASE_URL` / `SECRET_KEY` 在 conftest 模块顶部（任何 `app.*`
  导入之前）指向 pytest 会话级临时目录；`app.db.session` 导入期创建的 engine
  因此天然落在临时数据库上。
- 为什么是会话级临时目录而不是每个测试 `tmp_path`：engine/session 在导入期绑定
  配置，逐测试换 DATA_DIR 需要重载整个 app 包。会话级临时目录 + 每测试
  `drop_all/create_all`（`setup_db` 夹具）已满足隔离要求。
- 数据隔离守卫（autouse session）：会话开始与结束时分别对真实 `data/` 做
  （相对路径, 大小, mtime）快照比对，任何污染直接失败。
- `data_dir` / `db_path` / `real_data_before` fixture 供显式断言。

## 可用夹具（conftest.py）

| fixture | 说明 |
|---|---|
| `client` | TestClient（raise_server_exceptions=False） |
| `db` | SQLAlchemy 会话 |
| `admin_user` / `normal_user` | admin/普通用户（ORM 直建，bcrypt 哈希） |
| `admin_token` / `user_token` | 直接签发 JWT（快速路径） |
| `admin_headers` / `user_headers` | Bearer 认证头 |
| `login` / `headers_for` | **真实登录接口**换 token（验证密码哈希链路） |
| `user_factory` | 任意用户（角色/部门/激活态可配） |
| `department_factory` / `department` | 部门 |
| `category_factory` / `category` | 分类（parent_id 支持二级） |
| `tag_factory` / `tag` | 标签 |
| `freeze` | freezegun.freeze_time（时效边界测试） |

公共常量：`ADMIN_PASSWORD = "admin1234"`、`USER_PASSWORD = "user1234"`。

## 生成器（tests/files/，详见 files/README.md）

`make_pdf / make_docx / make_xlsx / make_xls / make_large_pdf / make_large_xlsx /
make_corrupted / make_duplicate / write_raw_file / sha256_of / unique_name`
+ 中文文件名常量：`PDF_FILENAME`（员工手册2026.pdf）、`DOCX_FILENAME`（公司制度.docx）、
`XLSX_FILENAME`（费用报销表.xlsx）、`XLS_FILENAME`（工资明细表.xls）。

## 与后端的契约（供后端/后续测试对齐）

1. 全部 ORM 模型在 `app/models/__init__.py` 统一导出（conftest `import app.models`
   依赖它注册 Base.metadata，建表才完整）。
2. 认证契约：`app.core.security.hash_password / verify_password / create_access_token`、
   `POST /api/v1/auth/login`。
3. 资料/通知端点按 docs/API.md 契约实现后，`tests/helpers.py` 的
   `create_document / upload_document_file / create_notice` 即刻可用（t25 全量测试）。
4. 文件类型契约：魔数 = PDF `%PDF-`、DOCX/XLSX `PK\x03\x04`、XLS OLE2 头
   （DESIGN.md §5.1），与生成器输出一致。

## 已知约定

- 本机系统时钟为 2026 年；时间相关断言（如 freeze 测试）使用 2024 作为“过去”参考。
- `data/database/app.db`（真实目录）可能是开发服务器早期运行遗留物；数据隔离
  守卫以“会话前后快照一致”为准，允许其存在但禁止测试写入。