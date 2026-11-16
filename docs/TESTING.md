# 测试运行规则（docs/TESTING.md）

> 本文件是全量测试的**执行规则**，不是当前测试数量或项目状态的真源；最新验证结果见 `交接记录` 顶部。
>
> ⚠️ 2026-10-26 起默认**并行**运行（pytest-xdist，`-n auto`）：521 用例
> 4m05s → 24~38s；2026-11-09 本次收尾用默认 `-n auto` 实测 **665 用例 / 28.84s**（并行数不同，耗时不可横向比较）。原"三批串行"降级为**低配机器**的备用方案。
> 详见 §一与 §二。

## 一、总原则

1. **默认并行**（2026-10-26 改）：直接跑 `pytest` 即为并行（`pyproject.toml`
   的 `addopts = "-n auto"`），无需分批。2026-11-09 Windows / Python 3.13.7 本次收尾实测 665 用例 / 28.84s（默认 `-n auto`；历史 608 用例 2m22s）。
   - **并行隔离已实测**：xdist 每个 worker 是独立进程 → 各自 import `conftest`
     → 各自 `mkdtemp` → **各自独立 `app.db`**（实测 4 worker → 4 个独立
     库文件）。各 worker 的 `setup_db`（`drop_all`/`create_all`）互不干扰。
   - **退回串行**：排查偶发问题时用 `pytest -n 0`（或 `pytest -p no:xdist`）。
2. **判定等价**：分批只是调度方式，通过标准与全量一致——**全量 passed
   即通过**；当前基线 **665 项**（2026-11-09 本次收尾 `-n auto` 实测，28.84s）。
3. **隔离保证**：`tests/conftest.py` 使用会话级临时 DATA_DIR + 真实 `data/`
   快照守卫，跑测试**绝不触碰真实业务数据**（dev 服务器对 database/app.db
   的写入被豁免，业务文件目录严格监控）

## 一·2、低配机器：三批串行（备用方案）

> 仅当开发机/服务器 CPU 紧张（`-n auto` 反而更慢或触发时序类偶发失败）时
> 使用。**跑完一批再跑下一批，禁止与 `-n auto` 混用**。


## 二、三批构成（低配机器备用方案，基线 **521**，2026-10-21 全量实测：158 + 264 + 99）

> 2026-10-26：并行化后本节不再是默认路径，仅在低配机器上按 §一·2 使用；
> 下列命令需加 `-n 0` 退回串行。
>
> 2026-09-29 勘误：原文写「基线 497，154+245+98」是 2026-09-15 的旧数。
> 2026-10-16 勘误：09-29 新增的 `test_p1_fixes_20260929.py`（8 条）与
> `test_notice_linkage_reconcile_20260929.py`（11 条）当时未登入本表（遗漏归批），
> 同日 `test_auth_boundary.py` 新增改密闸门回归 4 条（P0 修复）。
> 2026-10-21：批 3 新增 `test_backup_zero_files.py::test_restore_survives_windows_locked_db_files`
> （P0 跨平台恢复回归），98 → 99；现表与 `--collect-only` 一致：**521**。
> ⚠️ 本机沙箱的 FS broker 会把 `Path.mkdir(exist_ok=True)` 在**目录已存在**时误抛
> `PermissionError`，导致所有用 `tmp_path` 的用例集体 ERROR（实测 40 个）——**不是产品缺陷**。
> 必须用补齐入口跑：`./.venv/bin/python ~/.workbuddy/tools/run_pytest.py -q <目标>`。
>
> 📌 `.venv` 的创建与依赖安装见 `README.md`「部署」章节；下文
> `./.venv/bin/python` 在 Windows 上对应 `.venv/Scripts/python`。

| 批次 | 测试文件 | 项数 | 预期耗时 |
|---|---|---|---|
| 批 1 | test_permissions、test_users、test_b1_s1_fixes、test_auth_boundary、test_infra_smoke、test_phase2_api、test_invariants | 158 | ≈5–6 分钟 |
| 批 2 | test_notices、test_recycle_backup、test_logic_consistency、test_notice_snapshot_backfill、test_bulk_and_password、test_documents、test_categories、test_validity、test_files、test_audit、test_backup_retention、test_notice_linkage_reconcile_20260929、test_p1_fixes_20260929 | 264 | ≈10–12 分钟 |
| 批 3 | test_e2e_flows、test_edge_cases、test_performance_baseline、test_concurrency、test_backup_zero_files、test_xss_defense | 99 | ≈1–6 分钟 |

命令（逐批执行，`&&` 串联即为"跑完一批再下一批"）：

```bash
.venv/bin/python -m pytest tests/test_permissions.py tests/test_users.py \
  tests/test_b1_s1_fixes.py tests/test_auth_boundary.py tests/test_infra_smoke.py \
  tests/test_phase2_api.py tests/test_invariants.py -q

.venv/bin/python -m pytest tests/test_notices.py tests/test_recycle_backup.py \
  tests/test_logic_consistency.py tests/test_notice_snapshot_backfill.py \
  tests/test_bulk_and_password.py tests/test_documents.py tests/test_categories.py \
  tests/test_validity.py tests/test_files.py tests/test_audit.py \
  tests/test_backup_retention.py tests/test_notice_linkage_reconcile_20260929.py \
  tests/test_p1_fixes_20260929.py -q

.venv/bin/python -m pytest tests/test_e2e_flows.py tests/test_edge_cases.py \
  tests/test_performance_baseline.py tests/test_concurrency.py \
  tests/test_backup_zero_files.py tests/test_xss_defense.py -q
```

## 三、失败处理规则

1. **先复跑失败项**判定性质：`.venv/bin/python -m pytest --lf -q`
   - 复跑**通过** → 偶发失败（多为时序/共享资源类）；在原批次完整复验一次，
     通过即可放行，同时评估是否需要加隔离
   - 复跑**仍失败** → 稳定回归，按堆栈定位修复
2. 定位时加 `--tb=short`；修复验证只跑相关文件/批次，不必每次全量
3. **新增测试文件**时归入语义最近的批次，并同步更新本表与 `交接记录` 的
   批次清单与基线数字
4. **新增测试只用主题式命名**（`test_<域>.py`，如 test_backup_retention）；
   历史存量的日期式/阶段式命名（test_p1_fixes_20260929、test_phase2_api、
   test_b1_s1_fixes 等）**不改名**——改名会打乱本表与 HANDOFF 的批次索引（评审 Q2，2026-10-25 固化）

## 四、何时全量、何时只跑部分

| 场景 | 跑什么 |
|---|---|
| 功能上线前 / 大重构后 / 发版前 | **三批全量** |
| 单点修复、影响面明确 | 所在批次 + 直接受影响的模块批次 |
| 前端样式/模板改动 | pytest 不受影响；跑下方浏览器走查脚本 |

## 五、浏览器走查与守卫脚本（需先启动 uvicorn 127.0.0.1:8000）

| 脚本 | 断言 | 用途 |
|---|---|---|
| `scripts/admin_journey.py` | 31 项 | 管理员全流程（含 §26 红线断言）；项数随演示数据浮动，以实跑为准（2026-09-29 勘误：原文 32 与 HANDOFF/README 的 31 不一致） |
| `scripts/user_journey.py` | 流程 + 3 项权限 | 普通用户视角全流程 |
| `scripts/modal_a11y_check.py` | 17 项 | 弹窗键盘可达性 / Promise 关闭路径 |
| `scripts/a11y_dom_check.py` | 0 问题守卫 | 前端无障碍 DOM 扫描（发现即失败） |
| `scripts/frontend_p1_check.py` | 15 项 | **前端 P1 修复的定向验证**：登录失败不自跳转、`confirmModal` 白名单标签渲染与 5 组转义攻击用例（2026-09-29 加；后端单测零 DOM 断言，JS 改动只能在这里验） |
| `scripts/extreme_samples_check.py` | 21 项 | 极端合法样本预演（上线前必跑） |
| `scripts/bench_pagination.py` | 性能报告 | 5 万条资料分页压测 |
| `scripts/theme_check.py` | 56 项 | **换肤契约**：令牌值 / 侧栏逐页高亮唯一 / 组件样式 / 交互 / 表格列数一致 / 零横向溢出 / 控制台零 error |
| `scripts/palette_audit.py` | 13 页 × 2 角色 | **配色守卫**（2026-09-29 加）：逐页沿祖先链合成有效底色算 WCAG 对比度 + 旧主题残留扫描（含 rgba 三元组） |

大改动之后：**三批全量 + 前五条走查**；涉及列表性能的改动另跑压测脚本对比基线。

### 5.1 跨平台路径口径（2026-10-26 统一）

所有脚本的**解释器路径**与**临时目录**不再硬编码，改由 `scripts/_paths.py`
统一解析（唯一真源）：

| 常量 | 含义 | Windows 实测 | Linux |
|---|---|---|---|
| `PY` | 当前解释器（`sys.executable`） | `.venv\Scripts\python.exe` | `.venv/bin/python` |
| `TMP_DIR` | 临时根（`tempfile.gettempdir()`，可用 `FMS_TMP_DIR` 覆盖） | `%LOCALAPPDATA%\Temp` | `/tmp` |
| `E2E_DATA_DIR` | E2E 隔离数据副本 | `%TEMP%ms_e2e_data` | `/tmp/fms_e2e_data` |
| `E2E_FILES_DIR` | E2E 测试文件 | `%TEMP%\e2e_files` | `/tmp/e2e_files` |

**本轮修掉一个真 bug**：此前 bash 的 `/tmp`（Windows = `%TEMP%`）与 Python 的
`Path("/tmp")`（Windows = `C:	mp`）**指向不同目录**。于是 `bootstrap_env.py`
把 E2E 数据建到 `C:	mpms_e2e_data`，`serve_e2e.sh` 却去 `%TEMP%ms_e2e_data`
找 —— 在 Windows 上**必然报「缺少 /tmp/fms_e2e_data」**（Linux 因 `/tmp` 同源
不会暴露，是跨平台盲区）。现两者都从 `_paths.py` 取值，修复后 `serve_e2e.sh`
在 Windows 上实测可正常启动。

### 5.2 走查脚本的登录等待（2026-10-26 修正）

登录成功是「**JSON 登录响应 → `window.location.href` 编码跳转**」，
`wait_for_load_state("networkidle")` **早于**跳转完成——按 networkidle 断言 URL
会读到仍在 `/login`，导致后续所有步骤因「未登录/未加载完」空转出 ⚠️。

**约定**：走查脚本登录后一律用 `page.wait_for_url(lambda u: "/login" not in u)`
等待 URL 真正离开登录页，再 `wait_for_load_state("networkidle")`。
`admin_journey.py` / `a11y_dom_check.py` / `m01_auth.py` 本来就是这么写的；
`user_journey.py` 此前漏了，本轮已补（修正后 9 步走通、0 ⚠️）。

---

## 六、全功能浏览器 E2E（旧 Playwright 自举，历史保留）

> ⚠️ 本节保留 2026-10/11 初版 Playwright E2E 的历史运行方式，仍固定 :8010 且 `bootstrap_env.py` 会复制真实 `data/`。
> 当前全功能矩阵、ego 真浏览器与隔离合成沙箱门禁以 §八 为准；不要在生产或当前 Agent 工作中运行本节命令。

> 历史完整报告：`docs/history/E2E_AUDIT_2026-09-14.md`、`docs/history/E2E_AUDIT_2026-09-29.md`；当前结果以 `交接记录` 顶部为准。

三步自举（`/tmp` 不保值，重启后必须重建）：

```bash
./.venv/bin/python scripts/e2e/bootstrap_env.py     # 造测试文件 + 数据副本 /tmp/fms_e2e_data
bash scripts/e2e/serve_e2e.sh                       # :8010 被测实例（后台常驻，勿用 nohup）
E2E_BASE_URL=http://127.0.0.1:8010 ./.venv/bin/python scripts/e2e_full_check.py [模块前缀]
```

历史基线见 `交接记录`；本文件不维护当前用例数量，执行命令仍以本段脚本为准。

> 2026-10-16 新增 `m43_password_gate.py`（Y01–Y04）：首登改密服务端闸门的浏览器回归——
> API 层 403 PASSWORD_CHANGE_REQUIRED、登录即送改密页、改密后恢复、旧令牌作废。
> 同日修了两处全量负载下偶发的用例竞态（均为固定 sleep 猜异步，非产品缺陷）：
> H01 详情页断言改为轮询 `#detail-root` 文本（该节点由 document_detail.js 异步渲染）；
> U04 旧密码失效改为「等 modal 关闭 + 有界轮询登录」。

跑完必须核对**演示库未被污染**（唯一有效证据，逐表比计数，别只看"跑完了"）。

### 六·0 内容状态矩阵（2026-10-19 起）

除既有 seed 数据外，前端状态改动必须用**隔离空库 + 1 条数据**分别验证：

- 工作台：0 条（引导态）/ 1 条（featured 焦点卡）/ 2 条（featured 双卡 + 健康行）/
  ≥3 条（dense）——0 条时零值卡与空列表隐藏；1–2 条时零值卡、搜索与 dense 双列隐藏；
  管理员与普通用户两种视角都要过（普通用户不渲染编辑入口，草稿通知不可见）。
- 资料：0 条（空态引导、无搜索/表头）/ 1–2 条（featured）/ ≥3 条（表格）。
- 通知：0 条（空态引导）/ 1–2 条（featured）/ ≥3 条（列表）。
- 通知详情：短正文（72ch 阅读列 + 侧栏）、未查看/已查看（无确认条）、
  无关联资料（compact 行）三态；桌面与 390px 各一次。
- DOCX 预览：标题/段落/列表/表格/加粗 runs 渲染为 `.docx-preview`；恶意文本仍按
  `textContent` 显示；截断时显示默认下载提示；单元测试与 G03 各一层。
- 加载：skeleton + `aria-busy`；错误：可重试 + `role="alert"`；筛选无结果：
  “清除筛选”态，不得与真正的空库态混用。
- 筛选摘要 chips：资料/通知带筛选进入页面 → chips 可见；点击单项可移除；
  「清除全部」恢复；通知类型清空后空库态能正确出现。
- 断点矩阵：资料表 601/640/700/768/900 为卡片；901/1024/1100/1199 分类树置顶、
  表格全宽且表头 sticky；1200/1440 分类树在左、表格仍无横向滚动；
  页面无横向溢出（E2E V04 已含 900/1024px）。
- 搜索入口：有页内搜索的列表页顶条输入框隐藏、`#cb-global-link` 可见且可直达
  `/documents`；无页内搜索的页面仍显示顶条输入。
- 当前验证截图由回归证据目录或走查脚本输出；不得用假数据填充空态。

### 六·1 两条必须遵守的写法（各踩过一次）

1. **不要用固定 `wait_for_timeout` 等异步回填**。
   典型：`admin/document_form.js::loadDoc()` 是 `async`，`await api.get(...)` **之后**
   才写 `titleEl.value` / `summaryEl.value` / 生效区间。若用例在响应到达前就 `fill()`，
   回填会把填好的值**覆盖回原值** → 提交的是旧值。
   症状是「**单跑必过、全量必挂**」（全量 13 分钟、20 个模块压同一实例，CPU 紧张）。
   正确写法：轮询到 `#f-title` 的值**等于后端当前值**为止
   （同一同步块内赋值，所以标题一致＝整块回填完成）：
   ```python
   want = http("GET", f"/api/v1/documents/{did}", app.admin.token)["body"]["title"]
   s.page.wait_for_function(
       "(w)=>{const e=document.getElementById('f-title'); return !!e && e.value===w;}",
       arg=want, timeout=15000)
   ```
   已修：`m13_document_edit.py::_open_edit`、`m20_notice_create.py::N06`。

2. **控制台噪声必须逐桶记长度**，不能两桶拼接后切片 —— 否则新增噪声会从后一个桶取，
   把上一用例的历史 403 反复算到后续用例头上（一次能造 20+ 假失败）。

### 六·2 默认预期行为（**不是缺陷**，别当 bug 报）

- 项目**零原生 `confirm()/alert()`**，全走 `ui.confirmModal` → `#modal-root [data-confirm]`，
  用 `harness.confirm_modal()`。挂 `page.on("dialog")` 是死代码。
- 管理页删除语义：分类 / 部门 / 标签 / 通知类型均先确认后删；用户物理删除仍按 `项目规范` 锁定规则免二次确认。通知发布/删除、备份恢复也有确认。
- 预期 4xx（重名 409、越权 403）要显式 `app.allow_noise()`。

---

## 七、lint 与类型检查（2026-10-25 固化，评审 M2/M3/Q2）

工具使用当前虚拟环境解释器；目标运行时
**3.10+**（2026-10-25 依赖安全升级后由 3.9 抬升，fastapi 0.133 / starlette 1.3
等 Requires-Python >=3.10 实测），ruff 与 mypy 的 target 均按 3.10 固化。配置唯一真源
`pyproject.toml`（不要再带 CLI 参数跑，参数漂移=配置失效）。

```bash
python -m ruff check          # lint：全仓零违规（基线 2026-10-25，42 处清零）
python -m mypy                # 类型：只查 app/，零错误（基线 2026-10-25，67 文件）
python -m pip_audit -r requirements.txt --no-deps   # 依赖漏洞：零通告（基线 2026-10-25，51→0）
```

纪律：

1. **改完代码两条都要过**，且不允许用 `# noqa` 无理由豁免——豁免必须带理由注释
   （现有 8 处 BLE001/E402 均如此）。
2. **存量零违规是硬基线**：新代码不得引入新发现；顺手修复同文件存量可以，
   但不为凑数跨文件大扫除。
3. mypy 首跑（2026-10-25）即揪出 1 处真实类型缺陷（`notice_service.py` 补偿路径
   可空路径直传），已修——这是它留任的理由；不开启严格模式，不追覆盖率数字。
4. 无 CI / 无 git remote（2026-10-25 核实），三条命令全凭改动者自觉执行。
5. **本地提交守门（2026-10-26 加）**：`scripts/git-hooks/pre-commit` 在每次
   `git commit` 前自动跑 ruff + mypy（实测合计 < 1 秒，不会拖慢提交）。
   安装一次即可：

   ```bash
   bash scripts/git-hooks/install.sh
   ```

   - 钩子本身不进版本库（`.git/hooks/` 不受 Git 管理），所以用脚本分发；
     换机器 / 重新 clone 后需再装一次。
   - 需要连全量测试一起跑：`FMS_RUN_TESTS=1 git commit ...`（并行下约 **30 秒**，
     只在重大改动时用）。
   - 未做 pre-push 跑测试：本仓库无 remote，pre-push 永不触发；且每次提交
     跑测试必然被 `--no-verify` 绕过，等于没有守门。
   - 紧急绕过：`git commit --no-verify`（按 AGENTS §34 纪律，不应作为日常）。

---

## 八、全功能矩阵 / ego 真浏览器门禁（2026-11-11）

新增 `scripts/full_matrix/` 作为独立沙箱协调器；`.mjs` 模板固化在
`scripts/full_matrix/ego_templates/`，执行时同步到本轮忽略目录
`_agent_full_matrix_<run_id>/`，避免证据文件进入 Git。

```powershell
& ".venv\Scripts\python.exe" -m scripts.full_matrix.run prepare
& ".venv\Scripts\python.exe" -m scripts.full_matrix.run g0
& ".venv\Scripts\python.exe" -m scripts.full_matrix.run g1
& ".venv\Scripts\python.exe" -m scripts.full_matrix.run g4
& ".venv\Scripts\python.exe" -m scripts.full_matrix.run ego round-00-smoke
# ... 依次执行 round-01-roles 至 round-05-destructive ...
& ".venv\Scripts\python.exe" -m scripts.full_matrix.run ego round-99-final
& ".venv\Scripts\python.exe" -m scripts.full_matrix.run g8
& ".venv\Scripts\python.exe" -m scripts.full_matrix.run report
```

约束：

- 使用独立 `%TEMP%\docnotice-e2e\<run_id>` 沙箱与唯一端口；真实 `data/` 和 `.env` 只读，G1 会临时停止真实 8000 实例，结束后恢复。
- `FMS_TMP_DIR` 统一 E2E 文件、journey、theme/a11y 等脚本的临时目录；不得直接运行会复制真实 `data/` 的旧 `bootstrap_env.py`。
- `TRUSTED_HOSTS` 必须包含实际访问域名/IP；本地测试默认含 `localhost,*.localhost,127.0.0.1,::1,testserver`。
- ego 坚持一个 TaskSpace 多 Page；`.mjs` 通过 UTF-8 文件执行，中文/账号/断言留在文件内，不用 PowerShell heredoc 或管道。
- G0/G1/G4/G8 与 ego 全部 PASS、真实数据零差异、报告 `releaseDecision=PASS` 才算放行。

### 八·1 附件 / 回收站专项（2026-11-12）

- `tests/test_attachment_recycle_full.py`：API、数据库、文件系统 14 条专项断言。
- `scripts/e2e/m72_attachment_recycle_full.py`：Playwright 14 个 case，覆盖附件方案 A、替换、删除、恢复、归档恢复、永久删除、通知回收站、清空回收站、权限、反向锁定。
- `scripts/full_matrix.run ego round-06-attachment-recycle`：ego 真浏览器 16 条 admin/user/anon 交叉断言。
- 运行前提：先 `g1` 启隔离 :8121；单项 Playwright 可用 `E2E_BASE_URL=http://127.0.0.1:8121 python scripts/e2e_full_check.py Z`。

### 八·2 通知正文 / 关联资料搜索专项（2026-11-12）

- `tests/test_notice_content_search.py`：通知标题/正文命中、普通用户范围外不泄露、纯文件通知不伪造文件正文、关联资料 `title/summary` 契约与页面文案守卫。
- `scripts/e2e/m73_notice_content_search.py`：Playwright Q01–Q04，覆盖管理员/普通用户通知列表真浏览器搜索、关联资料候选按说明命中、已选保留与反向锁定。
- `scripts/full_matrix.run ego round-07-notice-search`：ego 真浏览器管理员/普通用户双线证据；已加入 `ROUND_ORDER`。
- 运行前提：先 `g1` 启隔离 :8121；单项 Playwright 可用 `E2E_BASE_URL=http://127.0.0.1:8121 python scripts/e2e_full_check.py Q`。
- 本专项只查数据库里管理员手填的标题/正文/资料说明，不解析 PDF/DOCX/XLSX/XLS 文件正文。

### 八·3 三方交叉全功能验收专项（2026-11-16）

ego 轮次由 8 轮扩到 12 轮（181 条真浏览器断言），新增四轮补齐此前的 ego 覆盖缺口：

| 轮次 | 覆盖 | 断言数 |
|---|---|---:|
| `round-08-admin-vocab` | 分类 4 级上限/同级重名/整树删除先确认；标签与部门 CRUD+确认；「废止/替换」禁删禁改名；用户新建→停用→无法登录；内容责任链；**用户物理删除免确认弹窗**（反向断言）；普通用户写接口 403 | 13 |
| `round-09-validity-files` | 九类日期边界与闭区间；「暂无失效日期」文案（含禁用词扫描）；详情页历史日期查询；R1 倒挂；R2 覆盖通道（确认+原因+审计）；替换文件旧件进回收站且版本+1；多图合成一份多页 PDF | 8 |
| `round-10-notice-files` | 通知文件上传/预览/下载（含 `no-store`）；详情页预览入口真实渲染；`.pptx` 作通知文件被拒；通知↔资料双向关联；废止联动截断→取消回滚→恢复重放 | 7 |
| `round-11-backup-audit` | 仅库 vs 完整备份 `file_count` 差异；只读校验；备份→改动→恢复回滚；审计按操作类型筛选；批量删除只追加**一条** `audit_delete`；无「清空全部」入口 | 8 |

- 对应 pytest 回归：`tests/test_notice_linked_visibility.py`（关联资料可见范围泄露，5 条）、`tests/test_invariants.py::TestPurgeUserAndDepartment::test_purge_user_referenced_by_document_target_ok`（物理删除 500）、`tests/test_full_matrix_runner.py`（验收框架证据完整性，7 条）。
- 运行：`python -m scripts.full_matrix.run ego <round-id>`；`ROUND_ORDER` 已含 08–11。
- **Page 预算**：ego 单 TaskSpace 上限 8 个 Page，运行器在每轮结束调用 `ego_templates/lib/recycle_pages.mjs` 回收，轮次脚本无需自行清理。
- 证据与功能矩阵：`_agent_full_matrix_<run_id>/acceptance/feature-matrix.md`。
