# P17 数据 Schema前端开发计划

> 范围：前端页面与前端 API 需求。
> 后端业务、数据模型和实现尚未确认。
> 本页所有 API、状态和操作均为前端原型草案。

## 页面身份

- 页面 ID：P17
- 页面类型：管理
- 路由：
- `/settings/data-schemas`
- 设计与验收基线：`../../docs/FRONTEND-UI-BASELINE.md`

## 前端区域

Registry、版本详情、定义、兼容性和引用区域。

## 前端交互

搜索、选择固定版本、展示校验/发布候选入口。所有跨页跳转使用稳定 ID 和目标页 route builder。

## API 需求

- 合同状态：读取为 `Mock-backed draft`；消费页：P17，P15 仅消费 route resolution；Schema 发布为候选能力。
- `GET /projects/{projectId}/regions/{regionCode}/route-resolutions/p15-to-p17`：请求 `schemaId, schemaVersion, componentId, detailTab`；响应 `canonicalQuery, relationRevision, allowedActions=[VIEW], blockedReasons, scope, requestId`，禁止客户端猜测跨页绑定。
- `GET /organizations/{organizationId}/stream-schemas`：请求 `q, status, logicalType, sort, after | before, limit`；响应 `items, pageInfo, snapshotAt, scope, requestId`。`GET .../stream-schemas/{schemaId}/versions/{schemaVersion}` 返回定义、兼容模式/结果、哈希、`etag, allowedActions, blockedReasons`。
- `POST .../stream-schemas/{schemaId}/versions/{schemaVersion}:preflight-publish` 请求期望哈希/ETag、校验报告、兼容性检查与变更说明；响应 `allowed, preflightToken, expiresAt, impacts, warnings, blockers`。`POST .../stream-schemas/{schemaId}/versions/{schemaVersion}:publish` 请求 token，带 `If-Match, Idempotency-Key`。
- 分页使用不透明游标；错误覆盖 400、403、404/410、409/412、422、429、离线、关系/校验陈旧、未知枚举与 `CONTRACT_MISMATCH`。
- Schema 新建、编辑、预检和发布为 `draft / user-confirmation-required`；未确认时仅只读 Mock 或 `feature-unavailable`，不得把本地定义标记为已发布。

## 页面状态

按页面需要覆盖 loading、refreshing、empty、filtered-empty、partial-error、fatal-error、forbidden、not-found/gone、conflict、rate-limited、offline、contract-mismatch、unknown-enum 和 feature-unavailable。

这些是前端显示状态，不是后端内部状态机。

## 验收

- 页面布局、主要操作入口和返回路径可用。
- 1440、1024、768、390 四档核心内容可访问。
- Route、Query Codec、API Schema、Adapter、Query Key 和 Mock 可测试。
- 主要正常与失败场景有前端自动化证据。
- 未确认业务不显示为生产可用。
