# P01 Dashboard 事实源与决策记录（BR01）

状态：PD-BR-01～07 已冻结并在现有 BE21 Router/Service/Repository/model/cursor/audit/index
基础上落实。Browser Mock、效果图数字和测试 seed 不是事实源。一次请求只允许一个 principal、一个
project 和一个 region；数据库查询统一使用 UTC 的 `[from,to)`。

## 已确认的正式边界

- 页面默认请求最近 24 小时，可切换 7 天和 30 天。wire 仍必须显式发送带 offset 的 `from`、
  `to` 和项目 IANA `timezone`；服务端不读取浏览器本地时区。
- activity 是最近业务事件流，不是上传吞吐桶、字节趋势或 Mock 时间序列。
- coverage 在版本化 collection plan、robot group、task catalog 和 denominator 建立前始终为
  `BLOCKED`。`collection_observations` 只保留为 scope/query-shape 技术证据。
- pending-items V1 只有上传失败、QC 异常、待 Tag 审核、待发布四类，并与 principal capability
  取交集。
- 区域容量只属于 P12；P01 不返回 storage 数据。
- P01 主状态区域使用无时间窗口的 `task-status` 只读投影。任务列表按 exact project+region
  的 `collection_jobs` 关联；不传 `task_id` 时主轨道汇总全部任务，`task_id` 只用于可选下钻。
  只有一个任务时同时返回该任务详情；多任务汇总不会伪装成零值。

## Task status 当前状态投影

投影 cohort 固定为 `collection_tasks.collection_tasks.collection_task_id` →
`ingest.collection_jobs.task_id` → `ingest.rollouts.collection_job_id`。每个
`data_package_id` 只得到一个 current main state，同时保留以下正交事实：

- 登记/上传：`rollouts.status` 与最新 `upload_sessions.status/failure_code`；登记不是设备端
  `CAPTURED/SAVED`。
- 设备执行：`ingest.device_capture_facts` 只接受带精确 organization/project/region scope 的
  device-agent service identity。`CAPTURED` 与带本地文件大小/SHA256的 `SAVED` 均为不可变
  事实；顶栏的设备计数和已确认时长只读取这里，绝不由 Manifest、rollout 或上传时间推断。
- Raw 接收：`rollout_objects.committed_at`，代表对象已通过大小、CRC64、SHA256校验并提交。
- Raw结构：最新 immutable `raw_verification_reports`；`REJECTED` 独立于 QC REJECT。
- QC：`quality_rollout_summaries` 的 current `PASS/RISK/REJECT`。RISK/REJECT 保留Raw且阻断
  标准化，不提供人工强制通过。
- 标准化：最新 `aligned_fragment_attempts`（WRITING/READY/ABORTED）、可用时的
  `workflow.jobs` 技术错误和 `lance_rollout_lineage`。Temporal ingest workflow 在每个阶段和
  终态通过幂等 activity 写入 `workflow.jobs`，并使用 patch marker 兼容历史重放。
  对齐/Lance技术失败不会改写QC结论；
  QC PASS 后如果既没有可用执行结果又不能由 attempt/lineage 判定，技术状态返回
  `UNKNOWN/UNAVAILABLE`，不伪造等待或成功。
- 后续：`annotation_tasks`、最新 `annotation_reviews` 和
  `publishing.rollout_publication_lineage`。

任务 `ACTIVE/CLOSED/CANCELLED` 生命周期和 target attainment 分字段返回；达到数据包目标不会
自动关闭ACTIVE任务。持续时长目标只汇总每个数据包最新的设备 `SAVED` 事实；尚未收到事实时
明确为 0 秒已确认进度，不会用上传、Manifest 或对象时间伪造。

## Activity 真实事件目录

所有展示文本由 Service 的固定 allowlist 生成，不读取 QC report JSON、review comment、failure
detail、对象 key、签名 URL 或异常文本。排序固定为
`occurred_at DESC, event_type DESC, source_id DESC`，Cursor 同时绑定 principal、project、region、
range、timezone 和完整排序 tuple。

| event type | 事实源 | 稳定 source/event identity 与去重键 | occurred_at | 安全状态/目标 |
| --- | --- | --- | --- | --- |
| `UPLOAD_COMMITTED` | `ingest.rollout_objects` | `data_package_id`；`upload_committed:{source_id}` | `committed_at` | 固定 `COMMITTED`；有 session 时链接 upload，否则只返回 rollout 目标 |
| `QC_COMPLETED` | immutable `qc_reports` | `report_sha256`；`qc_completed:{source_id}` | `created_at` | 约束内 `PASS/RISK/REJECT`；目标为对应 upload/rollout |
| `TAG_REVIEW_DECIDED` | immutable `annotation.annotation_reviews` + task + exact rollout region | `review_id`；`tag_review_decided:{source_id}` | `review.created_at` | 约束内 review decision；目标为 annotation task |
| `DATASET_PUBLISHED` | `publishing.rollout_publication_lineage` | publication content identity（按 region 聚合）；`dataset_published:{source_id}` | `published_at` | 固定 `PUBLISHED`；目标为 immutable dataset version |

没有持久化不可变事实的“事件类型”不加入目录。特别地，mutable upload 状态变化没有被包装成完整
历史事件；上传失败只作为当前 pending 投影。

## Pending V1 目录、权限和关闭投影

排序先按技术归一化 domain severity（`CRITICAL > HIGH > MEDIUM > LOW`），同 severity 再按
`opened_at ASC, item_type ASC, source_id ASC`，即等待最久者优先。这里没有 SLA 数值。

| item type | 当前事实/source id | opened_at | severity | capability | 关闭规则 | allowlisted deep link |
| --- | --- | --- | --- | --- | --- | --- |
| `UPLOAD_FAILED` | `ingest.upload_sessions.status=FAILED` / `session_id` | `updated_at` | HIGH | `upload.manage` | session 离开 `FAILED` | `/ingest/uploads/{session_id}` |
| `QC_ANOMALY` | current `quality_rollout_summaries.status in (RISK,REJECT)` / `report_sha256` | `updated_at` | REJECT=CRITICAL，RISK=HIGH | `dataset.read` + `upload.read` | current summary 变为 `PASS` | 对应 `/ingest/uploads/{session_id}` |
| `TAG_REVIEW_PENDING` | `annotation_tasks.status=SUBMITTED` / `task_id` | `updated_at` | MEDIUM | `annotation.review` | task 离开 `SUBMITTED` | `/annotations/tasks/{task_id}` |
| `PUBLICATION_PENDING` | `annotation_tasks.status=APPROVED` 且 rollout 无 publication lineage / `task_id` | `updated_at` | LOW | `dataset_version.publish` | exact rollout lineage 建立 | `/datasets/{dataset_id}/versions/{version}` |

Repository 只接收 Service 已计算的 authorized source enum；SQL 再以 allowlist CTE 取交集。返回模型
还校验每个 item type 位于 `authorized_source_types` 且 deep link 使用固定相对模板。四类当前事实使用
`(item_type, source_id)` 去重，不使用 Browser Mock 六枚举。

## Rollout → publication 区域血缘

`publishing.rollout_publication_lineage` 的不可变身份为
`(project_id, dataset_id, dataset_version, rollout_id)`，同时保存：

- exact `region_code`（只从 `ingest.rollouts(project_id, rollout_id)` 连接）；
- `base_lance_version`、publication content identity 和 `published_at`；
- `FORWARD/BACKFILL` 来源及记录时间。

`PostgresPublishedManifestRepository.create_immutable` 在写入/确认 immutable dataset version 的同一
数据库事务内调用受约束函数补齐全部 rollout lineage，并核对 expected/resolved 数相等。重复发布
重入同一边界；并发冲突仍由 dataset version immutable key 决出唯一 winner。任何 rollout 无法连接
exact ingest region 时整个正向事务以 `PUBLICATION_REGION_LINEAGE_UNRESOLVED` 回滚。

迁移回填只展开历史 manifest 的 rollout，并按 exact project+rollout 连接；重复执行使用同一主键收敛。
不可连接的历史 rollout 不写入，也绝不使用项目默认 region。Dashboard summary 对 exact
project+region+range 只计入确定 lineage；若同 project/range 仍有不可归属历史，已知值投影为
`PARTIAL`，没有任何已知值则 `BLOCKED`。

`lineage_count` 的身份是唯一 publication+rollout 关系，`publication_count` 是 distinct immutable
publication identity；这两个字段不冒充其余 signal/episode/work 指标。

## Repository、时间和资源保护

- Router、Service、Repository 三层重复验证 `dashboard.read`、principal、project、region；每个事实
  SQL 使用 requested-scope CTE，显式携带 principal/project/region 和 `[from,to)`。
- PostgreSQL connection factory 同时设置 `app.subject_id/project_id/region_code`，RLS 再执行 exact
  scope；管理员也不能触发跨 region 聚合。
- 同步范围上限暂为 31 天、page size 为 1～100、statement timeout 为 1.5 s。这些只是技术 DoS/
  fail-fast 保护，不是生产 SLO、p95 或产品默认。
- 响应 `Cache-Control: no-store`。Cursor 使用 HMAC，跨 principal/scope/range/timezone/endpoint
  重放返回稳定 `INVALID_CURSOR`。
- 每个接受的查询写 `core.audit_events`；details 只有 endpoint、query fingerprint 和 result status，
  不含 token、对象 key、签名 URL 或原始异常。

百万行代理数据与索引决定见 `backend/tests/dashboard/EXPLAIN-EVIDENCE.md`；它只证明 query shape，
不代表生产延迟或容量通过。
