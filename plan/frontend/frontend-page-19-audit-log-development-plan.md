# P19 审计日志前端开发计划

> 范围：前端页面与前端 API 需求。
> 后端业务、数据模型和实现尚未确认。
> 本页所有 API、状态和操作均为前端原型草案。

## 页面身份

- 页面 ID：P19
- 页面类型：列表/详情
- 路由：
- `/settings/audit`
- 设计与验收基线：`../../docs/FRONTEND-UI-BASELINE.md`

## 前端区域

指标、筛选、事件表、游标分页和详情面板。

## 前端交互

筛选、打开事件、关闭详情、导出候选入口保持不可用。所有跨页跳转使用稳定 ID 和目标页 route builder。

## API 需求

- 合同状态：只读合同为 `Mock-backed draft`；消费页：P19；导出为候选能力且当前不可用。
- `GET /projects/{projectId}/audit/bootstrap`：请求 `occurredFrom, occurredTo`；响应指标、`asOf, catalogVersion, policyVersion, integrity, allowedActions, blockedReasons, scope, requestId`。
- `GET .../audit/events/facets`：请求时间、actor/event/resource/outcome/risk/requestId 与可选 `regionCode[]`；响应可选 facets。`GET .../audit/events` 增加固定排序、`after | before, limit`，响应 `items, pageInfo, snapshotAt, redaction, scope, requestId`。
- `GET .../audit/events/{eventId}`：响应固定事件、actor、resource、request、outcome、risk、变更摘要、关系、保留/完整性信息与脱敏说明；前端合同禁止出现 secret、凭据、签名 URL 或主机路径。
- 分页只接受一个不透明游标，时间范围变化后复位；错误覆盖 400、403、404/410、429、离线、完整性未知、未知枚举与 `CONTRACT_MISMATCH`。
- 导出、批量下载和完整未脱敏事件为 `draft / user-confirmation-required`；P19 当前无写/导出接口，入口必须 `feature-unavailable`，不得由页面拼接导出文件或移除脱敏。

## 页面状态

按页面需要覆盖 loading、refreshing、empty、filtered-empty、partial-error、fatal-error、forbidden、not-found/gone、conflict、rate-limited、offline、contract-mismatch、unknown-enum 和 feature-unavailable。

这些是前端显示状态，不是后端内部状态机。

## 验收

- 页面布局、主要操作入口和返回路径可用。
- 1440、1024、768、390 四档核心内容可访问。
- Route、Query Codec、API Schema、Adapter、Query Key 和 Mock 可测试。
- 主要正常与失败场景有前端自动化证据。
- 未确认业务不显示为生产可用。
