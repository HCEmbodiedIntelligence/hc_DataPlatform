# P07 版本详情前端开发计划

> 范围：前端页面与前端 API 需求。
> 后端业务、数据模型和实现尚未确认。
> 本页所有 API、状态和操作均为前端原型草案。

## 页面身份

- 页面 ID：P07
- 页面类型：详情/候选操作
- 路由：
- `/datasets/:datasetId/versions/:versionId`
- 设计与验收基线：`../../docs/FRONTEND-UI-BASELINE.md`

## 前端区域

版本头、概要、Episode、Revision、差异、Manifest、Schema 和检查结果。

## 前端交互

查看固定快照、打开 Episode、展示复核候选入口。所有跨页跳转使用稳定 ID 和目标页 route builder。

## API 需求

- 合同状态：读取为 `Mock-backed draft`；消费页：P07；复核、差异任务与下载均为候选能力。
- `GET /projects/{projectId}/datasets/{datasetId}/versions/{versionId}/bootstrap`：响应固定 Version、`snapshotToken, operationalRevision, etag, allowedActions, blockedReasons, scope, requestId`。
- `GET .../{versionId}/episodes|manifest|required-storage|operational-inventory`：请求固定快照/运营 revision、筛选及 `after | before, limit`；响应 `items, pageInfo, snapshotAt`。`GET .../episode-revisions/{revisionId}` 与 `GET .../{versionId}/schema` 返回固定快照详情。
- `POST .../{versionId}/review-checks`、`POST .../{versionId}:approve|:return`、`POST .../{versionId}/diff-jobs`：请求期望状态/复核内容/比较版本，写操作带 `If-Match`，危险或可重试操作带 `Idempotency-Key`；响应检查结果、版本结果或 `jobId`。
- 错误覆盖 400、403、404/410、409/412、422、429、离线、快照漂移及 `CONTRACT_MISMATCH`；局部标签页失败单独呈现。
- 候选写操作与下载状态：`draft / user-confirmation-required`；无授权、无预检或真实接口未确认时显示 `feature-unavailable`，不得生成下载地址或声称复核已提交。

## 页面状态

按页面需要覆盖 loading、refreshing、empty、filtered-empty、partial-error、fatal-error、forbidden、not-found/gone、conflict、rate-limited、offline、contract-mismatch、unknown-enum 和 feature-unavailable。

这些是前端显示状态，不是后端内部状态机。

## 验收

- 页面布局、主要操作入口和返回路径可用。
- 1440、1024、768、390 四档核心内容可访问。
- Route、Query Codec、API Schema、Adapter、Query Key 和 Mock 可测试。
- 主要正常与失败场景有前端自动化证据。
- 未确认业务不显示为生产可用。
