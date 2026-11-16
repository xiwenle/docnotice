# 依赖安全审计（2026-10-19）

> 本文件按时间顺序记录安全审计与复测；当前状态只看最后一节或 `交接记录` 顶部，不要把开头基线当成当前漏洞状态。

- 工具：`pip-audit -r requirements.txt`
- 基线：升级前 **36 个已知漏洞 / 6 个包**
- 处置后：**14 个已知漏洞 / 4 个包**（全部为当前镜像或 FastAPI 版本下无可用修复版本）
- 结论：**不阻塞当前内网部署**；CI 保留审计步骤（可见、不阻断），
  待镜像出现修复版本或 FastAPI 支持 Starlette 1.x 时立即升级。

## 1. 已升级

| 包 | 旧版本 | 新版本 | 原因 |
|---|---|---|---|
| PyJWT | 2.13.0 | **2.15.1** | 修复 2.14/2.15 系列 JWT 校验漏洞 |
| pypdf | 6.16.2 | **6.21.1** | 修复 6.17–6.19 系列 PDF 解析漏洞 |

回归：`test_auth_boundary`、`test_files`、`test_documents`、`test_notices`、
`test_notice_linkage_reconcile_20260929`、`test_p1_fixes_20260929` 全部通过
（升级后实跑，详见 HANDOFF 2026-10-19 节）。

## 2. 剩余项与可达性

| 包 | 当前 | 可用修复 | 本项目的可达性判断 |
|---|---|---|---|
| python-multipart | 0.0.20 | 0.0.22–0.0.31（**镜像无包**） | 路径穿越项要求 `UPLOAD_DIR`+`UPLOAD_KEEP_FILENAME`，本项目不使用；表单 DoS 项仅管理员上传入口可达，且受 `MAX_UPLOAD_SIZE_MB` 与内网边界约束 |
| starlette | 0.49.3 | 1.0.1–1.3.1（FastAPI 0.128.8 要求 <0.50） | Host/path 重建项需依赖 `request.url` 做鉴权，本项目鉴权走 Cookie/Bearer + DB 复核，不依赖重建 URL 的 host；**Windows StaticFiles UNC/SSRF 项需在 Windows 部署时额外防护**（见 §3） |
| anyio | 4.12.1 | 4.14.2（**镜像无包**） | 本项目不使用 anyio 进程池；无出站 TLS 连接 |
| python-dotenv | 1.2.1 | 1.2.2（**镜像无包**） | 仅启动时读取 `.env`，不调用 `set_key/unset_key`；需本机写权限才可利用 |

剩余 ID：`python-multipart` PYSEC-2026-1852/3036/3037/3038/3039/3040；
`starlette` PYSEC-2026-161/248/249/2280/2281；`anyio` PYSEC-2026-4024/4025；
`python-dotenv` PYSEC-2026-2270。

## 3. 部署侧对冲（Windows 必读）

Starlette 0.49.3 的 StaticFiles 在 Windows 上对 UNC 路径（`\\host\share`）可能触发
出站 SMB 连接。当前 FastAPI 版本无法升级 Starlette，Windows 部署必须至少做到：

1. 反向代理/Nginx 拒绝含反斜杠或 UNC 形态的 URL；
2. 服务账号禁止出站 SMB（防火墙出站 445 端口拒绝）；
3. 优先使用 Linux 部署；Windows 仅限可信内网。

## 4. 升级触发条件

满足任一条件时重新执行审计并升级：

- 内部 PyPI 镜像出现 `python-multipart>=0.0.31`、`anyio>=4.14.2`、`python-dotenv>=1.2.2`；
- FastAPI 新版本声明支持 `starlette>=1.3.1`；
- 准备从内网迁移到公网/跨网段访问（风险等级上调）。

## 5. 纪律

- 不盲目升级 `bcrypt`/`passlib`（4.0.1 的 pin 有兼容性原因，见 requirements.txt 注释）。
- 依赖升级后必须跑对应模块测试 + 三批全量；涉及 JWT/PDF/上传的升级还要跑 E2E。
- 审计结果与本文件同步更新；CI 的 `pip-audit` 步骤非阻断，但每次流水线都可见。

## 6. 2026-11-03 复测（双线合并后）

- pypdf 6.21.2 在本轮复测中带 **7 条 CVE**（CVE-2026-119203/119204/119205/119206/
  119207/119208/119209），修复线 6.21.3 / 6.21.4 → 已升至 **6.21.4**。
  该包在生产 PDF 上传校验（`file_safety.py`）与预览路径上在用，属真实可达面。
- 复测命令：`python -m pip_audit -r requirements.txt --no-deps` → **No known vulnerabilities found**。
- 回归：`test_files` / `test_documents` / `test_invariants` / `test_xss_defense`
  共 **142 passed**；合并后全量 **590 passed**。
