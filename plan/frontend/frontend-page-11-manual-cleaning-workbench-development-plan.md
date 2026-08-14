# P11 手动清洗工作台前端开发计划

> 范围：前端页面与前端 API 需求。
> 后端业务、数据模型和实现尚未确认。
> 本页所有 API、状态和操作均为前端原型草案。

## 页面身份

- 页面 ID：P11
- 页面类型：工作台
- 路由：
- `/manual/drafts/:draftId`
- 设计与验收基线：`../../docs/FRONTEND-UI-BASELINE.md`

## 前端区域

资源头、Viewer、时间轴、编辑区、预览和提示区。

## 前端交互

编辑前端草稿、模拟保存/预览/提交、处理冲突提示。所有跨页跳转使用稳定 ID 和目标页 route builder。

## API 需求

- 合同状态：工作台流程为 `Mock-backed draft`；消费页：P11；保存、预览与提交均需用户确认。
- `GET /projects/{projectId}/regions/{regionCode}/cleaning-drafts/{draftId}/bootstrap`：响应固定草稿、来源 Version/Revision、EDL、媒体可用性、预览/提交摘要、`etag, allowedActions, blockedReasons, scope, requestId`。`GET .../{draftId}/review-findings` 仅返回 P07 已产生的只读反馈。
- `PUT .../{draftId}/edl`：请求 `expected_edl_revision, expected_operation_hash, operations, client_mutation_id`，带 `If-Match`；响应更新后的 Draft/EDL 与 `requestId`。
- `POST .../{draftId}/previews`：请求 `base_revision_id, edl_revision, operation_hash`；`POST .../{draftId}/commits` 另带 `preview_id, successor_composition_hash, acknowledgement`；两者均带 `If-Match, Idempotency-Key`，响应接受任务而非伪造完成态。
- 页面无列表分页；媒体没有可验证描述符时保持安全占位。错误覆盖 400、403、404/410、409/412、422、429、离线、内容哈希漂移与 `CONTRACT_MISMATCH`。
- 保存、预览、提交和媒体加载状态为 `draft / user-confirmation-required`；未确认时仅 MSW 或 `feature-unavailable`，提交必须有显式确认且不得本地制造 successor。

## 页面状态

按页面需要覆盖 loading、refreshing、empty、filtered-empty、partial-error、fatal-error、forbidden、not-found/gone、conflict、rate-limited、offline、contract-mismatch、unknown-enum 和 feature-unavailable。

这些是前端显示状态，不是后端内部状态机。

## 验收

- 页面布局、主要操作入口和返回路径可用。
- 1440、1024、768、390 四档核心内容可访问。
- Route、Query Codec、API Schema、Adapter、Query Key 和 Mock 可测试。
- 主要正常与失败场景有前端自动化证据。
- 未确认业务不显示为生产可用。
