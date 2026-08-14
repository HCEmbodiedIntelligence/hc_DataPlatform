# P05 数据集前端开发计划

> 范围：前端页面与前端 API 需求。
> 后端业务、数据模型和实现尚未确认。
> 本页所有 API、状态和操作均为前端原型草案。

## 页面身份

- 页面 ID：P05
- 页面类型：列表
- 路由：
- `/datasets`
- 设计与验收基线：`../../docs/FRONTEND-UI-BASELINE.md`

## 前端区域

页面头、摘要、组合筛选、数据表和游标分页。

## 前端交互

筛选排序、打开详情、展示创建入口。所有跨页跳转使用稳定 ID 和目标页 route builder。

## API 需求

- 合同状态：读取为 `Mock-backed draft`；消费页：P05；创建为需确认候选能力。
- `GET /projects/{projectId}/datasets`：请求 `q, status, schema, robotId, createdFrom, createdTo, sort, after | before, limit`；响应 `items, pageInfo, snapshotAt, scope, requestId`。
- `GET .../datasets:page-capabilities`、`GET .../datasets:summary`、`GET .../datasets:facets`：后两者复用当前筛选但不带游标；响应能力、计数与 facets，且必须与列表作用域一致。
- `POST .../datasets`：请求公开名称、描述及已选择的来源标识，带 `Idempotency-Key`；响应 `data, scope, requestId, contractVersion`。
- 分页使用不透明游标，排序必须含稳定 ID；错误覆盖 400、403、404、409/412、429、离线、未知枚举与 `CONTRACT_MISMATCH`。
- 候选创建状态：`draft / user-confirmation-required`；能力未返回或接口未确认时入口为 `feature-unavailable`，不得伪造创建成功或导出能力。

## 页面状态

按页面需要覆盖 loading、refreshing、empty、filtered-empty、partial-error、fatal-error、forbidden、not-found/gone、conflict、rate-limited、offline、contract-mismatch、unknown-enum 和 feature-unavailable。

这些是前端显示状态，不是后端内部状态机。

## 验收

- 页面布局、主要操作入口和返回路径可用。
- 1440、1024、768、390 四档核心内容可访问。
- Route、Query Codec、API Schema、Adapter、Query Key 和 Mock 可测试。
- 主要正常与失败场景有前端自动化证据。
- 未确认业务不显示为生产可用。
