# P12 存储容量前端开发计划

> 范围：前端页面与前端 API 需求。
> 后端业务、数据模型和实现尚未确认。
> 本页所有 API、状态和操作均为前端原型草案。

## 页面身份

- 页面 ID：P12
- 页面类型：汇总/分析
- 路由：
- `/storage/overview`
- 设计与验收基线：`../../docs/FRONTEND-UI-BASELINE.md`

## 前端区域

页面头、指标、图表、Inventory 表、对象详情和费用区域。

## 前端交互

切换区域、查看详情、刷新候选数据。所有跨页跳转使用稳定 ID 和目标页 route builder。

## API 需求

- 合同状态：只读合同为 `Mock-backed draft`；消费页：P12；刷新只是重新读取，不代表启动服务端作业。
- `GET /projects/{projectId}/storage/overview`：请求 `regionCode, months(3|6|12)`；响应指标、趋势、分类分布、`scope, asOf, requestId`。`GET .../storage/cost-breakdown` 请求 `regionCode, billingPeriod?`，响应可用性明确的费用拆分与币种/周期。
- `GET .../storage/objects`：请求 `regionCode, objectRole, storageClass, anomaly[], status[], sort, after | before, limit`；响应 `items, pageInfo, snapshotAt, scope, requestId`。
- `GET .../storage/objects/{objectId}` 请求 `regionCode, snapshotId`，响应固定对象详情；`GET .../storage/multipart` 请求状态、稳定排序与游标，响应未完成分片页。
- 分页只接受一个不透明游标；对象详情必须与列表 `snapshotId` 对齐。错误覆盖 403、404/410、429、离线、局部失败、费用不可见与 `CONTRACT_MISMATCH`。
- 对象处置、费用预测和强制扫描为 `draft / user-confirmation-required`；P12 当前无写接口，必须 `feature-unavailable`，Mock 数据不得标记为真实账单。

## 页面状态

按页面需要覆盖 loading、refreshing、empty、filtered-empty、partial-error、fatal-error、forbidden、not-found/gone、conflict、rate-limited、offline、contract-mismatch、unknown-enum 和 feature-unavailable。

这些是前端显示状态，不是后端内部状态机。

## 验收

- 页面布局、主要操作入口和返回路径可用。
- 1440、1024、768、390 四档核心内容可访问。
- Route、Query Codec、API Schema、Adapter、Query Key 和 Mock 可测试。
- 主要正常与失败场景有前端自动化证据。
- 未确认业务不显示为生产可用。
