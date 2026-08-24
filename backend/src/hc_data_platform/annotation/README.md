# 数据标注修订模块（F0-04 / F1-04 / F2-04 / F3-03）

该模块负责标注任务、当前/草稿投影、不可变修订、仅追加操作和审核决定。
`EXCLUDE` 与 `RESTORE` 始终使用半开区间的同步步骤范围，因此会作用于该步骤的所有模态。
生效范围经过规范化，保持有序且互不重叠。恢复也是一种操作；它绝不会删除此前的排除操作，
也不会更改原始数据或 Lance 数据。

`AnnotationService` 基于 `AnnotationRepositoryPort` 运行。内存仓库与 PostgreSQL 使用
相同的状态版本比较并交换边界，`PostgresAnnotationRepository` 接受 DB-API 2 连接工厂。
组合该适配器前，请按顺序应用 `migrations/annotation/`。数据库驱动仍为
可选依赖，由应用组合层负责提供。

每次保存草稿都必须同时满足三个前置条件：`expected_revision`、`If-Match` 和
`client_mutation_id`。重复提交完全相同的变更会返回原来的不可变修订；在同一 ID 下更改
请求正文或前置条件会返回 409。修订或 ETag 过期也返回并发 409。领取、保存、提交和审核操作
都会推进由强 ETag 表示的任务状态版本。

HTTP 路由从 `request.state.auth_context` 读取已由 BE-02 验证的 `AuthContext`。
标注员、审核员和发布者的操作使用不同的角色检查；所有公开读取都限定在项目范围内。
OPEN-08 未确认时，自审由 `SelfReviewPolicy.UNCONFIRMED` 保持未冻结；部署只能显式配置
`ALLOW` 或 `DENY`，不能由 UI 或角色名推断。发布模块只能获得 `AnnotationApprovedV1`，即一个精确的已批准修订及其
生效排除范围快照。后续任何编辑都会创建新修订并移除当前批准指针，同时保留审核历史。

Tag Schema 以 `schema_id + version` 标识。Schema 内容支持任意深度的父子 Tag 图、路径、
继承必填属性、互斥集合和对象关系；代码没有最大层级常量。Schema 发布只允许把相同内容的
草稿状态冻结为 `PUBLISHED`，数据库触发器阻止已发布版本的修改或删除。任务、草稿、提交和
审核都固定 `base_lance_version + tag_schema_id + tag_schema_version`。区间始终是
`[start_step,end_step)`，并相对固定基线的 `base_step_count` 验证。

`submit` 使用 `Idempotency-Key` 生成不可变 `AnnotationSubmission`，其中包含层级、边界、
必填属性、互斥冲突、对象关系和 Schema 版本六项检查。审核必须引用该精确提交；完整性和
对齐不在这里重复审核。

`AnnotationReviewPreparationWorkflow` 为 worker 提供同一 reviewable-version 生成边界；
它只接受已固定的 Lance 版本、已发布 Schema、精确 revision 和累计操作，生成完整六项
检查及内容哈希绑定，并由 Temporal 历史 replay 测试保护确定性。

外部旧 P11 step 记录仍可通过 `LegacyCleaningAnnotationAdapter` 单向导入：明确的排除/恢复
操作成为普通 annotation revision，未知规则保存在 `LegacyAuditReference.source_payload`。
数据库内已经存在的 P11 Workbench 行由
`annotation/0008_scoped_legacy_cleaning_import.sql` 一次性迁移。该迁移只接受能从固定 Episode
revision、selected stream 精确解析到同组织/项目/区域 Lance 血缘的草稿；缺失或多义会在任何
写入前让整个迁移失败。P11 EDL 是纳秒区间，annotation 是 step 区间，因此迁移不会猜采样
换算，而是把完整 EDL snapshot 以 `PRESERVED_NANOSECOND_EDL` 模式保存在不可变审计载荷中。
映射键包含 organization/project/region，所以不同租户的同名 draft 不再碰撞。

PostgreSQL 的 claim、revision/restore、submission 和 review CAS 会在同一事务追加脱敏
`core.audit_events`；REJECT/NEEDS_REVISION 只记录决定、revision 和 submission ID，不记录审核
意见正文。审计写失败会回滚业务指针。

`AutoAnnotationProvider` 只是协议。`DisabledAutoAnnotationProvider` 会报告
`enabled=false` 并抛出 `FEATURE_DISABLED`；系统中不存在 VLM 任务表或虚假结果。

在 `backend/` 目录运行专项验证：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/pytest tests/annotation
.venv/bin/ruff format --check src/hc_data_platform/annotation tests/annotation
.venv/bin/ruff check src/hc_data_platform/annotation tests/annotation
.venv/bin/mypy src/hc_data_platform/annotation
```
