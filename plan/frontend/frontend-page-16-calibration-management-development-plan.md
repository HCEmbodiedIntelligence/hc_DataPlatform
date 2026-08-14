# P16 标定管理前端开发计划

> 范围：前端页面与前端 API 需求。
> 后端业务、数据模型和实现尚未确认。
> 本页所有 API、状态和操作均为前端原型草案。

## 页面身份

- 页面 ID：P16
- 页面类型：管理
- 路由：
- `/settings/calibrations`
- 设计与验收基线：`../../docs/FRONTEND-UI-BASELINE.md`

## 前端区域

标定列表、关系图、详情、历史和报告区域。

## 前端交互

选择记录、切换详情、展示校验/发布候选入口。所有跨页跳转使用稳定 ID 和目标页 route builder。

## API 需求

- 合同状态：读取为 `Mock-backed draft`；消费页：P16；校验与发布为候选能力。
- `GET /projects/{projectId}/regions/{regionCode}/calibration-sets`：请求 `q, robotId, componentId, status, sort, after | before, limit`；响应 `items, pageInfo, snapshotAt, scope, requestId`。`GET .../calibration-sets/{setId}` 返回固定记录、版本、可用期、内容/校验哈希、报告标识、`etag, allowedActions, blockedReasons`。
- `POST .../calibration-sets/{setId}/versions/{version}:preflight-publish`：请求 `expected_hash, expected_etag, validation_context_hash, validation_report_id, change_summary`，带 `If-Match, Idempotency-Key`；响应 `allowed, preflightToken, expiresAt, impacts, warnings, blockers`。
- `POST .../calibration-sets/{setId}/versions/{version}:publish`：请求有效 `preflight_token`，带相同资源的 `If-Match` 与新幂等键；响应发布结果及 `requestId`。
- 分页只接受单个不透明游标；错误覆盖 400、403、404/410、409/412、422、429、离线、报告/哈希陈旧与 `CONTRACT_MISMATCH`。
- 校验生成、预检和发布状态为 `draft / user-confirmation-required`；未确认或缺少有效报告时显示 `feature-unavailable`，不得伪造 READY/PUBLISHED。

## 页面状态

按页面需要覆盖 loading、refreshing、empty、filtered-empty、partial-error、fatal-error、forbidden、not-found/gone、conflict、rate-limited、offline、contract-mismatch、unknown-enum 和 feature-unavailable。

这些是前端显示状态，不是后端内部状态机。

## 验收

- 页面布局、主要操作入口和返回路径可用。
- 1440、1024、768、390 四档核心内容可访问。
- Route、Query Codec、API Schema、Adapter、Query Key 和 Mock 可测试。
- 主要正常与失败场景有前端自动化证据。
- 未确认业务不显示为生产可用。
