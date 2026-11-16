# 作业资料系统部署包

## 包内包含

- `app/`：运行代码
- `scripts/`：初始化、迁移、环境检查与迁移脚本
- `docs/`：API、数据库、Linux/Windows 部署与使用文档
- `deploy/windows/`：Windows 启动和任务计划模板
- `deploy/linux/`：systemd 服务模板
- `.env.example`、`requirements.txt`、`README.md`、`DESIGN.md`、`pyproject.toml`

## 包内不包含

- `.env`、真实密钥
- `data/`、真实业务文件、数据库、备份
- `.venv/`
- `tests/`、E2E/审计证据、截图、原型
- Git 历史和本地缓存
- AI Agent 专用索引、本地审计证据与 `docs/history/`


## 当前制品与校验

- 本包版本：`docnotice-deploy-20261116-v1.4`
- 包内 `BUILD_INFO.txt`：生成时间、Git 提交、文件数、打包边界。
- 包内 `PACKAGE_SHA256.txt`：逐文件 SHA256 清单；`DELIVERY_MANIFEST.md`：交付清单与上线前检查。
- 同名 `.sha256` 文件是 ZIP 的校验值；当前 ZIP SHA256：`见同名 `.sha256` 文件`。部署前先校验 ZIP，再按清单核对关键文件。
- 回滚说明见 `deploy/ROLLBACK.md`；本版本无新增数据库迁移，回滚不要求回退数据库结构。

## 部署步骤

### Windows

1. 解压到例如 `C:\docnotice`。
2. 安装 Python 3.10+（推荐 3.12/3.13，64 位）。
3. 执行：

```bat
cd /d C:\docnotice
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
.venv\Scripts\python -c "import secrets; print(secrets.token_urlsafe(48))"
```

4. 编辑 `.env`：
   - `APP_ENV=production`
   - `SECRET_KEY=` 上一步生成的随机串
   - `DATA_DIR=C:/docnotice/data`
   - `INITIAL_ADMIN_USERNAME / INITIAL_ADMIN_PASSWORD` 改成强密码
   - `COOKIE_SECURE=false`（仅内网 HTTP 部署）
   - 自动备份按需要配置 `BACKUP_SCHEDULE`
   - `TRUSTED_HOSTS` 写入员工实际访问域名/IP；不写会导致带 `Origin` 的 POST/PUT/DELETE 被 403
5. 预检并初始化：

```bat
.venv\Scripts\python scripts\check_env.py
.venv\Scripts\python scripts\init_db.py
```

6. 启动：

```bat
deploy\windows\start_docnotice.bat
```

或参照 `docs/DEPLOY_WINDOWS.md` 注册任务计划程序和防火墙规则。

### Linux

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/python scripts/check_env.py
.venv/bin/python scripts/init_db.py
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

生产常驻、反向代理和防火墙步骤见 `docs/DEPLOY.md` 与 `deploy/linux/docnotice.service.example`。

## 重要部署约束

- 单机、单实例运行；不要把 SQLite 数据目录放在网络盘。
- 反向代理部署必须正确设置真实客户端 IP，否则同一出口 IP 的集中登录可能触发登录限速。
- `data/` 独立于程序目录，首次启动自动生成。
- 不提供异地灾备；按项目锁定决策使用应用内同机备份，并巡检磁盘。