# P02 数据源前端开发计划

> 范围：前端页面与前端 API 需求。
> 后端业务、数据模型和实现尚未确认。
> 本页所有 API、状态和操作均为前端原型草案。

## 页面身份

- 页面 ID：P02
- 页面类型：列表/管理
- 路由：
- `/ingest/sources`
- 设计与验收基线：`../../docs/FRONTEND-UI-BASELINE.md`

## 前端区域

页面头、摘要、筛选、数据表、详情面板、编辑弹窗。

## 前端交互

搜索筛选、查看、新建或编辑原型、不可用操作提示。所有跨页跳转使用稳定 ID 和目标页 route builder。

## API 需求

- 合同状态：读取为 `Mock-backed draft`；消费页：P02；真实可写能力仍未确认。
- `GET /projects/{projectId}/regions/{regionCode}/data-sources/page`：请求 `q, connectorType, status, sort, after | before, limit`；响应 `items, facets, counts, pageInfo, snapshotAt, scope, requestId`。`GET .../data-sources/{sourceId}` 返回固定资源、`etag, allowedActions, blockedReasons`，响应不得回显凭据。
- `POST .../data-sources`、`PATCH .../data-sources/{sourceId}` 及 `POST .../{sourceId}:test-connection|:rotate-credential|:enable|:disable`：请求为连接器公开配置与 `Idempotency-Key`，更新类另带 `If-Match`；响应资源信封或连接测试任务信封，任何 secret 只允许一次性提交、不可进入页面缓存。
- 分页只接受不透明 `after` 或 `before`，不得同时提交；响应保留 `pageInfo` 与 `snapshotAt`。
- 错误：400 字段校验、403、404/410、409、412、429、离线与 `CONTRACT_MISMATCH`，均使用统一错误信封。
- 候选写操作状态：`draft / user-confirmation-required`；未确认、无 `allowedActions` 或无安全凭据通道时按钮为 `feature-unavailable`，仅可使用去敏 Mock。

## 页面状态

按页面需要覆盖 loading、refreshing、empty、filtered-empty、partial-error、fatal-error、forbidden、not-found/gone、conflict、rate-limited、offline、contract-mismatch、unknown-enum 和 feature-unavailable。

这些是前端显示状态，不是后端内部状态机。

## 验收

- 页面布局、主要操作入口和返回路径可用。
- 1440、1024、768、390 四档核心内容可访问。
- Route、Query Codec、API Schema、Adapter、Query Key 和 Mock 可测试。
- 主要正常与失败场景有前端自动化证据。
- 未确认业务不显示为生产可用。
