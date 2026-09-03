# PostgreSQL 数据库表设计说明

本文档说明 `backend/migrations` 当前迁移完成后的 PostgreSQL 表结构，包括表用途、字段、
主键、外键、唯一键、普通索引以及主要数据约束。SQL 迁移文件是最终事实来源；修改表结构时，
应新增迁移文件并同步更新本文档，不要直接修改已经应用过的迁移。

## 1. 数据库定位

本目录内的 SQL 全部用于 PostgreSQL。Lance 数据本身存放在 Lance 数据集或对象存储中；
`lance_*` 表只保存 Lance 数据集的目录、版本、回执和血缘元数据。

### 当前组织隔离基线

`security/019_product_organization_scope.sql` 是跨产品的组织作用域协调迁移。它在任何
DDL 变更前扫描所有含 `project_id` 但不含 `organization_id` 的持久基础表，并要求每个历史
项目都能从组织-项目注册表解析到且仅解析到一个组织；零映射或多映射会让整次迁移回滚，
不会留下半加列状态。通过预检后，迁移统一回填非空组织键、外键/检查约束和组织前导索引，
再为每张项目表安装：

- 一个中性的 permissive `hc_scope_allow` 策略；
- 一个精确匹配组织、项目和可选区域的 restrictive `hc_scope_isolation` 策略。

restrictive 策略会与历史 permissive 策略做 AND，因此旧的仅项目策略不能放宽组织边界。
唯一显式例外是 `access_control.account_notifications`：它保持强制 RLS 和收件人隔离策略，
因为账户收件箱必须在尚未选择项目时可读。迁移末尾再次断言不存在仅项目基础表。

迁移执行器把 `security/001_core.sql`、`012_organization_aware_rls.sql`、
`019_product_organization_scope.sql` 和 `020_platform_super_admin.sql` 作为有序可重复安全迁移；
包括后置的 `annotation/0008_scoped_legacy_cleaning_import.sql` 在内的业务迁移完成后，执行器会
再次运行 019 收敛组织策略，再运行 020 恢复 `platform.admin` RLS 语义。020 根据既有平台
角色 grant 补齐全局标记并仅在实际变更时提升 revision/撤销旧会话，不依赖用户名。历史前缀
测试只执行该前缀内已存在的可重复版本，不能把未来安全规则提前应用到旧 schema。

```text
+--------------------------- PostgreSQL ----------------------------+
| core         安全、幂等、审计、Outbox、迁移记录                    |
| ingest       采集任务、Rollout、分片上传、原始对象                  |
| workflow     工作流作业和失败对账                                  |
| annotation   标注任务、修订、操作、审核                            |
| publishing   数据集发布版本、资产和导出                            |
| public (*)   校验、质检、对齐、Lance 目录元数据                     |
+-------------------------------------------------------------------+
                              |
                              | dataset_uri / artifact_uri / object_key
                              v
+---------------------- Lance / 对象存储 ----------------------------+
| MCAP 原始文件、Arrow 暂存片段、Lance 数据集、发布产物               |
+-------------------------------------------------------------------+

(*) 未在 SQL 中显式指定 schema 的表会创建到当前 search_path 的首个可写 schema；
    按 PostgreSQL 默认配置通常是 public。本文后续统一按 public.<table> 表示。
```

## 2. Key 和约束标记

```text
PK      Primary Key，主键；唯一且非空，用于唯一定位一行
FK      Foreign Key，外键；由 PostgreSQL 强制保证引用目标存在
UK      Unique Key，唯一键；禁止指定字段组合重复
IDX     Index，普通索引；用于查询加速，不保证唯一
NN      NOT NULL，不能为空
CK      CHECK，数据库检查约束
DEFAULT 数据库默认值
RLS     Row-Level Security，行级安全隔离

PK(1), PK(2) 表示复合主键中的字段顺序。
FK -> schema.table(columns) 表示真实数据库外键。
LOGICAL -> table 表示仅有业务关联，数据库没有创建外键约束。
```

本项目大量使用复合主键。租户身份由 `organization_id` 与组织内的 `project_id` 共同组成；
需要区域隔离的表还会加入 `region_code`，使相同业务 ID 或内容哈希可安全存在于不同作用域。

## 3. Schema 与表清单

```text
PostgreSQL
|
+-- core
|   +-- schema_migrations
|   +-- idempotency_records
|   +-- audit_events
|   +-- outbox_events
|   +-- audit_integrity_heads
|   `-- audit_integrity_entries
|
+-- ingest
|   +-- collection_jobs
|   +-- rollouts
|   +-- upload_sessions
|   +-- upload_objects
|   +-- upload_parts
|   `-- rollout_objects
|
+-- workflow
|   +-- jobs
|   `-- reconciliation_items
|
+-- annotation
|   +-- annotation_tasks
|   +-- annotation_revisions
|   +-- annotation_operations
|   +-- annotation_reviews
|   +-- annotation_mutations
|   +-- annotation_current             [VIEW]
|   `-- annotation_drafts              [VIEW]
|
+-- publishing
|   +-- dataset_versions
|   +-- publication_assets
|   +-- export_attempts
|   `-- published_exports
|
`-- public (默认 search_path)
    +-- raw_verification_reports
    +-- quality_profiles
    +-- qc_reports
    +-- quality_rollout_summaries
    +-- alignment_profiles
    +-- aligned_fragment_attempts
    +-- lance_schema_snapshots
    +-- lance_datasets
    +-- lance_dataset_versions
    +-- lance_rollout_lineage
    `-- lance_pending_reconciliation
```

`preview` schema 现在包含 `artifacts`、`jobs` 和 `sessions` 三张持久表。预览媒体仍存放在
对象存储，PostgreSQL 保存精确对象清单、租户身份、状态、血缘、TTL、LRU 和回收保护。
`preview/0002_publication_leases_and_timeline.sql` 为媒体 Worker 增加 owner/尝试 token/心跳/
租约栅栏，并把发布 token、timeline 和 placeholder manifest 作为持久回执保存。
`preview/0003_global_media_capacity.sql` 增加部署级固定 FFmpeg capacity slot，所有媒体 Worker
通过带 owner/attempt token/过期时间的 PostgreSQL lease 共享全局并发上限。
`annotation.frame_selection_manifests` 保存 ingest 生成的不可变抽帧引用；候选清单本体在
对象存储，自动标注 job 固化同一引用，避免 provider 回退到全量视频解码。其 project quota、
保留期、hold、`ACTIVE -> DELETING` 栅栏由 0011 管理；后续 0012 以只向前迁移补充 task
dataset/rollout 血缘绑定和 job 只能引用 ACTIVE selection 的触发器，已应用的 0011 保持不变。

## 4. 主要关系图

图中 `1 ----< N` 表示一对多，`1 ---- 1` 表示一对一。实线关系均有数据库 FK；跨模块的
相同 ID 多数是逻辑关联，详见各表说明。

```text
ingest.collection_jobs
        1
        |
        `----< ingest.rollouts
                    1
                    +---- 1 ingest.upload_sessions
                    |           1
                    |           +---- 1 ingest.upload_objects
                    |           `----< N ingest.upload_parts
                    |
                    `---- 1 ingest.rollout_objects

quality_profiles
        1
        `----< N qc_reports
                    1
                    `----< N quality_rollout_summaries

lance_schema_snapshots
        1
        `----< N lance_datasets
                    1
                    +----< N lance_dataset_versions
                    |           1
                    |           `----< N lance_rollout_lineage
                    `----< N lance_pending_reconciliation

annotation.annotation_tasks
        1
        +----< N annotation.annotation_revisions
        |           1
        |           +----< N annotation.annotation_operations
        |           +----< N annotation.annotation_reviews
        |           `----< N annotation.annotation_mutations
        |
        +---- 1 current_revision      (延迟外键)
        +---- 0..1 submitted_revision (延迟外键)
        `---- 0..1 approved_review    (延迟外键)

publishing.dataset_versions
        1
        +----< N publishing.publication_assets
        +----< N publishing.export_attempts
        `----< N publishing.published_exports
                    N
                    `---- 1 publishing.export_attempts (promoted_attempt_id)
```

## 5. core：平台基础表

### 5.1 `core.schema_migrations`

用途：记录已执行迁移的版本和 SHA-256 校验值。该表由迁移程序创建，不在业务 SQL 文件中。

```text
+ core.schema_migrations
| PK: version
| RLS: 否
|
+-- version          text         NN PK       迁移文件相对路径/版本
+-- checksum_sha256  char(64)     NN CK       迁移 SQL 的 SHA-256
`-- applied_at       timestamptz  NN DEFAULT  执行时间，默认 now()
```

### 5.2 `core.idempotency_records`

用途：保存接口幂等请求指纹与响应，避免同一请求被重复执行。

```text
+ core.idempotency_records
| PK: (organization_id, project_id, region_code, scope_key, idempotency_key)
| FK: (organization_id, project_id)
|     -> registry.organization_projects(organization_id, project_id)
| IDX: idempotency_records_expiry_idx(expires_at)
| RLS: 是，organization_id + project_id + region_code
|
+-- organization_id     text         NN PK(1) FK  组织/租户
+-- project_id          text         NN PK(2) FK  组织内项目
+-- region_code         text         NN PK(3)     区域；默认空字符串
+-- scope_key           text         NN PK(4) CK  幂等作用域
+-- idempotency_key     text         NN PK(5) CK  客户端幂等键
+-- request_fingerprint char(64)     NN           请求内容指纹
+-- response_json       jsonb                     已缓存响应
+-- created_at          timestamptz  NN DEFAULT   创建时间
`-- expires_at          timestamptz  NN           过期时间
```

### 5.3 `core.audit_events`

用途：保存用户或系统对资源执行操作的审计事件。

```text
+ core.audit_events
| PK: audit_id
| FK: (organization_id, project_id)
|     -> registry.organization_projects(organization_id, project_id)
| IDX: audit_events_project_occurred_idx(
|      organization_id, project_id, occurred_at DESC, audit_id)
| RLS: 是，organization_id + project_id + 可选 region_code
|
+-- audit_id       uuid         NN PK  审计事件 ID
+-- organization_id text       NN FK  组织/租户
+-- project_id     text         NN FK CK 组织内项目
+-- region_code    text                可选区域
+-- actor_id       text         NN CK  操作者 ID
+-- action         text         NN CK  操作名称
+-- resource_type  text         NN     资源类型
+-- resource_id    text         NN     资源 ID
+-- request_id     text         NN CK  请求追踪 ID
+-- before_hash    char(64)            变更前内容哈希
+-- after_hash     char(64)            变更后内容哈希
+-- details        jsonb        NN     详情，默认空对象
`-- occurred_at    timestamptz NN     事件发生时间
```

### 5.4 `core.outbox_events`

用途：事务 Outbox，保存等待发布到消息系统的领域事件。

```text
+ core.outbox_events
| PK: event_id
| FK: (organization_id, project_id)
|     -> registry.organization_projects(organization_id, project_id)
| IDX: outbox_events_pending_idx(organization_id, available_at, occurred_at)
|      WHERE published_at IS NULL
| IDX: outbox_events_organization_claimable_idx(
|      organization_id, available_at, occurred_at, event_id)
|      WHERE published_at IS NULL
| RLS: 是，organization_id + project_id + 可选 region_code
|
+-- event_id          uuid         NN PK       事件 ID
+-- organization_id   text         NN FK CK    组织/租户
+-- project_id        text         NN FK CK    组织内项目
+-- region_code       text                     可选区域
+-- event_type        text         NN CK       事件类型
+-- envelope          jsonb        NN          事件信封/载荷
+-- occurred_at       timestamptz  NN          事件发生时间
+-- published_at      timestamptz              成功发布时间
+-- publish_attempts  integer      NN CK       发布次数，默认 0
+-- available_at      timestamptz  NN DEFAULT  下次允许发布时间
+-- last_error        text                     最近一次错误
+-- last_error_code   text                     可重试的稳定错误码
+-- claim_token       uuid                     并发投递租约令牌
+-- claimed_by        text                     租约持有者
`-- claimed_until     timestamptz              租约到期时间
```

### 5.5 `core.audit_integrity_heads` / `core.audit_integrity_entries`

用途：按精确的组织、项目、区域作用域保存审计事件 SHA-256 链头与不可变链条。组织 ID 参与摘要，
因此同项目 ID 的不同组织不会共享序列或摘要。

```text
+ core.audit_integrity_heads
| PK: (organization_id, project_id, region_code)
| FK: (organization_id, project_id)
|     -> registry.organization_projects(organization_id, project_id)
| RLS: 是，organization_id + project_id + region_code
|
+-- organization_id text        NN PK(1) FK  组织/租户
+-- project_id      text         NN PK(2) FK  组织内项目
+-- region_code     text         NN PK(3)     区域；默认空字符串
+-- last_sequence   bigint       NN CK        最后序号
+-- last_event_hash char(64)                  最后事件摘要
`-- updated_at      timestamptz  NN DEFAULT   更新时间

+ core.audit_integrity_entries
| PK: audit_id -> core.audit_events(audit_id)
| UK: (organization_id, project_id, region_code, sequence_no)
| FK: (organization_id, project_id)
|     -> registry.organization_projects(organization_id, project_id)
| RLS: 是，organization_id + project_id + region_code
|
+-- audit_id           uuid         NN PK FK  审计事件 ID
+-- organization_id    text         NN UK FK  组织/租户
+-- project_id         text         NN UK FK  组织内项目
+-- region_code        text         NN UK     区域；默认空字符串
+-- sequence_no        bigint       NN UK CK  作用域内序号
+-- previous_event_hash char(64)                前一事件摘要
+-- event_hash         char(64)     NN CK      当前事件摘要
+-- occurred_at        timestamptz  NN         事件发生时间
`-- created_at         timestamptz  NN DEFAULT 建链时间
```

## 6. ingest：数据采集与上传

### 6.1 `ingest.collection_jobs`

用途：采集任务主表，一个采集任务可包含多个 Rollout。

```text
+ ingest.collection_jobs
| PK: (project_id, collection_job_id)
| RLS: 是，project_id + region_code
|
+-- project_id        text         NN PK(1)  项目/租户
+-- region_code       text         NN        区域
+-- task_id           text         NN        上游任务 ID，无数据库 FK
+-- collection_job_id text         NN PK(2)  采集任务 ID
+-- robot_id          text         NN        机器人 ID
+-- status            text         NN CK     REGISTERED/COLLECTING/PAUSED/
|                                            COMPLETED/FAILED/CANCELLED
+-- created_at        timestamptz  NN        创建时间
`-- updated_at        timestamptz  NN        更新时间
```

### 6.2 `ingest.rollouts`

用途：采集任务中的一次 Rollout/轨迹记录。

```text
+ ingest.rollouts
| PK: (project_id, rollout_id)
| UK: (project_id, collection_job_id, sequence_no)
| UK(partial): (project_id, source_fingerprint)
|              WHERE source_fingerprint IS NOT NULL AND duplicate_of_rollout_id IS NULL
| FK: (project_id, collection_job_id)
|     -> ingest.collection_jobs(project_id, collection_job_id)
| FK: (project_id, duplicate_of_rollout_id)
|     -> ingest.rollouts(project_id, rollout_id)
| RLS: 是，project_id + region_code
|
+-- project_id        text         NN PK(1) FK  项目/租户
+-- region_code       text         NN           区域
+-- collection_job_id text         NN FK         所属采集任务
+-- rollout_id        text         NN PK(2)      Rollout ID
+-- sequence_no       integer      NN UK CK      任务内序号，必须大于 0
+-- robot_id          text         NN            机器人 ID
+-- source_sha256     char(64)     NN CK         原始数据哈希
+-- source_fingerprint char(64)       CK         与转换器版本无关的源录制身份
+-- duplicate_of_rollout_id text      FK CK      历史重复包所指向的 canonical Rollout
+-- status            text         NN CK         REGISTERED/UPLOADING/
|                                                 RAW_COMMITTED/VERIFYING/
|                                                 RAW_VERIFIED/FAILED/CANCELLED
+-- created_at        timestamptz  NN            创建时间
`-- updated_at        timestamptz  NN            更新时间
```

历史数据兼容：源录制身份上线前已经完成质检的旧转换结果继续作为历史质检事实保留；只有未完成
质检的旧同源包会标记 `duplicate_of_rollout_id`。canonical 源指纹仍会拦截之后再次上传同一源。

### 6.3 `ingest.upload_sessions`

用途：一次 Rollout 对应的对象存储分片上传会话。

```text
+ ingest.upload_sessions
| PK: session_id
| UK: object_key
| UK: (project_id, rollout_id)
| FK: (project_id, rollout_id) -> ingest.rollouts(project_id, rollout_id)
| IDX: upload_sessions_active_idx(project_id, region_code, status, updated_at DESC)
| RLS: 是，project_id + region_code
|
+-- session_id             uuid          NN PK     上传会话 ID
+-- project_id             text          NN UK FK  项目/租户
+-- region_code            text          NN        区域
+-- rollout_id             text          NN UK FK  Rollout ID
+-- object_key             text          NN UK     对象存储 Key
+-- multipart_upload_id    text          NN        对象存储分片上传 ID
+-- expected_sha256        char(64)      NN CK     预期 SHA-256
+-- expected_size          bigint        NN CK     预期字节数，必须大于 0
+-- expected_crc64         numeric(20,0) NN CK     预期 CRC64
+-- manifest_fingerprint   char(64)      NN CK     上传清单指纹
+-- status                 text          NN CK     REGISTERED/UPLOADING/PAUSED/
|                                                  MULTIPART_COMPLETED/RAW_COMMITTED/
|                                                  FAILED/CANCELLED
+-- etag                   text                    对象 ETag
+-- failure_code           text                    失败码
+-- created_at             timestamptz   NN        创建时间
+-- updated_at             timestamptz   NN        更新时间
`-- completed_at           timestamptz             完成时间
```

### 6.4 `ingest.upload_objects`

用途：上传会话对应的对象校验结果；`session_id` 唯一，因此与上传会话是一对一关系。

```text
+ ingest.upload_objects
| PK: object_id
| UK: session_id
| UK: object_key
| FK: session_id -> ingest.upload_sessions(session_id)
| RLS: 是，project_id + region_code
|
+-- object_id       uuid          NN PK     上传对象 ID
+-- session_id      uuid          NN UK FK  上传会话 ID
+-- project_id      text          NN        项目/租户
+-- region_code     text          NN        区域
+-- rollout_id      text          NN        Rollout ID，逻辑关联
+-- object_key      text          NN UK     对象存储 Key
+-- expected_size   bigint        NN CK     预期大小
+-- expected_sha256 char(64)      NN        预期 SHA-256
+-- expected_crc64  numeric(20,0) NN CK     预期 CRC64
+-- actual_size     bigint           CK     实际大小
+-- actual_sha256   char(64)                 实际 SHA-256
+-- actual_crc64    numeric(20,0)    CK     实际 CRC64
+-- etag            text                    对象 ETag
+-- status          text          NN CK     PENDING/MULTIPART_COMPLETED/
|                                           VERIFIED/COMMITTED/FAILED/CANCELLED
+-- created_at      timestamptz   NN        创建时间
`-- updated_at      timestamptz   NN        更新时间
```

### 6.5 `ingest.upload_parts`

用途：记录上传会话中的每一个分片。

```text
+ ingest.upload_parts
| PK: (session_id, part_number)
| FK: session_id -> ingest.upload_sessions(session_id)
| IDX: upload_parts_session_status_idx(session_id, status, part_number)
| RLS: 是，project_id + region_code
|
+-- session_id               uuid          NN PK(1) FK  上传会话 ID
+-- project_id               text          NN           项目/租户
+-- region_code              text          NN           区域
+-- part_number              integer       NN PK(2) CK  分片号，1..10000
+-- status                   text          NN CK         AUTHORIZED/UPLOADED
+-- etag                     text                        分片 ETag
+-- size                     bigint           CK         分片大小
+-- crc64                    numeric(20,0)    CK         分片 CRC64
+-- authorization_expires_at timestamptz                 上传授权过期时间
`-- updated_at               timestamptz   NN           更新时间
```

### 6.6 `ingest.rollout_objects`

用途：记录验证并提交成功的 Rollout 原始对象及其清单。

```text
+ ingest.rollout_objects
| PK: (project_id, rollout_id)
| UK: object_key
| UK: manifest_key
| FK: (project_id, rollout_id) -> ingest.rollouts(project_id, rollout_id)
| RLS: 是，project_id + region_code
|
+-- project_id    text          NN PK(1) FK  项目/租户
+-- region_code   text          NN           区域
+-- rollout_id    text          NN PK(2) FK  Rollout ID
+-- object_key    text          NN UK        原始对象 Key
+-- manifest_key  text          NN UK        清单对象 Key
+-- source_sha256 char(64)      NN CK        原始内容 SHA-256
+-- crc64         numeric(20,0) NN CK        CRC64
+-- file_size     bigint        NN CK        文件大小
+-- status        text          NN CK        固定为 COMMITTED
`-- committed_at  timestamptz   NN           提交时间
```

### 6.7 `ingest.device_capture_facts`

用途：保存经过 device-agent 服务身份认证的设备端 `CAPTURED/SAVED` 追加式事实。云端
rollout 登记、Manifest 时间和 Raw 对象提交都不得生成此表记录。

```text
+ ingest.device_capture_facts
| PK: fact_id
| UK: (organization_id, project_id, region_code, device_id, source_event_id)
| FK: (organization_id, project_id, collection_task_id) -> collection_tasks.collection_tasks
| RLS: 是，organization_id + project_id + region_code；UPDATE/DELETE 被触发器拒绝
|
+-- event_type            text          NN CK  CAPTURED/SAVED
+-- collection_job_id     text          NN     设备收到的采集作业
+-- recording_request_id  text          NN     录制请求
+-- data_package_id       text          NN     数据包
+-- device_sequence_no    bigint        NN CK  设备单调事件序号
+-- capture_started_at    timestamptz   NN     设备采集起点
+-- capture_ended_at      timestamptz   NN     设备采集终点
+-- saved_at              timestamptz          SAVED 本地持久化时间
+-- local_artifact_size   bigint               SAVED 本地文件大小
+-- local_artifact_sha256 char(64)             SAVED 本地文件摘要
+-- producer_subject_id   text          NN     已认证设备服务主体
+-- source_fingerprint    char(64)      NN     幂等内容指纹
`-- received_at           timestamptz   NN     云端接收事实时间
```

## 7. workflow：工作流与恢复

### 7.1 `workflow.jobs`

用途：记录 Temporal 工作流对应的平台作业和执行状态。Ingest workflow 通过带 replay patch
保护的幂等 local activity 在各阶段、质量终态、技术终态和取消终态更新此投影；Temporal history
仍是编排事实源，本表服务于查询和恢复。

```text
+ workflow.jobs
| PK: job_id
| UK: workflow_id
| IDX: jobs_project_status_updated_idx(project_id, status, updated_at DESC)
| RLS: 是，project_id
|
+-- job_id                  uuid         NN PK       作业 ID
+-- workflow_id             text         NN UK       Temporal Workflow ID
+-- project_id              text         NN          项目/租户
+-- resource_id             text         NN          业务资源 ID
+-- job_type                text         NN          作业类型
+-- status                  text         NN CK       PENDING/RUNNING/SUCCEEDED/
|                                                   TECHNICAL_FAILED/QUALITY_RISK/
|                                                   QUALITY_REJECTED/CANCELLED
+-- attempt                 integer      NN DEFAULT  尝试次数，默认 0
+-- result                  jsonb                    执行结果
+-- error_code              text                     错误码
+-- created_at              timestamptz  NN          创建时间
+-- updated_at              timestamptz  NN          更新时间
+-- workflow_run_id         text                     Temporal Run ID
+-- workflow_version        text         NN DEFAULT  工作流版本，默认 v1
+-- temporal_namespace      text         NN DEFAULT  Temporal 命名空间
+-- task_queue              text         NN DEFAULT  Temporal Task Queue
+-- stage                   text         NN DEFAULT  当前阶段，默认 pending
+-- error_message           text                     错误详情
`-- cancellation_requested  boolean      NN DEFAULT  是否请求取消，默认 false
```

### 7.2 `workflow.reconciliation_items`

用途：保存 Lance 目录或发布资产写入失败后需要重试的对账项目。

```text
+ workflow.reconciliation_items
| PK: reconciliation_id
| UK: workflow_id
| UK: idempotency_key
| IDX: reconciliation_pending_idx(kind, status, updated_at)
|      WHERE status IN ('PENDING', 'FAILED')
| RLS: 是，project_id
|
+-- reconciliation_id uuid         NN PK       对账 ID
+-- workflow_id        text         NN UK       工作流 ID
+-- project_id         text         NN          项目/租户
+-- resource_id        text         NN          资源 ID
+-- kind               text         NN CK       LANCE_CATALOG/PUBLISHING_ASSET
+-- idempotency_key    text         NN UK       幂等键
+-- status             text         NN CK       PENDING/RUNNING/RESOLVED/FAILED
+-- payload            jsonb        NN          重试所需载荷
+-- attempt            integer      NN DEFAULT  尝试次数，默认 0
+-- last_error_code    text                     最近错误码
+-- created_at         timestamptz  NN          创建时间
+-- updated_at         timestamptz  NN          更新时间
`-- resolved_at        timestamptz              解决时间
```

## 8. verification：原始数据校验

### 8.1 `public.raw_verification_reports`

用途：保存不可变的原始数据验证报告。最终主键包含租户和区域，相同内容哈希可出现在不同租户。

```text
+ public.raw_verification_reports
| PK: (project_id, region_code, report_sha256)
| UK: (project_id, region_code, rollout_id, source_sha256)
| IDX: raw_verification_reports_rollout_created_idx
|      (project_id, rollout_id, created_at DESC)
| IDX: raw_verification_reports_scope_idx
|      (project_id, region_code, rollout_id, created_at DESC)
| RLS: 是，project_id + region_code
| IMMUTABLE: UPDATE/DELETE 由触发器拒绝
|
+-- project_id    text         NN PK(1) UK  项目/租户
+-- region_code   text         NN PK(2) UK  区域
+-- report_sha256 char(64)     NN PK(3) CK  规范化报告内容哈希
+-- rollout_id    text         NN UK        Rollout ID，逻辑关联 ingest.rollouts
+-- source_sha256 char(64)     NN UK CK     原始数据哈希
+-- object_key    text         NN           原始对象 Key
+-- status        text         NN CK        RAW_VERIFIED/REJECTED
+-- report_json   jsonb        NN CK        完整报告，内部关键字段与列值一致
`-- created_at    timestamptz  NN DEFAULT   创建时间
```

## 9. quality：质量检测

### 9.1 `public.quality_profiles`

用途：保存有版本的质量检测配置。

```text
+ public.quality_profiles
| PK: (project_id, profile_id, profile_version)
| UK: (project_id, profile_id, profile_version, profile_sha256)
| UK: (project_id, profile_sha256)
| RLS: 是，project_id
|
+-- project_id      text         NN PK(1) UK  项目/租户
+-- profile_id      text         NN PK(2) UK  配置 ID
+-- profile_version integer      NN PK(3) CK  配置版本，大于 0
+-- schema_version  text         NN CK        固定 quality-profile/v1
+-- profile_sha256  char(64)     NN UK CK     配置内容哈希
+-- profile_json    jsonb        NN           完整配置
`-- created_at      timestamptz  NN DEFAULT   创建时间
```

### 9.2 `public.qc_reports`

用途：保存按设计不可变的完整质检报告。最终主键按租户和区域限定内容哈希。当前迁移没有为
该表设置拒绝 `UPDATE/DELETE` 的触发器，不可变性仍需由数据库权限或服务层保证。

```text
+ public.qc_reports
| PK: (project_id, region_code, report_sha256)
| UK: (project_id, region_code, rollout_id, source_sha256,
|      profile_id, profile_version, engine_version)
| FK: (project_id, profile_id, profile_version, profile_sha256)
|     -> quality_profiles(project_id, profile_id, profile_version, profile_sha256)
| IDX: qc_reports_rollout_created_idx
|      (project_id, region_code, rollout_id, created_at DESC)
| RLS: 是，project_id + region_code
|
+-- project_id      text         NN PK(1) UK FK  项目/租户
+-- region_code     text         NN PK(2) UK     区域
+-- report_sha256   char(64)     NN PK(3) CK     报告内容哈希
+-- rollout_id      text         NN UK           Rollout ID，逻辑关联
+-- source_sha256   char(64)     NN UK CK        原始数据哈希
+-- profile_id      text         NN UK FK        质量配置 ID
+-- profile_version integer      NN UK FK CK     质量配置版本
+-- profile_sha256  char(64)     NN FK CK        质量配置哈希
+-- engine_version  text         NN UK           质检引擎版本
+-- schema_version  text         NN CK           固定 qc-report/v1
+-- status          text         NN CK           PASS/RISK/REJECT
+-- report_json     jsonb        NN              完整质检报告
`-- created_at      timestamptz  NN DEFAULT      创建时间
```

### 9.3 `public.quality_rollout_summaries`

用途：保存每个 Rollout 当前生效的质检摘要；与完整报告不同，该表允许更新。

```text
+ public.quality_rollout_summaries
| PK: (project_id, region_code, rollout_id)
| FK: (project_id, region_code, report_sha256)
|     -> qc_reports(project_id, region_code, report_sha256)
| RLS: 是，project_id + region_code
|
+-- project_id      text         NN PK(1) FK  项目/租户
+-- region_code     text         NN PK(2) FK  区域
+-- rollout_id      text         NN PK(3)     Rollout ID
+-- source_sha256   char(64)     NN CK        原始数据哈希
+-- profile_id      text         NN           质量配置 ID
+-- profile_version integer      NN CK        质量配置版本
+-- engine_version  text         NN           质检引擎版本
+-- status          text         NN CK        PASS/RISK/REJECT
+-- report_sha256   char(64)     NN FK        当前完整报告哈希
`-- updated_at      timestamptz  NN DEFAULT   更新时间
```

## 10. alignment：数据对齐

### 10.1 `public.alignment_profiles`

用途：保存项目级数据对齐配置。

```text
+ public.alignment_profiles
| PK: (project_id, profile_id)
| RLS: 是，project_id
|
+-- project_id   text         NN PK(1)     项目/租户
+-- profile_id   text         NN PK(2) CK  对齐配置 ID
+-- profile_json jsonb        NN CK        完整配置，schema_version 必须为
|                                          alignment-profile/v1
`-- created_at   timestamptz  NN DEFAULT   创建时间
```

### 10.2 `public.aligned_fragment_attempts`

用途：记录将 Rollout 转换为 Arrow 暂存片段的每次尝试。

```text
+ public.aligned_fragment_attempts
| PK: (project_id, rollout_id, source_sha256, converter_version, attempt_id)
| IDX: aligned_fragment_ready_scope_idx
|      (project_id, region_code, rollout_id, updated_at DESC)
|      WHERE status = 'READY'
| RLS: 是，project_id + region_code
| IMMUTABLE: 状态变为 READY 后，UPDATE/DELETE 由触发器拒绝
|
+-- project_id        text         NN PK(1)  项目/租户
+-- region_code       text         NN        区域
+-- rollout_id        text         NN PK(2)  Rollout ID，逻辑关联
+-- source_sha256     char(64)     NN PK(3) CK  原始数据哈希
+-- converter_version text         NN PK(4)  转换器版本
+-- attempt_id        text         NN PK(5)  尝试 ID
+-- content_sha256    char(64)        CK     片段内容哈希
+-- schema_sha256     char(64)        CK     Arrow Schema 哈希
+-- row_count         bigint           CK     行数
+-- staging_uri       text                    暂存片段 URI
+-- staging_format    text             CK     READY 时固定 arrow-ipc/v1
+-- status            text         NN CK     WRITING/READY/ABORTED
+-- manifest_json     jsonb                    运行时清单
+-- created_at        timestamptz  NN DEFAULT  创建时间
`-- updated_at        timestamptz  NN DEFAULT  更新时间
```

## 11. Lance Catalog：Lance 目录元数据

这些表位于 PostgreSQL，保存 Lance 数据集的管理信息，不保存 Lance 的实际数据行。

### 11.1 `public.lance_schema_snapshots`

用途：保存数据集的不可变 Schema 快照和指纹。

```text
+ public.lance_schema_snapshots
| PK: (project_id, dataset_id, schema_snapshot_id)
| UK: (project_id, dataset_id, fingerprint)
| RLS: 是，project_id
|
+-- project_id         text              NN PK(1) UK  项目/租户
+-- dataset_id         text              NN PK(2) UK  数据集 ID
+-- schema_snapshot_id text              NN PK(3)     Schema 快照 ID
+-- frequency_hz       double precision  NN CK        数据频率，必须大于 0
+-- fields_json        jsonb             NN           字段定义
+-- fingerprint        text              NN UK CK     Schema 指纹，64 位十六进制
+-- snapshot_json      jsonb             NN           完整 Schema 快照
`-- created_at         timestamptz       NN DEFAULT  创建时间
```

### 11.2 `public.lance_datasets`

用途：保存每个逻辑数据集当前对应的 Lance 地址、Schema 和当前版本。

```text
+ public.lance_datasets
| PK: (project_id, dataset_id)
| UK: dataset_uri
| UK: (project_id, dataset_id, fingerprint)
| FK: (project_id, dataset_id, schema_snapshot_id)
|     -> lance_schema_snapshots(project_id, dataset_id, schema_snapshot_id)
| RLS: 是，project_id
|
+-- project_id         text              NN PK(1) FK UK  项目/租户
+-- dataset_id         text              NN PK(2) FK UK  数据集 ID
+-- schema_snapshot_id text              NN FK        当前 Schema 快照 ID
+-- frequency_hz       double precision  NN CK        数据频率
+-- fingerprint        text              NN UK CK     Schema 指纹
+-- dataset_uri        text              NN UK        Lance 数据集 URI
+-- current_version    bigint            NN CK        逻辑当前版本，默认 0
+-- created_at         timestamptz       NN DEFAULT   创建时间
`-- updated_at         timestamptz       NN DEFAULT   更新时间
```

### 11.3 `public.lance_dataset_versions`

用途：保存数据集每次成功提交后的逻辑版本及 Lance 物理版本回执。版本在业务设计上不可变，
但当前迁移没有设置拒绝 `UPDATE/DELETE` 的触发器。

```text
+ public.lance_dataset_versions
| PK: (project_id, dataset_id, version)
| UK: storage_commit_id
| UK: (project_id, dataset_id, rollout_id, source_sha256, converter_version)
| FK: (project_id, dataset_id) -> lance_datasets(project_id, dataset_id)
| IDX: lance_versions_lance_snapshot_idx(dataset_uri, lance_version)
| RLS: 是，project_id
|
+-- project_id         text         NN PK(1) FK UK  项目/租户
+-- dataset_id         text         NN PK(2) FK UK  数据集 ID
+-- version            bigint       NN PK(3) CK     平台逻辑版本，大于 0
+-- schema_snapshot_id text         NN              Schema 快照 ID
+-- frequency_hz       double precision NN CK       数据频率
+-- fingerprint        text         NN CK           Schema 指纹
+-- content_hash       text         NN CK           版本内容哈希
+-- dataset_uri        text         NN IDX          Lance 数据集 URI
+-- lance_version      bigint       NN CK IDX       Lance 物理版本，大于 0
+-- storage_commit_id  text         NN UK CK        存储提交 ID
+-- rollout_id         text         NN UK           本次提交的 Rollout ID
+-- source_sha256      text         NN UK CK        原始数据哈希
+-- converter_version  text         NN UK           转换器版本
+-- committed_rollouts jsonb        NN              已提交 Rollout 清单
+-- receipt_json       jsonb        NN              完整提交回执
`-- created_at         timestamptz  NN              创建时间
```

### 11.4 `public.lance_rollout_lineage`

用途：记录 Rollout 被加入哪个 Lance 版本，以及来源片段和转换器血缘。

```text
+ public.lance_rollout_lineage
| PK: (project_id, dataset_id, rollout_id)
| UK: storage_commit_id
| FK: storage_commit_id -> lance_dataset_versions(storage_commit_id)
| FK: (project_id, dataset_id, version_added)
|     -> lance_dataset_versions(project_id, dataset_id, version)
| RLS: 是，project_id
|
+-- project_id           text         NN PK(1) FK  项目/租户
+-- dataset_id           text         NN PK(2) FK  数据集 ID
+-- rollout_id           text         NN PK(3)     Rollout ID，逻辑关联 ingest
+-- version_added        bigint       NN FK CK     首次加入的逻辑版本
+-- source_sha256        text         NN CK        原始数据哈希
+-- converter_version    text         NN           转换器版本
+-- schema_snapshot_id   text         NN           Schema 快照 ID
+-- fingerprint          text         NN CK        Schema 指纹
+-- fragment_uri         text         NN           Arrow 暂存片段 URI
+-- fragment_content_hash text        NN CK        暂存片段内容哈希
+-- step_count           bigint       NN CK        步数，不能小于 0
+-- storage_commit_id    text         NN UK FK     存储提交 ID
`-- created_at           timestamptz  NN DEFAULT   创建时间
```

### 11.5 `public.lance_pending_reconciliation`

用途：Lance 已成功提交但 PostgreSQL 目录登记失败时，保存待恢复记录。

```text
+ public.lance_pending_reconciliation
| PK: storage_commit_id
| FK: (project_id, dataset_id) -> lance_datasets(project_id, dataset_id)
| IDX: lance_pending_dataset_idx(project_id, dataset_id, first_seen_at)
| RLS: 是，project_id
|
+-- storage_commit_id text         NN PK CK   存储提交 ID
+-- project_id        text         NN FK IDX  项目/租户
+-- dataset_id        text         NN FK IDX  数据集 ID
+-- dataset_uri       text         NN         Lance 数据集 URI
+-- lance_version     bigint       NN CK      Lance 物理版本
+-- last_error        text         NN         最近错误
+-- attempt_count     integer      NN CK      尝试次数，默认 1
+-- first_seen_at     timestamptz  NN DEFAULT 首次发现时间
`-- last_attempt_at   timestamptz  NN DEFAULT 最近尝试时间
```

## 12. annotation：数据标注

### 12.1 `annotation.annotation_tasks`

用途：标注任务聚合根，保存当前修订、提交修订、审核结果和并发控制信息。

```text
+ annotation.annotation_tasks
| PK: task_id
| UK: (project_id, rollout_id)
| FK: (task_id, current_revision)
|     -> annotation_revisions(task_id, revision) [DEFERRABLE]
| FK: (task_id, submitted_revision)
|     -> annotation_revisions(task_id, revision) [DEFERRABLE]
| FK: (task_id, approved_revision, approved_review_id)
|     -> annotation_reviews(task_id, revision, review_id) [DEFERRABLE]
| IDX: annotation_tasks_project_status_idx(project_id, status, updated_at DESC)
| RLS: 是，project_id
|
+-- task_id            text         NN PK       标注任务 ID
+-- project_id         text         NN UK       项目/租户
+-- dataset_id         text         NN          数据集 ID，逻辑关联
+-- dataset_version    bigint       NN CK       数据集版本，大于 0
+-- rollout_id         text         NN UK       Rollout ID，逻辑关联
+-- assignee_id        text                     当前标注人
+-- current_revision   bigint       NN FK CK    当前修订，默认 0
+-- state_version      bigint       NN CK       状态并发版本，默认 0
+-- status             text         NN CK       DRAFT/SUBMITTED/APPROVED/
|                                              NEEDS_REVISION/REJECTED
+-- submitted_revision bigint          FK CK    已提交修订
+-- submitted_by       text                     提交人
+-- approved_revision  bigint          FK CK    已批准修订
+-- approved_review_id text            FK       批准审核 ID
+-- etag               text         NN          乐观并发 ETag
+-- created_at         timestamptz  NN DEFAULT  创建时间
`-- updated_at         timestamptz  NN DEFAULT  更新时间
```

### 12.2 `annotation.annotation_revisions`

用途：标注任务的不可变修订版本；修订号必须从父修订连续递增。

```text
+ annotation.annotation_revisions
| PK: (task_id, revision)
| FK: task_id -> annotation_tasks(task_id)
| RLS: 是，通过 annotation_tasks 继承项目作用域
| APPEND ONLY: UPDATE/DELETE 由触发器拒绝
|
+-- task_id            text         NN PK(1) FK  标注任务 ID
+-- revision           bigint       NN PK(2) CK  修订号，从 0 开始
+-- parent_revision    bigint           CK       父修订；revision=0 时为空
+-- author_id          text         NN           作者 ID
+-- client_mutation_id text         NN           产生修订的客户端变更 ID
`-- created_at         timestamptz  NN DEFAULT   创建时间
```

### 12.3 `annotation.annotation_operations`

用途：记录某个修订中对步骤区间执行的排除或恢复操作。

```text
+ annotation.annotation_operations
| PK: (task_id, revision, operation_id)
| UK: (task_id, operation_id)
| UK: (task_id, revision, operation_sequence)
| FK: (task_id, revision) -> annotation_revisions(task_id, revision)
| RLS: 是，通过 annotation_tasks 继承项目作用域
| APPEND ONLY: UPDATE/DELETE 由触发器拒绝
|
+-- task_id            text     NN PK(1) UK FK  标注任务 ID
+-- revision           bigint   NN PK(2) UK FK  修订号
+-- operation_id       text     NN PK(3) UK     操作 ID
+-- operation_sequence integer  NN UK CK        修订内操作序号
+-- kind               text     NN CK           EXCLUDE/RESTORE
+-- start_step         bigint   NN CK           起始步骤，包含
+-- end_step           bigint   NN CK           结束步骤，必须大于 start_step
+-- reason             text     NN DEFAULT      原因，默认空字符串
`-- modality_scope     text     NN CK           固定 ALL_MODALITIES
```

### 12.4 `annotation.annotation_reviews`

用途：保存审核员对某一修订的不可变审核决定。

```text
+ annotation.annotation_reviews
| PK: review_id
| UK: (task_id, revision, review_id)
| FK: (task_id, revision) -> annotation_revisions(task_id, revision)
| IDX: annotation_reviews_task_revision_idx(task_id, revision, created_at)
| RLS: 是，通过 annotation_tasks 继承项目作用域
| APPEND ONLY: UPDATE/DELETE 由触发器拒绝
|
+-- review_id  text         NN PK UK  审核 ID
+-- task_id    text         NN UK FK  标注任务 ID
+-- revision   bigint       NN UK FK CK  被审核修订
+-- reviewer_id text        NN        审核人 ID
+-- decision   text         NN CK     APPROVE/NEEDS_REVISION/REJECT
+-- comment    text         NN DEFAULT 审核意见
`-- created_at timestamptz  NN DEFAULT 创建时间
```

### 12.5 `annotation.annotation_mutations`

用途：保存客户端变更的幂等结果，保证同一 `client_mutation_id` 不被重复应用。

```text
+ annotation.annotation_mutations
| PK: (task_id, client_mutation_id)
| FK: (task_id, result_revision) -> annotation_revisions(task_id, revision)
| RLS: 是，通过 annotation_tasks 继承项目作用域
| APPEND ONLY: UPDATE/DELETE 由触发器拒绝
|
+-- task_id             text         NN PK(1) FK  标注任务 ID
+-- client_mutation_id  text         NN PK(2)     客户端变更 ID
+-- actor_id            text         NN           操作者 ID
+-- request_fingerprint char(64)     NN           请求指纹
+-- expected_revision   bigint       NN CK        请求期望的旧修订
+-- request_etag        text         NN           请求携带的 ETag
+-- result_revision     bigint       NN FK CK     变更产生的新修订
+-- result_etag         text         NN           变更后的 ETag
`-- created_at          timestamptz  NN DEFAULT   创建时间
```

### 12.6 `annotation.legacy_cleaning_migrations`

用途：把组织/项目/区域内的旧 CleaningDraft EDL revision 唯一映射到不可变 annotation
revision。`annotation/0008_scoped_legacy_cleaning_import.sql` 只接受 selected stream 能唯一解析到
固定 Lance rollout/version/step-count 的历史行；任何未解析或多义行都会在写入前中止整个迁移。
P11 纳秒 EDL 原文保存在 `source_payload`，不会被猜测转换成 step 操作。

```text
+ annotation.legacy_cleaning_migrations
| PK: (organization_id, project_id, region_code, source_draft_id, source_revision)
| UK: (target_task_id, target_revision)
| FK: (target_task_id, target_revision) -> annotation_revisions(task_id, revision)
| RLS: 精确 organization/project/region
| APPEND ONLY: UPDATE/DELETE 由触发器拒绝
|
+-- organization_id      text         NN PK(1)  组织 ID
+-- project_id           text         NN PK(2)  项目 ID
+-- region_code          text         NN PK(3)  区域
+-- source_draft_id      text         NN PK(4)  旧 Draft ID
+-- source_revision      bigint       NN PK(5)  旧 EDL revision
+-- source_audit_event_id text                    可选的原审计事件 ID
+-- source_actor_id      text         NN        原操作者
+-- source_created_at    timestamptz  NN        原创建时间
+-- target_task_id       text         NN UK FK  P08 task
+-- target_revision      bigint       NN UK FK  P08 revision
+-- source_payload       jsonb        NN        完整旧 EDL snapshot
`-- migrated_at          timestamptz  NN        导入时间
```

### 12.7 标注视图

```text
annotation.annotation_current [VIEW]
  来源: annotation_tasks
  内容: 当前修订、状态版本、状态、提交/批准指针、ETag、更新时间

annotation.annotation_drafts [VIEW]
  来源: annotation_tasks
  过滤: status IN ('DRAFT', 'NEEDS_REVISION', 'REJECTED')
  内容: task_id、当前 revision、etag、assignee_id、updated_at
```

## 13. publishing：数据发布与导出

### 13.1 `publishing.dataset_versions`

用途：保存准备发布的数据集不可变版本清单。

```text
+ publishing.dataset_versions
| PK: (project_id, dataset_id, dataset_version)
| RLS: 是，project_id
| IMMUTABLE: UPDATE/DELETE 由触发器拒绝
|
+-- project_id         text         NN PK(1)  项目/租户
+-- dataset_id         text         NN PK(2)  数据集 ID
+-- dataset_version    text         NN PK(3)  发布数据集版本
+-- base_lance_version text         NN        基础 Lance 版本，逻辑关联
+-- content_hash       text         NN CK     版本内容哈希
+-- manifest_json      jsonb        NN        不可变发布清单
`-- created_at         timestamptz  NN        创建时间
```

### 13.2 `publishing.publication_assets`

用途：保存一个发布版本所需的标注覆盖层或训练清单资产。

```text
+ publishing.publication_assets
| PK: (project_id, dataset_id, dataset_version, asset_kind)
| FK: (project_id, dataset_id, dataset_version)
|     -> publishing.dataset_versions(project_id, dataset_id, dataset_version)
| RLS: 是，project_id
| IMMUTABLE: UPDATE/DELETE 由触发器拒绝
|
+-- project_id      text    NN PK(1) FK  项目/租户
+-- dataset_id      text    NN PK(2) FK  数据集 ID
+-- dataset_version text    NN PK(3) FK  数据集版本
+-- asset_kind      text    NN PK(4) CK  ANNOTATIONS_LANCE/TRAINING_MANIFEST
+-- artifact_uri    text    NN           资产 URI
+-- content_sha256  text    NN CK        资产内容 SHA-256
+-- media_type      text    NN           MIME 类型
`-- size_bytes      bigint  NN CK        资产大小，不能小于 0
```

### 13.3 `publishing.export_attempts`

用途：记录每次导出尝试；这是发布模块中唯一允许更新生命周期状态的表。

```text
+ publishing.export_attempts
| PK: (project_id, dataset_id, dataset_version, export_format, attempt_id)
| FK: (project_id, dataset_id, dataset_version)
|     -> publishing.dataset_versions(project_id, dataset_id, dataset_version)
| IDX: export_attempts_status_idx(project_id, status, updated_at)
| RLS: 是，project_id
|
+-- project_id            text         NN PK(1) FK  项目/租户
+-- dataset_id            text         NN PK(2) FK  数据集 ID
+-- dataset_version       text         NN PK(3) FK  数据集版本
+-- export_format         text         NN PK(4) CK  lance_snapshot/lerobot_v3
+-- attempt_id            text         NN PK(5)     尝试 ID
+-- status                text         NN CK        STAGING/VALIDATING/FAILED/PUBLISHED
+-- staging_uri           text         NN           暂存位置
+-- staged_content_sha256 text            CK        暂存产物内容哈希
+-- failure_code          text                       失败码
+-- created_at            timestamptz  NN DEFAULT   创建时间
`-- updated_at            timestamptz  NN DEFAULT   更新时间
```

### 13.4 `publishing.published_exports`

用途：保存已经验证并提升为正式产物的导出记录。

```text
+ publishing.published_exports
| PK: (project_id, dataset_id, dataset_version, export_format)
| FK: (project_id, dataset_id, dataset_version)
|     -> publishing.dataset_versions(project_id, dataset_id, dataset_version)
| FK: (project_id, dataset_id, dataset_version, export_format, promoted_attempt_id)
|     -> publishing.export_attempts(..., attempt_id)
| RLS: 是，project_id
| IMMUTABLE: UPDATE/DELETE 由触发器拒绝
| INSERT GUARD: 对应 attempt 必须为 PUBLISHED，且 SHA-256 必须一致
|
+-- project_id             text         NN PK(1) FK  项目/租户
+-- dataset_id             text         NN PK(2) FK  数据集 ID
+-- dataset_version        text         NN PK(3) FK  数据集版本
+-- export_format          text         NN PK(4) FK CK  lance_snapshot/lerobot_v3
+-- manifest_content_hash  text         NN CK        发布清单内容哈希
+-- artifact_uri           text         NN           正式产物 URI
+-- artifact_content_sha256 text        NN CK        正式产物 SHA-256
+-- media_type             text         NN           MIME 类型
+-- row_count              bigint       NN CK        数据行数
+-- promoted_attempt_id    text         NN FK        被提升的导出尝试 ID
`-- published_at           timestamptz  NN DEFAULT   发布时间
```

### 13.5 Episode 与 Dataset 发布版本

`annotation.annotation_submissions.episode_version` 是任务内从 1 开始的稳定业务版本号，只在
提交审核时递增；保存草稿不会创建新的 Episode 业务版本。唯一索引
`(task_id, episode_version)` 防止同一任务重复分配版本。

`publishing.episode_version_finalizations` 在 Dataset 发布时冻结精确的 Episode 提交版本：

```text
+ publishing.episode_version_finalizations
| PK: (project_id, dataset_id, dataset_version, rollout_id)
| UK: (project_id, dataset_id, dataset_version,
|      annotation_task_id, annotation_submission_id)
| FK: (project_id, dataset_id, dataset_version)
|     -> publishing.dataset_versions
| FK: (annotation_task_id, annotation_submission_id)
|     -> annotation.annotation_submissions
| RLS: 是，project_id
| IMMUTABLE: UPDATE/DELETE 由触发器拒绝
```

`dataset_registry.dataset_versions.version_scope` 区分用户显式发布的 `DATASET_RELEASE` 与内部
工作快照 `INTERNAL`。历史 `version_lance_*` 行回填为 `INTERNAL`，不得再作为业务发布版本展示。

## 14. 跨模块逻辑关联

以下关联在业务上存在，但当前 SQL 没有创建跨模块外键。删除或修改上游数据时，PostgreSQL
不会自动阻止产生孤立记录，需要由服务层和工作流保证一致性。

```text
ingest.rollouts.rollout_id
    +-- LOGICAL -> raw_verification_reports.rollout_id
    +-- LOGICAL -> qc_reports.rollout_id
    +-- LOGICAL -> quality_rollout_summaries.rollout_id
    +-- LOGICAL -> aligned_fragment_attempts.rollout_id
    +-- LOGICAL -> lance_rollout_lineage.rollout_id
    `-- LOGICAL -> annotation.annotation_tasks.rollout_id

aligned_fragment_attempts.staging_uri
    `-- LOGICAL -> lance_rollout_lineage.fragment_uri

lance_datasets / lance_dataset_versions
    +-- LOGICAL -> annotation.annotation_tasks(dataset_id, dataset_version)
    `-- LOGICAL -> publishing.dataset_versions(dataset_id, base_lance_version)

collection_tasks.collection_tasks
    +-- UK: (organization_id, project_id, dataset_id)
    +-- 1:1 -> 一个项目内的采集任务唯一拥有一个逻辑 Dataset
    `-- dataset_id 不包含 region_code；region_code 仅保存在上传、Episode 和对象存储血缘

dataset_registry.datasets
    `-- FK: (organization_id, project_id, collection_task_id, dataset_id)
        -> collection_tasks.collection_tasks

workflow.jobs.resource_id
    `-- LOGICAL -> 任意流水线业务资源，由 job_type 决定具体表
```

## 15. 安全性、不可变性和生命周期规则

```text
租户隔离
  project_id              项目级租户边界
  region_code             项目内物理存储/数据驻留边界，不参与任务 Dataset 身份
  core.scope_matches()    读取事务中的 app.project_id/app.region_code
  app.platform_admin      已验证 platform.admin；只在 API 证明真实项目后绕过行 scope
  core.apply_project_rls  为带 project_id 的表启用并强制 RLS
  annotation 自有策略    同时使用 app.project_ids/app.is_admin

不可变表/记录
  raw_verification_reports                  整表禁止 UPDATE/DELETE
  aligned_fragment_attempts(status=READY)   READY 后禁止 UPDATE/DELETE
  annotation_revisions                      仅追加
  annotation_operations                     仅追加
  annotation_reviews                        仅追加
  annotation_mutations                      仅追加
  publishing.dataset_versions               仅追加
  publishing.publication_assets             仅追加
  publishing.published_exports              仅追加

允许更新的生命周期表
  ingest.*
  workflow.jobs
  workflow.reconciliation_items
  quality_rollout_summaries
  lance_datasets
  lance_pending_reconciliation
  annotation.annotation_tasks
  publishing.export_attempts
  platform.platform_instances
  platform.platform_task_leases
```

`platform.platform_instances` 是无租户 scope 的运维控制面事实。每个进程以 Pod UID（本地为
启动时 UUID）做主键，每 30 秒 upsert；目录查询以 PostgreSQL 时钟判定 90 秒未上报的实例为
`stale`，并仅清理超过保留期的历史记录。该目录只用于可见性，不承担 lease 或一致性判定。

`platform.environment_fences`、`platform.maintenance_operations`、`platform.writer_permits` 和
append-only `platform.maintenance_events` 实现全局维护控制面。租约到期、接管、writer inventory
和提交许可只使用 PostgreSQL 时钟；fencing token 来自永不回退的数据库序列。同一环境由 partial
unique index 保证最多一个 nonterminal operation，`platform.assert_writer_permit()` 是各写路径在
自身提交事务内复核 mode、epoch 和 expiry 的统一数据库原语。

`platform.platform_task_leases` 以 `(environment_id, task_id)` 保存平台循环的唯一当前 owner，
使用不可复用实例 UUID、数据库时钟 30 秒租约和全局单调 fencing token。接管会替换 lease UUID，
因此断连旧 owner 恢复后即使仍持有本地状态，也会在同一写事务提交前被
`platform.assert_task_lease()` 以稳定错误拒绝。`platform.platform_task_lease_events` 只追加
acquire、reacquire、takeover 和 release 事实，trigger 禁止 UPDATE/DELETE；只读维护模式拒绝
新 claim、renew 和带旧 lease 的提交。

`platform.backup_catalog_entries` 保存由独立仓库签名清单重建的不可变、脱敏索引；
`platform.backup_catalog_status_facts` 只追加生命周期和验证事实。`platform/004` 允许同一备份从
`INTEGRITY_VERIFIED` 追加新的 `INTEGRITY_VERIFIED` 独立复验事实，并增加按
`source_environment_id/status/backup_completed_at/backup_id` 的稳定 keyset 查询索引。相同
`operation_id` 只可幂等重放完全一致的事实，不能回退状态或伪造 `RESTORE_VERIFIED`。

`platform/005_runtime_config_revisions.sql` 增加环境级 monotonic head、完整有效值 snapshot 和
append-only APPLY/ROLLBACK event，并在节点目录心跳中报告 `applied_config_revision`。revision/event
由 trigger 禁止 UPDATE/DELETE；rollback 写入新的递增 revision，不回退 head。该表只承载应用层
精确 allowlist 的低风险值，不是 Secret、DSN、credential、TLS、镜像或 schema identity 通道。

`platform/006_platform_audit_integrity.sql` 把全局 `PLATFORM` access audit 接入独立的 P19
SHA-256 predecessor chain。既有事件按 `occurred_at/event_id` 稳定回填，后续 insert 在同一事务由
trigger 串链；source event 和 integrity entry 均禁止 UPDATE/DELETE。平台投影只返回 HMAC 引用、
事件分类、结果、request ID 和保留的安全字段名，不返回 actor/resource 原值或 `safe_details` 值；
viewer 只能分页读取，verifier 才能校验并导出受控 NDJSON。

`platform/007_release_control.sql` 保存 environment/release 级发布状态和 source/target 四组件精确
镜像引用；每次 preflight、四眼批准和外部 controller transition 都在同一数据库事务追加 event。
state version 作为 CAS 前置，requester 与 approver 必须不同；event 由 trigger 禁止 UPDATE/DELETE。
浏览器只写批准事实，不保存或接收 Kubernetes、Temporal、registry 或 GitOps credential。

`security/022_platform_operation_capabilities.sql` 增加只按全局精确 grant 判定的 viewer、
maintenance operator、release operator、verifier 和 break-glass capability。它们不被
`platform.admin` 通配，且同一 principal 最多只能持有一个 active operation capability；grant
变更会提升 capability revision 并撤销旧 session，避免旧身份继续执行高危操作。

`phases.json` 是显式、严格版本化的迁移分相合同：未列入 `contract_migrations` 的 manifest 条目
全部属于 expand。`hc-data-migrate upgrade-expand` 在同一 PostgreSQL session advisory lock 下只
执行 expand 并做 repeatable security reconciliation；`upgrade-contract` 只执行显式 contract 条目，
在数据库连接前要求 `sha256:<64 lowercase hex>` 批准 digest，且不会顺带 replay expand DDL。
Helm expand Job 可作为 pre-install/pre-upgrade hook，contract Job 默认关闭且绝不是 upgrade hook。

普通迁移各自在事务中执行并把校验值写入 `core.schema_migrations`；需要 concurrent index/backfill
的专用 executor 使用相同 session lock 和分阶段事务。迁移只向前：已应用文件不得重排或修改，
contract 后也没有盲目 down migration。
