# E 挂接与部署顺序

C 未修改 `runtime.py`、`workflow/worker.py`、路由总装配、OpenAPI、迁移 manifest/phases 或公共 G0 契约。以下改动需 E 在集成分支统一完成；直接部署 C 分支不会自动注册新路由/Worker。

1. 先将 `backend/migrations/ingest/017_robot_processing.sql` 加入迁移清单和 expand 阶段，并更新迁移声明/hash。顺序在 ingest/016 之后。新增 robot_processing / robot_processing_requests 两张有 RLS 的表；允许已完成 RISK/REJECT 的源 episode 没有训练版本。旧迁移没有改动，也不自动扫描/回填历史 Raw。
2. 迁移成功后再部署 C commit 代码。新的兼容机器人 LeRobot commit 会在同一事务内写 processing row、job.workflow_id 和 outbox；缺迁移会使 commit 失败并回滚，不能忽略。
3. API runtime 用现有 scoped connection factory 构建 `ProcessingStore`，调用 `processing_api.configure_processing(store)`，总路由加入 `processing_api.router`。沿用现有机器人 Bearer 认证例外，不让用户 JWT 中间件拦截这些机器人路径。生成公共 OpenAPI。
4. Worker 使用相同 `ProcessingStore`、生产 ObjectStoragePort 和 B 已确认实现构造 `RobotProcessingActivities`。注册 `RobotIngestProcessingWorkflow` 和该实例的 `.activities`；同步 activities 需要有线程 executor（测试使用 ThreadPoolExecutor）。沿用 Worker 已有维护模式、writer permit、shutdown/health 生命周期，E 需将新 activities 纳入这些公共守卫。
5. 在现有 outbox handler 表增加 `robot.ingest.processing.requested.v1` → `RobotProcessingOutboxHandler(temporal_client, store, task_queue=已注册新workflow的队列)`。沿用现有 `serve_outbox`、exact organization/project/region scopes 与租约恢复。handler 使用同一作用域检查事件与 DB generation/workflow_id，Temporal 使用 REJECT_DUPLICATE，避免响应丢失重开已完成 workflow。
6. 将 B 真实通用/OpenArm 入口适配到 C 端口，处理 [B-INTERFACE.md](B-INTERFACE.md) 中的 manifest/episode ID/子流程回执差异；没有 B 时默认 ProcessorUnavailable 会真实读 meta/info 后返回可显式重试的失败。

示意接线（变量来自 E 当前 runtime，不能原样当完整配置文件）：

```python
from hc_data_platform.robot_ingest import processing_api
from hc_data_platform.robot_ingest.processing_store import ProcessingStore, EVENT_TYPE
from hc_data_platform.robot_ingest.processing_worker import (
    RobotProcessingActivities, RobotProcessingOutboxHandler,
)
from hc_data_platform.robot_ingest.processing_workflow import RobotIngestProcessingWorkflow

store = ProcessingStore(scoped_connections)
processing_api.configure_processing(store)
app.include_router(processing_api.router)
robot_activities = RobotProcessingActivities(store, object_storage, b_processor)
workflows.append(RobotIngestProcessingWorkflow)
activities.extend(robot_activities.activities)
handlers[EVENT_TYPE] = RobotProcessingOutboxHandler(
    temporal_client, store, task_queue=ingest_queue,
)
```

`robot_ingest.__init__` 改为延迟导入 service，保留公开 API；这是为了 Temporal sandbox 导入 workflow 时不提前加载 JWT/cryptography。

## 恢复与运维

- API commit 前事务失败：Raw/job/outbox/upload 一起回滚。已上传对象和可能先写出的 manifest 保留，客户端重试同一 upload；并发创建 manifest 时校验获胜对象的不可变身份/资产后继续。
- outbox start 成功但确认失败：租约到期重投，重复 Temporal workflow_id 不重开工作流。Worker 离线或重启由 Temporal 持久历史接续。
- episode 单独保存；处理投影写入 upload JSON、Raw 汇总、job 和 raw_source_episodes 使用同一事务。失败/完成投影活动无限重试暂时的 DB 故障，避免因一次持久化失败把流程丢在内存中。
- 终态技术失败：机器人用持久 request_id 调 `:retry-processing`。保留稳定 Raw/episode 身份，只有 retryable 的失败段重置；旧 generation 的活动写回被拒绝。不可重试问题需修复导出生成新包，质量终态需人工审核/补采。
- 历史 COMMITTED、完整兼容且无已有 episode 的源可以显式恢复；已有业务处理事实的历史源拒绝自动覆盖，需 E 对账。原离线存档不会被批量自动投入处理。
- 手工 terminate/cancel Temporal、持续数据库不可用后的运维对账、B 发布成功但回执未保存的跨模块恢复，仍需 E/B 集成验收；本轮没有把它们计为通过。

## C 测试环境

只使用 `openarm-g0-c` Compose project，宿主端口 25532/27333/29102；未启动 API/frontend/gateway 产品服务，API 请求通过实际 FastAPI 路由的 TestClient，MinIO 分片 PUT 走真实 HTTP。原离线平台与 B 资源未启动/修改。

基础 Docker Hub 下载超时后使用 [compose.cached.json](compose.cached.json) 和 [Dockerfile.cached](Dockerfile.cached)；仍通过 G0 C Compose 入口构建 C 专用镜像，C 源码只读挂载。复现：

```bash
bash /home/hc_op/workspace/openarm-integration/C/platform/artifacts/g0/test-processing.sh --cached
```

该脚本只给 C 测试库直接应用新增 SQL，**不修改公共迁移清单，也没有把该次 SQL 写入正式 migration ledger**。E 发布时应通过正式清单在自己的隔离集成库应用；若复用 C 测试库，先核对临时迁移事实，不可盲目再次 CREATE TABLE。C 的 `hc_c_regression` 数据库只运行原基线 PostgreSQL 回归。

容器和 synthetic 数据卷保留供核查。停止（不删除卷）仍用 G0 入口：

```bash
bash /home/hc_op/workspace/openarm-data-integration-plan/g0/scripts/compose.sh C stop temporal minio postgres
```
