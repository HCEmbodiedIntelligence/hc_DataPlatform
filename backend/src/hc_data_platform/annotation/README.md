# 标注模块（BE-09）

该模块负责标注任务、当前/草稿投影、不可变修订、仅追加操作和审核决定。
`EXCLUDE` 与 `RESTORE` 始终使用半开区间的同步步骤范围，因此会作用于该步骤的所有模态。
生效范围经过规范化，保持有序且互不重叠。恢复也是一种操作；它绝不会删除此前的排除操作，
也不会更改原始数据或 Lance 数据。

`AnnotationService` 基于 `AnnotationRepositoryPort` 运行。内存仓库与 PostgreSQL 使用
相同的状态版本比较并交换边界，`PostgresAnnotationRepository` 接受 DB-API 2 连接工厂。
组合该适配器前，请先应用 `migrations/annotation/0001_annotation.sql`。数据库驱动仍为
可选依赖，由应用组合层负责提供。

每次保存草稿都必须同时满足三个前置条件：`expected_revision`、`If-Match` 和
`client_mutation_id`。重复提交完全相同的变更会返回原来的不可变修订；在同一 ID 下更改
请求正文或前置条件会返回 409。修订或 ETag 过期会返回 412。领取、保存、提交和审核操作
都会推进由强 ETag 表示的任务状态版本。

HTTP 路由从 `request.state.auth_context` 读取已由 BE-02 验证的 `AuthContext`。
标注员、审核员和发布者的操作使用不同的角色检查；所有公开读取都限定在项目范围内；
始终禁止自我审核。发布模块只能获得 `AnnotationApprovedV1`，即一个精确的已批准修订及其
生效排除范围快照。后续任何编辑都会创建新修订并移除当前批准指针，同时保留审核历史。

`AutoAnnotationProvider` 只是协议。`DisabledAutoAnnotationProvider` 会报告
`enabled=false` 并抛出 `FEATURE_DISABLED`；系统中不存在 VLM 任务表或虚假结果。

在 `backend/` 目录运行专项验证：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/pytest tests/annotation
.venv/bin/ruff format --check src/hc_data_platform/annotation tests/annotation
.venv/bin/ruff check src/hc_data_platform/annotation tests/annotation
.venv/bin/mypy src/hc_data_platform/annotation
```
