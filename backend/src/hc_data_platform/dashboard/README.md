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
- P01 正式 snapshot 不含 storage；区域容量只属于 P12。
- signal 按项目、区域和显式时间窗口计算云端数据包到达各阶段的去重数量；
  episode/work 状态集合和 freshness SLO 仍未冻结，继续 `BLOCKED`。

## Signal 云端阶段计数

每个阶段都在当前 `organization_id + project_id + region_code + [from,to)` 范围内，按
`data_package_id` 去重。阶段时间分别取对应云端持久化事实的发生时间，因此这些数量是独立的
窗口事件计数，不是百分比，也不强制伪装成单调递减漏斗。

| 阶段 | 云端事实源 | 阶段时间 |
| --- | --- | --- |
| `COLLECTED` | `ingest.rollouts` | `created_at` |
| `RECEIVED` | `ingest.rollout_objects` | `committed_at` |
| `AUTO_QC` | immutable `qc_reports` | `created_at` |
| `ALIGNED_30_HZ` | `aligned_fragment_attempts(status=READY)` | `updated_at` |
| `LANCE` | `lance_rollout_lineage` | `created_at` |
| `ANNOTATION` | `annotation.annotation_tasks` | `created_at` |
| `REVIEW` | `annotation.annotation_reviews` | `created_at` |
| `PUBLISHED` | `publishing.rollout_publication_lineage` | `published_at` |

`COLLECTED` 表示数据包已在云端登记为 rollout，不是设备端未上传的本地 SAVED 回执。
当窗口内八个阶段都为零时 signal 是 `EMPTY`；任一阶段有真实事实时是 `READY`。

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
| `UPLOAD_FAILED` | `ingest.upload_sessions.status=FAILED` / `session_id` | `updated_at` | HIGH | canonical `ingest.upload` | session 离开 `FAILED` | `/ingest/uploads/{session_id}` |
| `QC_ANOMALY` | current `quality_rollout_summaries.status in (RISK,REJECT)` / `report_sha256` | `updated_at` | REJECT=CRITICAL，RISK=HIGH | canonical `datasets.read` + `ingest.upload` | current summary 变为 `PASS` | 对应 `/ingest/uploads/{session_id}` |
| `TAG_REVIEW_PENDING` | `annotation_tasks.status=SUBMITTED` / `task_id` | `updated_at` | MEDIUM | `annotation.review` | task 离开 `SUBMITTED` | `/annotations/tasks/{task_id}` |
| `PUBLICATION_PENDING` | `annotation_tasks.status=APPROVED` 且 rollout 无 publication lineage / `task_id` | `updated_at` | LOW | canonical `datasets.publish` | exact rollout lineage 建立 | `/datasets/{dataset_id}/versions/{version}` |

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
