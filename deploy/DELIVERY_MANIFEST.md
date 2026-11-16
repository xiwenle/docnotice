# 交付清单（2026-11-16 v1.4）

## 交付物

| 文件 | 用途 |
|---|---|
| `docnotice-deploy-20261116-v1.4.zip` | 生产部署 ZIP |
| `docnotice-deploy-20261116-v1.4.zip.sha256` | ZIP 校验值 |
| `docnotice-deploy-20261116-v1.4/` | 解包后的可检查目录 |

## 包内必须存在

- `app/main.py`
- `.env.example`
- `requirements.txt`
- `pyproject.toml`
- `scripts/check_env.py`、`scripts/init_db.py`、`scripts/migrate.py`
- `scripts/migrations/`
- `deploy/DEPLOYMENT_README.md`、`deploy/VALIDATION.md`、`deploy/ROLLBACK.md`
- `deploy/DELIVERY_MANIFEST.md`
- `deploy/windows/start_docnotice.bat`
- `deploy/linux/docnotice.service.example`
- `docs/API.md`、`docs/DATABASE.md`、`docs/DEPLOY.md`、`docs/DEPLOY_WINDOWS.md`、`docs/TESTING.md`
- `BUILD_INFO.txt`、`PACKAGE_SHA256.txt`

## 包内禁止存在

- `.env`、`data/`、`.venv/`、`tests/`、`.git/`
- 原型、截图、审计证据、`AGENT_INDEX.md`
- `*.db`、`*.sqlite`、`*.log`、`*.pid`

## 校验步骤

1. 校验 ZIP：`Get-FileHash docnotice-deploy-20261116-v1.4.zip -Algorithm SHA256`，结果与同名 `.sha256` 一致。
2. 解压到临时目录，核对上述必须存在/禁止存在清单。
3. 读取包内 `BUILD_INFO.txt`，确认版本、Git 提交和文件数。
4. 以 `PACKAGE_SHA256.txt` 逐文件核对完整性。
5. 在部署机器配置 `.env` 后运行 `python scripts/check_env.py`。
6. 首次启动后访问 `/health`，返回 `{"status":"ok",...}`。

## 版本与回滚

- 当前代码提交：`baf84ff`（2026-11-16 全功能真浏览器验收复验，184/184 PASS）；
  更早的补强提交见 Git 历史
- 历史代码发布标签：`admin-bulk-governance-v1.1`
- 回滚基线：`admin-bulk-baseline-20261111`
- 本版本无新增数据库迁移；回滚代码不要求回退数据库结构。
- 详细回滚步骤见包内 `deploy/ROLLBACK.md`。
