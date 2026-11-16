# 数据库设计（DATABASE.md）

> 项目：公司内部资料与通知管理系统 · v1
> 技术：SQLAlchemy 2.0 声明式 + SQLite（数据库仅存元数据与关系，文件二进制落 `data/`，见 DESIGN.md）
> 关系：本文是 [DESIGN.md](./DESIGN.md) 的数据层，接口见 [API.md](./API.md)

---

## 0. 总则

- 主键统一为自增整数 `id`（内部随机文件名为 `file_records.internal_name`，是存储标识而非主键）。
- 时间约定：
  - `Date` = 资料时效（`effective_from/effective_to`，闭区间，§14）；
  - `DateTime` = 操作时间/通知创建时间/生效时间（`published_at/effective_at`）与阅读时间（`read_at`）。
- 所有表含 `created_at`；可变更表含 `updated_at`。
- 软删除为主（服务层规则），`deleted_at/archived_at` 置位，而非删除行（§26）。
- SQLite 启用 `PRAGMA foreign_keys=ON`、`journal_mode=WAL`、`busy_timeout=5000`；文件型数据库连接池默认 `DB_POOL_SIZE=20` + `DB_MAX_OVERFLOW=20`，池等待 `DB_POOL_TIMEOUT_SECONDS=10`，超时由 API 返回 `503 DB_BUSY`（可重试），不得退化成 500。`get_db` 必须在请求结束时关闭 Session。
- 迁移用自研脚本：`scripts/migrate.py`（`--check` 预览）+ `scripts/migrations/NNN_*.py`
  幂等迁移，按 `is_applied` 检测跳过；无 Alembic 依赖（完整表清单见本文 §2 各表小节；2026-09-29 勘误：原文写「见 §11」，§11 实为 notice_targets）。

---

## 1. users（用户，§23、§24）

| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | Integer | PK autoincrement | |
| username | String(64) | UNIQUE NOT NULL | 登录名 |
| password_hash | String(255) | NOT NULL | bcrypt 哈希（`passlib[bcrypt]`，cost=`BCRYPT_ROUNDS` 默认 12；2026-09-29 勘误，原文写 Argon2id 与实现不符） |
| display_name | String(128) | NOT NULL | 姓名 |
| email | String(128) | NULL | |
| phone | String(32) | NULL | |
| role | String(16) | NOT NULL DEFAULT 'user' CHECK IN ('user','admin') | 角色 RBAC（权限模型 v3：超管已并入 admin） |
| is_active | Boolean | NOT NULL DEFAULT 1 | 停用=false 拒登，后端复核 |
| auth_source | String(16) | NOT NULL DEFAULT 'local' | 账号来源标识；当前仅 local |
| external_uid | String(128) | NULL | 外部身份 ID；仅保留字段，不使用、不接入 |
| must_change_password | Boolean | NOT NULL DEFAULT 0 | 初始密码强制改（可选） |
| token_version | Integer | NOT NULL DEFAULT 0 | 单点登录锚点（2026-09-03）：登录/改密/重置自增，JWT `ver` 不等即 401 |
| last_login_at | DateTime | NULL | |
| created_at | DateTime | NOT NULL | |
| updated_at | DateTime | NOT NULL | |

索引/约束：
- `uq_users_username` UNIQUE(username)
- `uq_users_auth_ext` UNIQUE(auth_source, external_uid)
- `ix_users_role`, `ix_users_active`（列表筛选）

关系：
- users → user_departments 1:N；→ notice_reads 1:N；→ audit_logs 1:N（操作人）；→ file_records 1:N（上传人）。

---

## 2. departments（部门，§21）

| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | Integer | PK | |
| name | String(128) | NOT NULL | 部门名（未删除行内唯一，见下方索引） |
| description | String(255) | NULL | |
| is_active | Boolean | NOT NULL DEFAULT 1 | 软停用（仍有启用成员时禁删，见 API） |
| created_at | DateTime | NOT NULL | |
| updated_at | DateTime | NOT NULL | |

索引：`uq_departments_name_active` UNIQUE(name) WHERE is_active=1（已删除部门
不占名；旧全行约束由 `scripts/migrate_partial_unique.py` 迁移）、
`ix_departments_is_active`

> 2026-11-03：`code` 列与 `uq_departments_code_active` 索引已按产品决策
> 移除（迁移 `008_drop_department_code.py`）。

关系：departments → users 通过 user_departments M:N。

---

## 3. user_departments（用户-部门 M:N，§21）

| 字段 | 类型 | 约束/默认 |
|---|---|---|
| id | Integer | PK |
| user_id | Integer | FK→users.id NOT NULL |
| department_id | Integer | FK→departments.id NOT NULL |

- `uq_user_departments` UNIQUE(user_id, department_id)
- `ix_user_departments_department_id`（按部门反查成员时不需 join users 表先扫）

关系：一个用户可属多个部门；发布给部门时展开为成员集合。

---

## 4. categories（分类，§9：一级 ~ 四级树）

| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | Integer | PK | |
| name | String(128) | NOT NULL | 分类名 |
| parent_id | Integer | FK→categories.id NULL | NULL=一级；非 NULL=其上级（可为一~三级） |
| sort_order | Integer | NOT NULL DEFAULT 0 | 同级排序 |
| is_active | Boolean | NOT NULL DEFAULT 1 | 2026-09-03 起删除=直接物理删，API 不再产生 0 值行；列保留用于历史行兼容（迁移保留的有引用软删行不可见、不占名） |
| created_at | DateTime | NOT NULL | |
| updated_at | DateTime | NOT NULL | |

约束/索引：
- `uq_categories_parent_name_active` UNIQUE(parent_id, name) WHERE is_active=1（同级下名称唯一，只对未删除行生效——已删除分类不占名；旧全行约束由 `scripts/migrate_partial_unique.py` 迁移。SQLite 中 NULL 视为不同——一级分类唯一性由 service 层校验：`WHERE parent_id IS NULL AND name=?`）
- `ix_categories_parent_id`
- **深度约束（service 层强制 `MAX_DEPTH=4`）**：沿 parent 祖先链累计深度，新节点超过四级即 `400 VALIDATION_ERROR`；重挂 parent 不得成环（不能挂到自身或其子孙之下）。2026-09 由二级放宽为四级。
- 保护：有子分类（任何状态）或被 documents 引用（含已归档），禁止删除（返回 `IN_USE`，见 API）。2026-09-03 起删除=直接物理删（行删除、名字释放），历史零引用软删行由启动迁移清理。

关系：categories → documents 1:N；categories → categories（自引用 parent-child）。

---

## 5. tags（标签，§9）

| 字段 | 类型 | 约束/默认 |
|---|---|---|
| id | Integer | PK |
| name | String(64) | UNIQUE NOT NULL |
| created_at | DateTime | NOT NULL |

- 扁平词表，不参与分类层级。
- 有文档引用时禁止删除（`IN_USE`）。

关系：tags → documents 通过 document_tags M:N。

---

## 6. file_records（存储文件元数据，§8、§10、§11、§27）

> 一个“文件记录”对应磁盘上一个文件（资料当前/历史文件、通知文件共用本表，用 `owner_type` 区分）。数据库**不存二进制**。

| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | Integer | PK | 内部唯一文件 ID |
| owner_type | String(16) | NOT NULL CHECK IN ('document','notice') | 归属类型 |
| owner_id | Integer | NOT NULL | 归属对象 id（documents.id / notices.id） |
| original_filename | String(255) | NOT NULL | 清洗后的原始文件名（中文正常，展示/下载用） |
| internal_name | String(64) | UNIQUE NOT NULL | 存储名：`uuid4().hex + ext`，服务器唯一标识 |
| file_type | String(8) | NOT NULL CHECK IN ('pdf','docx','xlsx','xls','pptx','jpg','jpeg','png','gif','webp') | 归一化类型（2026-11-03 扩图片 → 迁移 007；2026-11-06 扩 pptx → 迁移 012。pptx **只允许作附件**，主文件白名单不含它） |
| extension | String(8) | NOT NULL | 如 `.pdf` |
| mime_type | String(128) | NOT NULL | 校验后 MIME |
| size_bytes | Integer | NOT NULL | |
| sha256 | String(64) | NOT NULL | 内容指纹（去重/校验/恢复） |
| storage_rel_path | String(255) | NOT NULL | 相对 `DATA_DIR` 的路径，按内部名前 2 位分片，如 `documents/ab/abc….pdf`（防穿越：服务端派生，不接受入参） |
| status | String(16) | NOT NULL DEFAULT 'active' CHECK IN ('active','recycled') | active=在用；recycled=在回收站 |
| uploaded_by | Integer | FK→users.id NULL | 上传人 |
| uploaded_at | DateTime | NOT NULL | |
| created_at | DateTime | NOT NULL | |

索引：
- `ix_file_records_owner(owner_type, owner_id)`
- `ix_file_records_sha256`（重复文件检测）
- `uq_file_records_internal_name` UNIQUE(internal_name)

关系：documents.current_file_id → file_records.id（1:1）；recycle_bin.file_record_id → file_records.id（1:N）。通知文件：notices.file_id → file_records.id。

---

## 7. documents（资料，§8、§13、§14、§15、§16）

| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | Integer | PK | |
| title | String(255) | NOT NULL | 资料标题 |
| summary | Text | NULL | 说明 |
| category_id | Integer | FK→categories.id NULL | 归属分类（一~四级任意节点，含非叶子） |
| effective_from | Date | NOT NULL | 生效日期（必填） |
| effective_to | Date | NULL | 失效日期（可空=暂无失效日期，§13） |
| effective_note | String(255) | NULL | 时效说明（已废弃：界面/接口不再读写，列保留以防历史数据丢失） |
| audience_scope | String(16) | NOT NULL DEFAULT 'all' | **可见范围**（2026-11-03）：all/department/user。管理员不受限；越权访问返回 404 |
| current_file_id | Integer | FK→file_records.id NULL | 当前文件（§8：单当前文件；可为空=暂无文件） |
| attachment_file_id | Integer | FK→file_records.id NULL | **附件**（2026-11-06）：每资料最多 1 个，永不预览、只供下载。与 current_file 是两条独立的线，不要求二者格式对应。存为第二条 `file_records`（owner_type='document', owner_id=本资料 id） |
| rev_count | Integer | NOT NULL DEFAULT 1 | 替换次数（第几版标识，仅展示） |
| allow_download | Boolean | NOT NULL DEFAULT 0 | **已废弃**（2026-11-04）：下载权限改为「可见即可下载」，代码不再读写；列保留以防历史数据丢失。历史语义：是否允许普通用户下载原始文件（该开关已废弃） |
| archived_at | DateTime | NULL | 置位=已归档/入回收站（§26） |
| archived_by | Integer | FK→users.id NULL | 归档人 |
| archive_reason | String(255) | NULL | 归档原因 |
| created_by | Integer | FK→users.id NULL | |
| updated_by | Integer | FK→users.id NULL | |
| created_at | DateTime | NOT NULL | |
| updated_at | DateTime | NOT NULL | |

> **注意：没有 `status` 列**——时效状态由 `validity_service.compute_status` 按指定日期动态计算（§14、§15），归档用 `archived_at` 标记（不混入日期状态）。

索引：
- `ix_documents_category_id`
- `ix_documents_effective_from`, `ix_documents_effective_to`（按日期过滤/统计）
- `ix_documents_archived`(archived_at)
- 搜索用 `LIKE '%kw%'` 命中 title/summary（第一版规模；中文全文索引 FTS5 留作后续优化，不扩产）
  （2026-11-03：`doc_no` 列已随产品决策删除，搜索面相应收窄为标题 + 说明）

约束（service 层，见 DESIGN §7.2）：
- R1：`effective_from <= effective_to`（后者存在时）；
- R2：改既有窗口重叠需 `confirm_overwrite`，否则 409。

关系：
- documents → file_records（current_file_id，1:1）；
- documents → file_records（历史文件 owner_type='document'，1:N 经 recycle_bin）；
- documents → categories N:1；→ tags 经 document_tags M:N；→ notices 经 notice_documents M:N；→ notice_reads 无关（略）。

---

## 8. document_tags（资料-标签 M:N，§9）

| 字段 | 类型 | 约束/默认 |
|---|---|---|
| id | Integer | PK |
| document_id | Integer | FK→documents.id NOT NULL |
| tag_id | Integer | FK→tags.id NOT NULL |

- `uq_document_tags` UNIQUE(document_id, tag_id)
- `ix_document_tags_tag_id`

---

## 9. notices（通知，§17、§18、§19、§20、§21、§22）

| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | Integer | PK | |
| title | String(255) | NOT NULL | 标题 |
| content | Text | NULL | 纯文字正文（可为空=仅文件） |
| notice_type | String(32) | NULL | 自定义类型词表（2026-09 起改为 notice_types 表；种子六类：发布/修订/替换/废止/延期/其他）。**2026-11-03 起可空**：未选类型时为 NULL，且不触发废止/替换联动 |
| status | String(16) | NOT NULL DEFAULT 'published' CHECK IN ('published','cancelled') | 状态机（2026-11-04 草稿状态下线，迁移 011 删掉存量 draft 行并收窄 CHECK） |
| published_at | DateTime | NULL | 创建时间（创建时写入，§17） |
| effective_at | DateTime | NULL | 生效时间（表单文案为“生效时间”，可空=创建即生效，§17） |
| deadline_at | DateTime | NULL | 截止时间（可选；2026-11-03 起仅作展示，不影响查看） |
| file_id | Integer | FK→file_records.id NULL | 通知原始文件（存在性由 owner_type='notice' 一致） |
| created_by | Integer | FK→users.id NULL | |
| updated_by | Integer | FK→users.id NULL | 修改人（§22 已读后修改强制记录） |
| updated_at | DateTime | NOT NULL | |
| modified_reason | String(255) | NULL | 已读后修改原因（§22） |
| cancel_reason | String(255) | NULL | 取消原因 |
| auto_expired_docs | Text | NULL | 发布「废止/替换」前关联资料的原 `effective_to` 快照（JSON）；取消/恢复时据此回滚（§18 联动） |
| created_at | DateTime | NOT NULL | |

索引：`ix_notices_status`、`ix_notices_type`、`ix_notices_published_at`、`ix_notices_effective_at`

## 9a. notice_types（自定义通知类型词表，2026-09 新增）

| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | Integer | PK | |
| name | String(32) | NOT NULL UNIQUE | 类型名称 |
| sort_order | Integer | NOT NULL DEFAULT 0 | 排序 |
| is_active | Boolean | NOT NULL DEFAULT 1 | 停用后新建通知下拉不显示 |
| created_at | DateTime | NOT NULL | |

关系：
- notices → file_records 1:1（file_id）；→ documents 经 notice_documents M:N；
- notices → notice_targets 1:N（阅读对象规则）；→ notice_reads 1:N（物化阅读快照）；
- notices.notice_type ↔ notice_types.name（逻辑关联；被引用类型禁止删除 IN_USE）。

**发布语义（service 层，非列）**：
1. `status='published'`、`published_at=now`；
2. 目标展开为具体用户 → 批量插入 `notice_reads`（快照，§21）；
3. 若 `notice_type in ('废止','替换')` 且关联资料：自动把关联资料 `effective_to` 置为 `effective_at` 的日期部分（§7.3/DESIGN），冲突则回滚；
4. 写审计 `publish_notice`。

---

## 10. notice_documents（通知-资料 M:N，§19）

| 字段 | 类型 | 约束/默认 |
|---|---|---|
| id | Integer | PK |
| notice_id | Integer | FK→notices.id NOT NULL |
| document_id | Integer | FK→documents.id NOT NULL |

- `uq_notice_documents` UNIQUE(notice_id, document_id)
- `ix_notice_documents_document_id`（按资料查关联通知）

---

## 11. notice_targets（阅读对象规则，§17、§21）

读取目标共三种（全体 / 指定部门 / 指定用户），发布时合并展开为快照。

| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | Integer | PK | |
| notice_id | Integer | FK→notices.id NOT NULL | |
| target_type | String(16) | NOT NULL CHECK IN ('all','department','user') | |
| department_id | Integer | FK→departments.id NULL | target_type='department' 时必填 |
| user_id | Integer | FK→users.id NULL | target_type='user' 时必填 |
| created_at | DateTime | NOT NULL | |

索引/约束：
- `ix_notice_targets_notice_id`
- 组合唯一 `uq_notice_targets` ON (notice_id, target_type, department_id, user_id)（SQLite 对 NULL 不去重，重复行由 service 层去重 + 'all' 每通知仅一行）
- CHECK（service 层）：`('all' ⇒ department_id IS NULL AND user_id IS NULL)`、`('department' ⇒ department_id NOT NULL)`、`('user' ⇒ user_id NOT NULL)`

展开规则（发布时，`notice_service.build_audience`）：
- `all` → 全部 `is_active=1` 用户；
- `department` → 该部门下（含 user_departments 关联）且激活的用户；
- `user` → 指定且激活的用户；
- 并集去重。

---

## 12. notice_reads（阅读快照，§20、§21）

> 发布时物化：每个需要阅读的用户一行。统计以此表为准，只算“实际需阅读人员”。

| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | Integer | PK | |
| notice_id | Integer | FK→notices.id NOT NULL | |
| user_id | Integer | FK→users.id NOT NULL | |
| read_at | DateTime | NULL | 打开详情时记录（首次覆盖） |
| read_ip | String(64) | NULL | |

索引/约束：
- `uq_notice_reads` UNIQUE(notice_id, user_id)
- `ix_notice_reads_user_id`（用户"我的通知"）

> 2026-11-03：`confirmed_at` / `confirm_ip` / `ix_notice_reads_confirmed`
> 随确认功能下线一并删除。

统计口径（§21，2026-11-03 修订）：
- 应阅读 = COUNT(*) **排除管理员**（`users.role != 'admin'`）与停用用户
- 已查看 = COUNT(read_at IS NOT NULL)，未查看 = 应阅读 − 已查看
- 只有「已查看/未查看」一个维度（确认功能已下线）
- 快照仍会为管理员物化一行，只是不进统计分母——管理员能看全部通知是
  管理权限，不代表被要求阅读

---

## 12.1 document_targets（资料可见范围的具体对象，2026-11-03）

> 与 `notice_targets` 结构同构，但语义不同：通知决定「谁必须读」，
> 资料决定「谁可以看」。scope=all 时不写任何行。

| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | Integer | PK | |
| document_id | Integer | FK→documents.id NOT NULL | |
| target_type | String(16) | NOT NULL CHECK IN ('all','department','user') | |
| department_id | Integer | FK→departments.id NULL | target_type=department 时填写 |
| user_id | Integer | FK→users.id NULL | target_type=user 时填写 |
| created_at | DateTime | NOT NULL | |

索引/约束：
- `uq_document_targets` UNIQUE(document_id, target_type, department_id, user_id)
- `ix_document_targets_document_id` / `_department` / `_user`

可见性判定（`app/services/document_visibility.py`）：
- 管理员 → 恒可见
- 普通用户 → `audience_scope='all'` 或命中其部门或命中其本人
- 列表 / 搜索 / 分页 total / 详情 / 文件 / 预览全部走同一条件

---

## 13. audit_logs（操作日志，§25、§33）

| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | Integer | PK | |
| user_id | Integer | FK→users.id NULL | NULL=系统自动（如自动失效写入） |
| username | String(64) | NULL | 操作人快照（防删除后失联） |
| action | String(32) | NOT NULL | 常量表（见 DESIGN §9） |
| object_type | String(32) | NULL | 实测取值（`grep object_type= app/services/`）：document / notice / notice_type / user / department / category / tag / recycle_bin / backup / audit_log。**无 `file`**（2026-09-29 勘误） |
| object_id | Integer | NULL | |
| object_label | String(255) | NULL | 人类可读对象名（标题/用户名） |
| detail | Text | NULL | JSON 字符串（新旧值、原因、IP 等） |
| ip | String(64) | NULL | |
| user_agent | String(255) | NULL | |
| created_at | DateTime | NOT NULL | |

索引：`ix_audit_logs_created_at`、`ix_audit_logs_action`、`ix_audit_logs_user_id`、`ix_audit_logs_object(object_type, object_id)`

写入规则：与业务操作同事务（原子，见 DESIGN §9）；`detail` 为 JSON 序列化。

---

## 14. backup_records（备份记录，§28）

| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | Integer | PK | |
| backup_type | String(16) | NOT NULL DEFAULT 'full' CHECK IN ('full','database') | full=库+文件；database=仅库 |
| backup_path | String(255) | NOT NULL | 相对 `DATA_DIR` 的备份目录 |
| manifest_path | String(255) | NULL | manifest.json 相对路径 |
| size_bytes | Integer | NULL | 备份总体积 |
| db_sha256 | String(64) | NULL | 库快照指纹（恢复校验） |
| file_count | Integer | NULL | 业务文件数 |
| status | String(16) | NOT NULL DEFAULT 'completed' CHECK IN ('completed','failed','restored','purged') | restored=曾用于恢复；purged=已删除备份（含保留策略自动清理，审计 backup_delete + auto_cleanup） |
| source | String(8) | NOT NULL DEFAULT 'manual'（迁移 003） | manual=管理员手动；auto=调度自动备份 |
| note | String(255) | NULL | |
| created_by | Integer | FK→users.id NULL | |
| created_at | DateTime | NOT NULL | |
| restored_at | DateTime | NULL | 最近一次用于恢复的时间 |
| restored_by | Integer | FK→users.id NULL | |

索引：`ix_backup_records_created_at`

---

## 15. recycle_bin（回收站，§8、§26）

| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | Integer | PK | |
| document_id | Integer | FK→documents.id NULL | 关联资料（归档/删除资料时） |
| file_record_id | Integer | FK→file_records.id NULL | 被替换/归档/删除的文件记录（无文件资料被删时为空，仍留档） |
| attachment_file_record_id | Integer | FK→file_records.id NULL | **附件**（2026-11-06）：归档资料时主文件与附件**共用同一条记录**（此处同填），恢复必须两个一起；单独替换/删除附件时只有本字段有值 |
| reason | String(16) | NOT NULL CHECK IN ('replaced','archived','deleted','attachment_deleted') | replaced=文件被替换；archived=资料归档；deleted=资料删除（软）；attachment_deleted=**附件被单独删除**（不影响资料状态） |
| origin_rel_path | String(255) | NULL | 进入回收站前相对路径（恢复时按需回写） |
| deleted_by | Integer | FK→users.id NULL | |
| deleted_at | DateTime | NOT NULL | |
| restored_at | DateTime | NULL | |
| restored_by | Integer | FK→users.id NULL | |
| purged_at | DateTime | NULL | 永久删除时间（admin，单次确认 confirm=true） |
| purged_by | Integer | FK→users.id NULL | |

索引：`ix_recycle_bin_document_id`、`ix_recycle_bin_status`(deleted_at)

流转：
- 替换文件 → reason='replaced'（文件进回收站，资料仍可用）；
- 归档/删除资料 → reason in ('archived','deleted')，且 `documents.archived_at` 置位、`current_file_id` 与 `attachment_file_id` 对应记录的 `status='recycled'`。**只产生一条记录**，两个 file id 同填；
- 单独替换附件 → reason='replaced'，只填 `attachment_file_record_id`；
- 单独删除附件 → reason='attachment_deleted'，只填 `attachment_file_record_id`，**资料归档状态不变**。
  刻意不复用 'deleted'：那个分支在恢复时会连带取消资料归档，删个附件把整份资料恢复出来是事故；
- 恢复 → `restored_at/by` 置位、文件 `status='active'`。archived/deleted 条目**主文件与附件一起恢复**（不提供单独恢复其中一个）；attachment_deleted 条目恢复附件文件并重新挂载到原资料；若资料已归档或已有当前附件则拒绝，replaced 条目仍只回文件库、不顶替当前文件；`documents.archived_at` 在资料归档恢复时清空；
- 永久删除 → 两个 file id 对应的物理文件**都删**（否则附件变成磁盘孤儿）；
- 永久删除 → `confirm=true`（仅 admin，单次确认），物理删文件 + `purged_at/by`，整行软标记（不清行，保留审计线索）；
- 一键清空 → `POST /recycle-bin/purge-all`：清空**全部**未处理条目（不认列表的 reason 筛选），完成后写一条汇总审计（`via=purge_all`）。

---

## 16. ER 关系图（文本）

```
users 1─N user_departments N─1 departments
users 1─N file_records(上传人)      notices 1─N audit_logs
users 1─N audit_logs(操作人)        users 1─N notice_reads
tags N─M document_tags M─N documents N─1 categories(parent self)
documents 1─1 file_records(current_file_id)      ← 主文件
documents 1─1 file_records(attachment_file_id)   ← 附件（2026-11-06，最多 1 个，永不预览）
file_records(owner_type='document') N─1 documents(历史)   ← 经 recycle_bin
documents N─M notice_documents M─N notices
notices 1─1 file_records(file_id)
notices 1─N notice_targets          notices 1─N notice_reads
documents ─ recycle_bin 1─N file_records(被回收文件)
backup_records(独立)  users ─ backup_records(创建人)
```

---

## 17. 初始化与迁移

- 建表与引导：应用启动 lifespan 调 `app/db/init_db.init_db()` → `Base.metadata.create_all`
  → `settings.ensure_dirs()` 创建 `data/*` 六目录 → 若不存在则按 `.env` 的
  `INITIAL_ADMIN_USERNAME` / `INITIAL_ADMIN_PASSWORD` 创建**初始 admin**（密码哈希入库、
  `must_change_password=1`；生产环境禁用占位/弱密码）→ 写入通知类型种子词表。
  手动入口 `scripts/init_db.py`（幂等，等价于直接调 `init_db()`）。
- 结构演进：`scripts/migrate.py`（`--check` 只预览）按序执行
  `scripts/migrations/NNN_*.py`，每个脚本自带 `is_applied` 检测可重复执行
  （001 部分唯一索引、002 优化器统计种子 ANALYZE、003 backups.source、004 清理墓碑备份、
  005/006/008 不可逆删列、007 图片类型、009 资料可见范围、010/011 通知口径、
  **012 资料附件 + pptx**）。

  迁移 012（2026-11-06）重建两张表并加两列：
  - `file_records.file_type` CHECK 扩容加 `pptx`；
  - `recycle_bin.reason` CHECK 扩容加 `attachment_deleted`，并新增
    `attachment_file_record_id` 列；
  - `documents` 新增 `attachment_file_id` 列。
  两张表都是一次性小表重建（模板同 007），**开发库失败可直接 `seed_demo.py --clean` 重建**；
  生产升级前必须备份（同 005/006/008 纪律）。
- **迁移分工规则（2026-10-25 固化，评审 A1/M1；两套机制不合并）**：
  新库一律 `create_all`（模型即最新 schema）；既有库的**结构演进**（加列/新表/改约束）
  只进 `scripts/migrations/`；`app/db/init_db.py` 的内联迁移清单已**冻结**（现存 11 个
  服务存量库启动自动升级），此后只允许「纯数据修复/对账」类启动迁移（docstring 须写明
  背景与幂等口径）。判据：要 ALTER/建表/重建 → scripts/migrations/；只改行数据 → init_db。
- `data/database/app.db` 不提交 Git，`.gitignore` 忽略 `data/`；备份恢复内含数据库快照
  （见 DESIGN §10）。

---

## 18. 与 项目规范 测试要求对应（§30、§31、§32）

实现时每个表的关键约束/规则都应能被测试断言：

- 时效边界（生效前一天/当天/后一天、失效前一天/当天/后一天、无失效日期、未来生效、已失效、通知导致失效）→ `validity_service.compute_status` + `notice_service` 发布联动；
- 文件（中文名、重复、四类格式、大文件、非法/伪造扩展名、路径穿越、文件损坏）→ `file_service/file_safety` + `file_records` 元数据；
- 权限 → 各表关联 + `require_roles` 依赖。
