# P08 数据标注前端开发计划

> 范围：前端页面与前端 API 需求。
> 后端业务、数据模型和实现尚未确认。
> 本页所有 API、状态和操作均为前端原型草案。

## 页面身份

- 页面 ID：P08
- 页面类型：队列/工作台
- 路由：
- `/annotations`
- `/annotations/tasks/:taskId`
- 设计与验收基线：`../../docs/FRONTEND-UI-BASELINE.md`

## 前端区域

任务队列、筛选、Viewer、时间轴、标注表单和 Inspector。

## 前端交互

打开任务、编辑本地草稿、模拟保存/提交和错误恢复。所有跨页跳转使用稳定 ID 和目标页 route builder。

## API 需求

- 合同状态：读取与编辑流程为 `Mock-backed draft`；消费页：P08；提交、复核和 rebase 需用户确认。
- `GET /projects/{projectId}/regions/{regionCode}/annotation-tasks`：请求 `queue, states, datasetId, schemaVersionId, assigneeId, q, sort, after | before, limit`；响应 `items, pageInfo, snapshotAt, scope, requestId`。`GET .../annotation-tasks/{taskId}` 返回 Task、Draft、Schema 与 `etag, allowedActions, blockedReasons`。
- `GET .../episode-revisions/{revisionId}/annotation-task-entry-resolution`：请求 `start_ns, end_ns, stream_id[], ontology_id, ontology_version`；响应解析后的固定 Revision/Stream/Schema 上下文，不得由客户端猜测绑定。
- `POST .../{taskId}:claim`、`PUT .../{taskId}/draft`：请求会话或 `expected_draft_revision, client_mutation_id, entries`；均带 `Idempotency-Key, If-Match`，响应 Task/Draft 及内容哈希。
- `POST .../{taskId}:submit-preflight|:submit|:review|:rebase`：请求期望 revision/hash、预检标识或决定；响应完成资源或 `jobId, status, requestId`。分页为不透明游标，筛选变化后复位。
- 错误覆盖 400、403、404/410、409/412、422、429、离线、租约/草稿冲突及 `CONTRACT_MISMATCH`。候选提交、复核、rebase 状态为 `draft / user-confirmation-required`；未确认时仅 MSW 或 `feature-unavailable`。

## 页面状态

按页面需要覆盖 loading、refreshing、empty、filtered-empty、partial-error、fatal-error、forbidden、not-found/gone、conflict、rate-limited、offline、contract-mismatch、unknown-enum 和 feature-unavailable。

这些是前端显示状态，不是后端内部状态机。

## 验收

- 页面布局、主要操作入口和返回路径可用。
- 1440、1024、768、390 四档核心内容可访问。
- Route、Query Codec、API Schema、Adapter、Query Key 和 Mock 可测试。
- 主要正常与失败场景有前端自动化证据。
- 未确认业务不显示为生产可用。
