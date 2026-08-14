# P04 上传详情前端开发计划

> 范围：前端页面与前端 API 需求。
> 后端业务、数据模型和实现尚未确认。
> 本页所有 API、状态和操作均为前端原型草案。

## 页面身份

- 页面 ID：P04
- 页面类型：详情
- 路由：
- `/ingest/uploads/:uploadId`
- 设计与验收基线：`../../docs/FRONTEND-UI-BASELINE.md`

## 前端区域

资源头、概要、对象列表、校验展示、历史与提示。

## 前端交互

分页查看对象、重试或取消候选入口、返回列表。所有跨页跳转使用稳定 ID 和目标页 route builder。

## API 需求

- 合同状态：读取为 `Mock-backed draft`；消费页：P04；重试、暂停、恢复、取消为候选写操作。
- `GET /projects/{projectId}/regions/{regionCode}/upload-sessions/{uploadId}/bootstrap`：响应固定上传资源、统计、`etag, allowedActions, blockedReasons, scope, requestId`。
- `GET .../{uploadId}/objects`、`GET .../{uploadId}/verification-runs`、`GET .../{uploadId}/events`：请求各自筛选、固定排序与 `after | before, limit`；响应 `items, pageInfo, snapshotAt, scope, requestId`。
- `POST .../{uploadId}:retry-verification|:pause|:resume|:cancel`：请求确认字段，带 `Idempotency-Key` 与 `If-Match`；响应更新资源或 `jobId, status, requestId`，页面只轮询已返回的任务标识。
- 错误：403、404/410、409/412、429、离线、部分区块失败与 `CONTRACT_MISMATCH`；对象列表失败不得抹掉已成功的 Bootstrap。
- 候选写操作状态：`draft / user-confirmation-required`；无动作授权或接口未确认时显示 `feature-unavailable`，禁止模拟真实重试或取消完成。

## 页面状态

按页面需要覆盖 loading、refreshing、empty、filtered-empty、partial-error、fatal-error、forbidden、not-found/gone、conflict、rate-limited、offline、contract-mismatch、unknown-enum 和 feature-unavailable。

这些是前端显示状态，不是后端内部状态机。

## 验收

- 页面布局、主要操作入口和返回路径可用。
- 1440、1024、768、390 四档核心内容可访问。
- Route、Query Codec、API Schema、Adapter、Query Key 和 Mock 可测试。
- 主要正常与失败场景有前端自动化证据。
- 未确认业务不显示为生产可用。
