# 公司内网部署手册（docs/DEPLOY.md）

> 目标：把「公司内部资料与通知管理系统」部署到公司内网服务器（Linux），
> 采用 venv + systemd 常驻运行。全流程与 项目规范 §29/§38/§39 对齐。
> **Windows 服务器请用姊妹篇：`docs/DEPLOY_WINDOWS.md`**（应用代码零改动）。

## 1. 环境要求

- 操作系统：Linux（本手册以 Ubuntu/Debian 系为例；macOS 本地开发步骤见 README.md）
- Python：3.10+（含 venv；2026-10-25 依赖安全升级后由 3.9 抬升）；本项目不引入 Docker/容器等额外设施，尽量降低维护成本
- 内存：512MB 以上即可；磁盘按业务文件量规划（见 §6 备份）
- 网络：仅内网访问（默认 `HOST=127.0.0.1` + 反向代理，或内网 `0.0.0.0`——二选一，见 §8 安全）

## 2. 安装与目录规划

```bash
# 部署目录（示例）：/srv/docnotice
sudo mkdir -p /srv/docnotice && sudo chown $USER /srv/docnotice
cd /srv/docnotice

# 代码（真实公司资料严禁提交 Git，代码与数据分离：数据在 /srv/docnotice/data）
git clone <内网代码仓库> .          # 或拷贝项目目录

# 虚拟环境与依赖
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# 开发/测试环境才需要：.venv/bin/pip install -r requirements-dev.txt
```

## 3. 配置（生产）

```bash
cp .env.example .env
```

| 配置项 | 生产建议 |
|---|---|
| `APP_ENV=production` | 必填：生产模式会**强制校验 SECRET_KEY**（缺失/占位值直接拒绝启动）。⚠️ 2026-09-15 起取值非法（如拼写错误 `prodction`）会**拒绝启动**而非静默降级为开发模式；只接受 `development` / `production` / `test`（大小写与首尾空格自动归一） |
| `SECRET_KEY` | 必替换：`.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(48))"` 生成 |
| `COOKIE_SECURE` | 留空 = 自动（`production` 开、其余关）。**内网 http:// 部署务必显式设 `false`**：强制 `Secure` 会让浏览器静默拒存登录 Cookie，表现为「登录成功却立刻退回登录页」。接入 HTTPS 后设 `true` |
| `DATA_DIR=/srv/docnotice/data` | 建议绝对路径；首次启动自动创建 `database/documents/notices/uploads/recycle_bin/backups` |
| `HOST / PORT` | uvicorn **不会读取**这两项，监听地址/端口由 systemd ExecStart 的 `--host/--port` 显式传入（两者保持一致即可）：反向代理方案 `127.0.0.1:8000`；直连方案 `0.0.0.0:80/443`（见 §8 安全建议） |
| `INITIAL_ADMIN_USERNAME/PASSWORD` | 首次启动自动创建初始管理员（**不再强制改密**，2026-10-29 起）。因没有强制改密兜底，密码必须是强密码（生产环境配占位值会拒绝启动）；引导完成后删除该配置 |
| `MAX_UPLOAD_SIZE_MB` | 按需（默认 50MB） |
| `LOGIN_RATE_WINDOW` | 登录限速窗口秒数（默认 60） |
| `LOGIN_RATE_MAX_ATTEMPTS` | 窗口内最大登录尝试次数（默认 10，防暴力破解） |
| `TRUST_REVERSE_PROXY_HEADERS` | 反代部署且应用端口不对其他来源开放时设 `true`，才会信任 `X-Forwarded-For`（审计 IP/登录限速用）。若 100 人经同一代理/NAT 却拿不到真实客户端 IP，会被默认「每 IP 60 秒 10 次」登录限速拦截 |
| `TRUSTED_HOSTS` | CSRF 校验使用的 Host / Origin 可信白名单（逗号分隔）。默认值仅含 `localhost,*.localhost,127.0.0.1,::1,testserver`；生产环境必须把实际访问域名或 IP 加入白名单，否则浏览器带 `Origin` 的 POST/PUT/DELETE 会被 403。若启用反代，也只应在 `TRUST_REVERSE_PROXY_HEADERS=true` 时信任 `X-Forwarded-Host` / `X-Forwarded-Proto`，且转发 Host 仍必须命中此白名单 |
| `DB_POOL_SIZE / DB_MAX_OVERFLOW` | 文件型 SQLite 连接池常驻/溢出上限，默认 `20 + 20`；100 用户单进程场景已按此验收 |
| `DB_POOL_TIMEOUT_SECONDS` | 池等待上限，默认 10 秒；超时返回 503 `DB_BUSY` + `Retry-After: 5` |
| `DATABASE_URL` | 默认留空（自动派生自 DATA_DIR）；如需显式指定请用**绝对路径**的 sqlite URL |
| `DISK_MIN_FREE_MB` | 磁盘健康绝对下限（默认 512MB）。可用空间低于此值时**拒绝创建备份**（且不产生任何备份目录）；启动时另巡检一次，不足记 ERROR 日志但不阻止启动 |
| `DISK_MIN_FREE_PERCENT` | 磁盘健康相对下限（默认 5.0%）。与上一项**都**须满足才算充足 |

> **配置预检（2026-10-26 加，强烈建议每次部署前执行）**：
> ```bash
> .venv/bin/python scripts/check_env.py
> ```
> 只读脚本，检查 10 项（`APP_ENV` 合法性、`SECRET_KEY` 占位与长度、初始管理员密码
> 强度、`COOKIE_SECURE` 与 `APP_ENV` 匹配、`DATA_DIR`/数据库可写、
> `BACKUP_SCHEDULE` 格式、`PORT` 合法性、磁盘余量）。
> **退出码 0=通过，1=有阻塞项（不可上线）**。它与 §3 表格互补：表格告诉你
> 该配什么，脚本告诉你配对了没有。

> **API 文档**：`APP_ENV=production` 时 `/docs`、`/redoc`、`/openapi.json` 自动关闭
> （2026-09-15 起；开发/测试环境保留以便联调）。

## 4. systemd 常驻服务

`/etc/systemd/system/docnotice.service`：

```ini
[Unit]
Description=Company Doc & Notice System
After=network.target

[Service]
Type=simple
User=docnotice                # 建议专用系统账号；不给 root
WorkingDirectory=/srv/docnotice
EnvironmentFile=/srv/docnotice/.env
ExecStart=/srv/docnotice/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
# ⚠️ 必须单进程运行（不要加 --workers N）：
#   进程内 TTL 缓存与自动备份调度线程都假定"每份数据只有一个进程"；
#   多 worker 会导致缓存失效失灵（改了数据还显示旧列表）和重复备份调度。
#   内网规模下单进程足够（5 万条资料压测 13 场景 ≤250ms）；确需扩容时
#   先改造缓存与调度为共享存储/单例锁，再上多进程。
Restart=always
RestartSec=3
# 可选加固
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

> 注意：`EnvironmentFile` 按 systemd 兼容格式（`KEY=value`，无 `export`）。

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now docnotice
sudo systemctl status docnotice
curl -s http://127.0.0.1:8000/health   # {"status":"ok",...}
```

## 5. 首次初始化与验收

- 首次启动自动完成：建 6 个业务目录（+ `logs/`）→ 建 **17 张表** → 创建初始管理员（幂等，可重复启动）。
  > 2026-11-04 勘误：新增 `document_targets`（资料可见范围）后为 **17 张**；
  > 2026-10-29 时实测为 16 张，
  > 且权限模型 v3 已无 superadmin（仅 `admin` / `user` 两种角色，见 `app/core/deps.py`）。
- 也可手动初始化：`/srv/docnotice/.venv/bin/python scripts/init_db.py`。
- 交付前按 `项目规范 §38` 实际验证：登录 → 上传资料（真实 PDF/DOCX/XLSX/XLS）→ 查看/预览/下载 → 发通知 → 阅读/确认 → 统计 → 备份 → 恢复。

## 6. 数据目录与备份策略

```
/srv/docnotice/data/
├── database/app.db      # 数据库（WAL 模式；文件权限建议 640，属主 docnotice）
├── documents/ notices/ uploads/ recycle_bin/
└── backups/             # 备份集（应用内备份与恢复）
```

- **应用内备份**（推荐，含库+文件+校验清单）：管理员「备份恢复」页或
  `POST /api/v1/backups {"type":"full"}`；恢复仅 admin + 二次确认，恢复前自动安全备份。
- **自动备份与保留策略**（应用内线程实现，无需系统 cron，`.env` 配置）：
  - `BACKUP_SCHEDULE`（默认 `sat 03:00`）：每周/每日自动全量备份；留空=关闭。
    调度与手动备份同代码路径；备份页列表以「自动」徽章区分来源。
  - `BACKUP_KEEP_DAYS=90`：超过 90 天的备份集在下次备份/恢复后自动清理；0=关闭。
  - `BACKUP_KEEP_COUNT=0`：可选份数兜底（超出保留最新 N 份）；0=不限。
  - `BACKUP_PRE_KEEP_COUNT=3`：恢复演练留旧目录（`*.pre_<时间戳>`）保留最近 3 组。
  - 清理只碰 `data/backups/` 白名单目录，全部写入审计日志；自动备份停止时
    清理随之停止（宁多留不误删）。备份页与工作台「距上次备份」卡可监视调度健康。
- **备份边界（2026-08-30 拍板）**：应用内自动备份 + 同机保留策略即最终备份
  方案，不做异地/双机/离机拷贝（项目规范 §28 锁定决策）。配套纪律：
  服务器磁盘健康纳入日常巡检（磁盘占用、SMART 状态）——单盘场景下这是
  唯一的风险观察点。
- 恢复演练：每季度用测试环境执行一次「备份 → 恢复 → 校验数据回到备份时点」（已在开发库实测通过）。
  这是备份可恢复性的最终验证手段（保留策略的前提是备份本身可用）。

## 7. 升级与维护

- **日常巡检盯两个信号**（决定何时需要扩容/升级，无需额外监控设施）：
  1. 关键词搜索响应变慢 —— 全站唯一全表扫描操作；明显变慢说明资料量到了
     该评估 FTS5 全文索引的量级；
  2. 备份体积增速 —— 备份列表可见体积趋势；增速加快时复核保留策略参数
     （`BACKUP_KEEP_DAYS` / `BACKUP_KEEP_COUNT`）。
  单进程容量余量参考：5 万条资料压测 13 场景冷调用 ≤250ms。

- 升级流程（**必须包含 migrate.py**）：

```bash
# ① 先在页面上做一次「立即备份」
sudo systemctl stop docnotice                     # ② 停服
cd /srv/docnotice && git pull                     # ③ 更新代码
.venv/bin/pip install -r requirements.txt         # ④ 依赖有变更时才需要
.venv/bin/python scripts/migrate.py --check       # ⑤ 先看有哪些迁移待执行（只读）
.venv/bin/python scripts/migrate.py               # ⑥ 执行迁移（幂等，已应用的自动跳过）
sudo systemctl start docnotice                    # ⑦ 启动
curl -fsS http://127.0.0.1:8000/health            # ⑧ health 验证
```

> ⚠️ **不能跳过 `migrate.py`**（2026-10-29 修正）。启动时的 `init_db()` 只跑
> **冻结的内联迁移**，不会执行 `scripts/migrations/NNN_*.py`；
> `Base.metadata.create_all` 也**不会给已有表补列**。
> 漏跑的直接后果是启动失败，例如缺 `backup_records.source` 列时报
> `no such column: backup_records.source`。全新部署（空 `data/`）可直接启动，
> 但升级必须走 `migrate.py`。

- 数据库结构变更：新增表/列/约束一律走 `scripts/migrations/`（见 §17、`docs/DATABASE.md`）；业务数据（`data/`）**不受代码替换影响**。
- 注意：代码升级前先做一次应用内备份（§6）。

> ⚠️ **迁移顺序不要在部署文档中重复维护**：完整迁移清单、破坏性迁移说明与执行顺序以
> `docs/DATABASE.md` §17 和 `scripts/migrations/` 为准。升级前必须运行
> `python scripts/migrate.py --check` 并按实际待执行迁移备份 `data/`。

## 8. 安全建议（必须项）

1. **密钥**：`SECRET_KEY` 强随机；`APP_ENV=production`；初始管理员密码必须是强密码（**没有强制改密兜底**），引导后从 `.env` 移除该配置。
2. **监听与网络**：优先 `HOST=127.0.0.1` + Nginx/Caddy 反代到内网域名；如直连内网，仅限可信网段（防火墙只放行业务端口如 8000）。

   > **反向代理必查**：若所有员工经同一 Nginx/Caddy 进入，代理必须覆盖写入真实 X-Forwarded-For，并在应用中设 TRUST_REVERSE_PROXY_HEADERS=true；应用端口只允许代理机访问。否则所有人共享代理 IP，集中登录会触发限速。/static 文本资源由应用选择性 gzip，PDF/Office/图片下载保持不压缩；反代可再启用压缩，但不要改写文件响应。

   **按来源 IP 做白名单**（选一种，按你的发行版）：

   ```bash
   # ufw（Ubuntu/Debian）
   sudo ufw allow from 10.0.0.0/8 to any port 8000 proto tcp
   sudo ufw enable && sudo ufw status

   # firewalld（RHEL/CentOS）
   sudo firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address=10.0.0.0/8 port port=8000 protocol=tcp accept'
   sudo firewall-cmd --reload
   ```

   > ⚠️ **IPv6 容易被漏掉**：只封了 IPv4 而机器有公网 IPv6 时，白名单形同虚设。
   > 先 `ip -6 addr` 确认是否有全局地址，有则同样加 IPv6 规则（或直接禁用该网卡 IPv6）。
   >
   > 取消：把 `allow`/`add-rich-rule` 换成 `delete` / `--remove-rich-rule` 后重新加载。
   > 排查：`ss -tlnp | grep 8000` 看监听地址；`curl -v http://<IP>:8000/health` 从白名单内外各试一次。
3. **TLS**：若未来需要跨网段/公网访问，反代层必须启用 HTTPS；本系统 Cookie `SameSite=Lax` 且生产自动 `Secure`。
4. **文件安全（内置）**：上传白名单扩展名+魔数校验+大小限制+随机内部名；`data/` 绝不通过 Web 暴露（直链 404，穿越 404——VERIFICATION §4）。
5. **账户策略**：普通员工按部门建号；管理员账号最小化；停用员工立即可登出（令牌逐请求复核 `is_active`）。
6. **审计**：关键操作全部留痕，`/admin/audit-logs` 可查；异常时先看操作日志再处置。
7. **系统账号**：服务用专用账号（非 root）；`data/` 目录权限收紧（`chmod 750`，属主服务账号）。
8. **勿把 `.env`、`data/` 提交 Git**（已 gitignore）；提交代码前检查无真实公司资料。

## 9. 常见问题

| 症状 | 处理 |
|---|---|
| 生产启动报“禁止使用缺失/占位 SECRET_KEY” | 按 §3 生成并写入 `.env`，`systemctl restart docnotice` |
| 上传大文件被拒 | `MAX_UPLOAD_SIZE_MB` 调大（默认 50MB） |
| 恢复报 500「备份校验失败」 | 备份集损坏（文件缺失/指纹不符）；先手工保留现状，联系管理员从另一备份恢复 |
| 数据库文件损坏 | 从最近一次应用内备份恢复；恢复前系统会自动生成当前状态安全备份 |
| 页面 302 回登录 | 会话过期/令牌角色变更；重新登录即可 |

## 10. 部署验收清单

- [ ] `.venv/bin/python scripts/check_env.py` **退出码 0**（有阻塞项先修再上线）
- [ ] 生产 `.env`：`APP_ENV=production` + 强 `SECRET_KEY` + 绝对 `DATA_DIR`
- [ ] `systemctl status docnotice` 运行中，`/health` 200
- [ ] 初始管理员可正常登录（不强制改密）；确认 `.env` 中初始密码为强密码，上线后移除该配置
- [ ] 员工/部门/分类/标签数据就绪；真实文件上传/预览/下载抽查通过
- [ ] 完成一次「备份 → 恢复」演练并留档
- [ ] 防火墙/反代仅向内网开放；`data/` 与 `.env` 权限收紧
- [ ] 自动备份已启用（`BACKUP_SCHEDULE` 非空）并跑通一次
- [ ] 磁盘余量纳入日常巡检（项目规范 §28 锁定：不做异地灾备，同机保留策略为最终方案）