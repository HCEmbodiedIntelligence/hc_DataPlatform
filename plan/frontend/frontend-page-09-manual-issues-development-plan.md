# P09 人工问题前端开发计划

> 范围：前端页面与前端 API 需求。
> 后端业务、数据模型和实现尚未确认。
> 本页所有 API、状态和操作均为前端原型草案。

## 页面身份

- 页面 ID：P09
- 页面类型：列表/管理
- 路由：
- `/manual/issues`
- 设计与验收基线：`../../docs/FRONTEND-UI-BASELINE.md`

## 前端区域

页面头、摘要、筛选、问题表和详情面板。

## 前端交互

查看、筛选、展示分诊和后续入口。所有跨页跳转使用稳定 ID 和目标页 route builder。

## API 需求

- 合同状态：读取为 `Mock-backed draft`；消费页：P09；新建、分诊、解决与转草稿为候选写操作。
- `GET /projects/{projectId}/regions/{regionCode}/manual-issues:page`：请求当前筛选，响应 `counts, facets, allowedActions, snapshotAt, scope, requestId`。`GET .../manual-issues` 增加稳定 `sort, after | before, limit`，响应 `items, pageInfo, snapshotAt`。
- `GET .../manual-issues/{manualIssueId}`：响应固定问题、来源 Version/Revision/Stream/时间范围、`etag, allowedActions, blockedReasons`；没有真实媒体描述符时详情只显示安全占位。
- `POST .../manual-issues` 请求来源标识、`start_ns, end_ns, issue_type, severity, note`；`POST .../{id}:triage|:resolve` 请求目标状态/责任人与原因或解决版本/说明；均带 `Idempotency-Key`，已有资源操作另带 `If-Match`。
- `POST .../{id}/cleaning-drafts` 请求空对象并复用问题上下文；响应 `CREATED | ALREADY_LINKED | SELECTION_REQUIRED` 及明确候选，不允许客户端重算来源。
- 错误覆盖 400、403、404/410、409/412、422、429、离线与 `CONTRACT_MISMATCH`。所有写操作和媒体预览为 `draft / user-confirmation-required`；未确认时仅 MSW 或 `feature-unavailable`。

## 页面状态

按页面需要覆盖 loading、refreshing、empty、filtered-empty、partial-error、fatal-error、forbidden、not-found/gone、conflict、rate-limited、offline、contract-mismatch、unknown-enum 和 feature-unavailable。

这些是前端显示状态，不是后端内部状态机。

## 验收

- 页面布局、主要操作入口和返回路径可用。
- 1440、1024、768、390 四档核心内容可访问。
- Route、Query Codec、API Schema、Adapter、Query Key 和 Mock 可测试。
- 主要正常与失败场景有前端自动化证据。
- 未确认业务不显示为生产可用。
