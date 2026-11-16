# Windows 服务器部署手册（docs/DEPLOY_WINDOWS.md）

> 与 `DEPLOY.md`（Linux 版）完全对应的 Windows 版本。**应用代码零改动**，
> 差异仅在：进程常驻方式（任务计划程序替代 systemd）、路径写法、防火墙命令。
> 目标环境：Windows Server 2016+ / Windows 10+，**Python 3.10+（64 位）**。
> （2026-10-29 修正：原写 3.9+，但依赖下限已抬升到 3.10——fastapi / starlette /
> uvicorn / pydantic-settings 的元数据都是 `>=3.10`，装 3.9 会在 `pip install` 阶段失败。）

## 1. 安装 Python

1. 官网 python.org 下载 **Windows installer (64-bit)**，**3.10 或更高**（推荐 3.12/3.13）
2. 安装时**勾选两项**：`Add python.exe to PATH`、`Install launcher for all users`
3. 安装完成后新开 CMD 验证：

```bat
python --version
```

## 2. 目录规划与代码

```bat
mkdir C:\docnotice
```

把项目文件放入 `C:\docnotice\`（U 盘 / 内网共享 / 从开发机 `scp` 均可）。
**两不拷**：`data\`（业务数据在服务器全新生成）、`.venv\`（依赖在服务器重装）。

> ⚠️ `data\` 必须放在**本地磁盘**（如 `C:\docnotice\data`）——SQLite 在网络
> 映射盘（UNC 路径/网盘）上不可靠，会出锁与损坏问题。

## 3. 虚拟环境与依赖（公司可用 pip）

```bat
cd /d C:\docnotice
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

## 4. 配置 .env

```bat
copy .env.example .env
.venv\Scripts\python -c "import secrets; print(secrets.token_urlsafe(48))"
```

用记事本编辑 `.env`，至少确认以下配置项（与 Linux 版一致）：

| 配置项 | 值 |
|---|---|
| `APP_ENV` | `production`（强制校验 SECRET_KEY） |
| `SECRET_KEY` | 上面命令生成的随机串 |
| `DATA_DIR` | `C:/docnotice/data` —— **用正斜杠 `/`**，Windows 的 Python 完全支持 |
| `INITIAL_ADMIN_USERNAME / PASSWORD` | 自定（上线后改掉） |
| `TRUSTED_HOSTS` | 必须包含员工实际访问的主机名或服务器 IP，例如 `docnotice.example.com,10.20.30.40`；不配置正式域名/IP 时，浏览器带 `Origin` 的写请求会被 CSRF 403 |

## 5. 先手动跑通一次

```bat
cd /d C:\docnotice
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

浏览器访问 `http://服务器IP:8000` → 首次启动自动建目录/建表/迁移/初始管理员 →
初始账号登录（**不再强制改密**，2026-10-29 起：密码由部署者本人设置，直接可用）。
验证上传/预览/下载/发通知后，Ctrl+C 停止，进入第 6 步。

> ⚠️ 因为不再有强制改密兜底，`.env` 里的 `INITIAL_ADMIN_PASSWORD` **必须**是强密码
> （生产环境下占位值会直接拒绝启动）。引导完成后建议从 `.env` 删除该项。

## 6. 开机自启：任务计划程序（系统自带，零外部依赖）

**6a. 写启动脚本** `C:\docnotice\start_docnotice.bat`：

```bat
@echo off
cd /d C:\docnotice
if not exist logs mkdir logs
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 >> logs\uvicorn.log 2>&1
exit /b %ERRORLEVEL%
```

> 说明：日志追加到 `logs\uvicorn.log`（体积大后可停任务、改名归档再重启）；
> 末行把退出码传给任务计划程序，崩溃时可触发自动重启（见 6c）。

**6b. 注册开机自启任务**（管理员 CMD，一行完成）：

```bat
schtasks /Create /TN docnotice /TR "C:\docnotice\start_docnotice.bat" /SC ONSTART /RU SYSTEM /RL HIGHEST /F
```

**6c. 配置"失败自动重启"**（命令行建不了的这一项，图形界面点一次即可）：

1. Win+R → `taskschd.msc` → 任务计划程序库 → 找到 `docnotice` → 右键**属性**
2. **设置** 页签：勾选「如果任务失败，按以下频率重新启动」→ 每 **1 分钟**，尝试 **999** 次
3. 确定。立即手动启动一次验证：右键 → **运行**，然后访问 `http://服务器IP:8000`

日常管理命令：

```bat
schtasks /Run /TN docnotice     &REM 启动
schtasks /End /TN docnotice     &REM 停止
schtasks /Query /TN docnotice   &REM 状态
```

> ⚠️ **必须单实例运行**（与 Linux 版同一约束）：进程内缓存与自动备份调度线程
> 假定唯一实例，**不要**再手动额外起一个 uvicorn，也不要用多机同时指向同一 data 目录。

## 7. 防火墙放行 8000 端口（管理员 CMD）

```bat
netsh advfirewall firewall add rule name="docnotice-8000" dir=in action=allow protocol=TCP localport=8000
```

员工访问 `http://服务器IP:8000`；要走 80/443 用 IIS/Nginx 反向代理（可选）。

### 7.1 只允许指定 IP 访问（推荐）

上面的命令对**所有**来源放行。若系统只供少数几台机器使用，应改为按来源 IP 白名单：

```bat
REM 只允许两个 IP（REM 为注释，不要一起粘贴）
netsh advfirewall firewall add rule name="docnotice-8000" dir=in action=allow ^
  protocol=TCP localport=8000 remoteip=192.168.1.10,192.168.1.55

REM 只允许一个网段
netsh advfirewall firewall add rule name="docnotice-8000" dir=in action=allow ^
  protocol=TCP localport=8000 remoteip=192.168.1.0/24
```

取消限制的方式（**只放开 IP，不删规则**）：

```bat
netsh advfirewall firewall set rule name="docnotice-8000" new remoteip=any
```

完整说明（Linux 版、IPv6 绕过排查、反向代理场景、上线检查清单）见 `docs/DEPLOY.md` 与 `docs/DEPLOY_WINDOWS.md`。

## 8. 升级流程

```bat
REM ① 先在页面上做一次「立即备份」再停服务
schtasks /End /TN docnotice                    &REM ② 停止

REM ③ 替换代码（保留 .venv / data / logs / .env / .git）
robocopy 新代码目录 C:\docnotice /MIR /XD .venv data logs .git __pycache__ /XF .env
REM    ⚠️ /XD 只排除【目录】，/MIR 会删除目标端多余的文件。
REM    因此 .env 必须用 /XF 单独排除，否则会被删掉（2026-10-29 修正）。

.venv\Scripts\python scripts\migrate.py --check   &REM ④ 先看待执行迁移（只读）
.venv\Scripts\python scripts\migrate.py           &REM ⑤ 执行迁移（幂等）
schtasks /Run /TN docnotice                       &REM ⑥ 启动
```

> ⚠️ **迁移顺序不要在部署文档中重复维护**：完整迁移清单、破坏性迁移说明与执行顺序以
> `docs/DATABASE.md` §17 和 `scripts/migrations/` 为准。升级前必须运行
> `.venv\Scripts\python scripts\migrate.py --check` 并按实际待执行迁移备份 `data/`。

> ⚠️ **不能跳过 `migrate.py`**：启动时的 `init_db()` 只跑冻结的内联迁移，
> 不执行 `scripts/migrations/`；`create_all` 也不会给已有表补列。

→ 访问 `/health` 与登录验证。

### 8.1 恢复误删的 `.env`

若曾经用旧版命令（未加 `/XF .env`）升级导致 `.env` 丢失：

1. **先别启动服务**（缺 SECRET_KEY 会让所有已签发令牌失效，但不会损坏数据）；
2. 从 `C:\docnotice\data\backups\` 最近一次备份中找回配置参数（备份不含 `.env`，
   但 `SECRET_KEY` 丢失后需重新生成并**通知所有人重新登录**）；
3. 按 §4 重新写 `.env`，用 `scripts\check_env.py` 校验后启动。

## 9. 常见问题

| 现象 | 处理 |
|---|---|
| 端口被占用 | `netstat -ano | findstr :8000` 找 PID → `taskkill /PID <pid> /F` |
| 员工访问不了 | 先看本机能否访问；再查防火墙规则（§7）、任务是否在运行 |
| pip 装不上 | 确认用的 `.venv\Scripts\pip`；公司镜像源加 `-i <内网源地址>` |
| 双击 bat 一闪而过 | 在行尾加 `pause` 手动跑一次看报错；常见是 Python 不在 PATH |
| 日志无限增长 | 停任务 → 把 `logs\uvicorn.log` 改名归档 → 启动（每月一次即可） |
| 杀毒软件拖慢 | 将 `C:\docnotice\data` 加入 Defender 排除目录（SQLite 高频小文件） |

## 10. 与 Linux 版（DEPLOY.md）的差异对照

| 事项 | Linux | Windows |
|---|---|---|
| 常驻方式 | systemd unit | 任务计划程序（ONSTART + 失败重启） |
| 启动命令 | `.venv/bin/python -m uvicorn ...` | `.venv\Scripts\python.exe -m uvicorn ...` |
| 防火墙 | ufw/firewalld 或云安全组 | `netsh advfirewall` |
| 数据目录 | `/srv/docnotice/data` | `C:/docnotice/data`（.env 用正斜杠） |
| 迁移/升级 | 完全相同（`scripts/migrate.py`，幂等） | 完全相同 |
