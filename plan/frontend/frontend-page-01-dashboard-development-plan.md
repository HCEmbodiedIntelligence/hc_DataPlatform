# P01 工作台前端开发计划

> 范围：前端页面与前端 API 需求。
> 后端业务、数据模型和实现尚未确认。
> 本页所有 API、状态和操作均为前端原型草案。

## 页面身份

- 页面 ID：P01
- 页面类型：汇总/分析
- 路由：
- `/dashboard`
- 设计与验收基线：`../../docs/FRONTEND-UI-BASELINE.md`

## 前端区域

页面头、时间范围、指标区、图表区、待办与活动区。

## 前端交互

切换时间范围、查看局部详情、重试失败区域。所有跨页跳转使用稳定 ID 和目标页 route builder。

## API 需求

- 合同状态：`Mock-backed draft`；消费页：P01；下列合同只描述当前页面可观察行为，不代表真实接口已联调。
- `GET /projects/{projectId}/regions/{regionCode}/dashboard/snapshot`：请求 `timezone, storageMonths=6`；响应 `scope, metrics, storageTrend, asOf, requestId`。
- `GET .../dashboard/activity`：请求 `from, to, timezone`；响应活动序列、时间窗口、`scope, requestId`。`GET .../dashboard/coverage` 返回覆盖率分组与采样时间。
- `GET .../dashboard/pending-items`：请求固定排序及 `after | before, limit(5|50)`；响应 `items, pageInfo, snapshotAt, scope, requestId`，空页游标必须为空。
- 错误：统一前端错误信封 `code, message, fieldErrors, operationErrors, blockedReasons, requestId, retryable`；P01 映射 403、429、离线、分区失败与 `CONTRACT_MISMATCH`。
- 候选能力：真实聚合口径与跨区统计为 `draft / user-confirmation-required`；未确认或 Mock 未覆盖时仅显示 `feature-unavailable`，不得合成指标。

## 页面状态

按页面需要覆盖 loading、refreshing、empty、filtered-empty、partial-error、fatal-error、forbidden、not-found/gone、conflict、rate-limited、offline、contract-mismatch、unknown-enum 和 feature-unavailable。

这些是前端显示状态，不是后端内部状态机。

## 验收

- 页面布局、主要操作入口和返回路径可用。
- 1440、1024、768、390 四档核心内容可访问。
- Route、Query Codec、API Schema、Adapter、Query Key 和 Mock 可测试。
- 主要正常与失败场景有前端自动化证据。
- 未确认业务不显示为生产可用。
