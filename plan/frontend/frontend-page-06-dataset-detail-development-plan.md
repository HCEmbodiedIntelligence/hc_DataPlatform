# P06 数据集详情前端开发计划

> 范围：前端页面与前端 API 需求。
> 后端业务、数据模型和实现尚未确认。
> 本页所有 API、状态和操作均为前端原型草案。

## 页面身份

- 页面 ID：P06
- 页面类型：详情/只读 Viewer
- 路由：
- `/datasets/:datasetId`
- `/datasets/:datasetId/versions/:versionId/episodes/:episodeId/view`
- 设计与验收基线：`../../docs/FRONTEND-UI-BASELINE.md`

## 前端区域

资源头、概要、版本、Episode、Schema、来源和容量区域。

## 前端交互

切换区域、打开固定版本、进入只读 Viewer。所有跨页跳转使用稳定 ID 和目标页 route builder。

## API 需求

- 合同状态：只读合同为 `Mock-backed draft`；消费页：P06 与 P06 Viewer；不得据此推断媒体下载能力。
- `GET /projects/{projectId}/datasets/{datasetId}/bootstrap`：响应 Dataset、固定当前版本、摘要、能力、`scope, requestId`。`GET .../{datasetId}/versions` 请求 `q, versionKind, versionStatus, sort, after | before, limit`，响应版本页。
- `GET .../{datasetId}/versions/{versionId}/episodes`：请求 `snapshotToken`、筛选、稳定排序与游标；响应真实 `items, pageInfo, snapshotAt, requestId`，稀疏窗口不得由前端补行。
- `GET .../{versionId}/schema-summary|source-provenance|capacity-facts`：响应 Schema 摘要、可分页来源及容量事实；每项必须回证 Path 中的 Dataset/Version 身份。
- Viewer 仅复用 Version Bootstrap 与 Episode 列表解析固定 `episodeId`；没有可验证媒体描述符时展示安全占位。错误覆盖 403、404/410、429、离线、局部失败与 `CONTRACT_MISMATCH`。
- 媒体播放、签名下载与补齐缺失 Episode 均为 `draft / user-confirmation-required`；当前 Mock 未提供时必须 `feature-unavailable`，不得构造 URL 或伪造媒体。

## 页面状态

按页面需要覆盖 loading、refreshing、empty、filtered-empty、partial-error、fatal-error、forbidden、not-found/gone、conflict、rate-limited、offline、contract-mismatch、unknown-enum 和 feature-unavailable。

这些是前端显示状态，不是后端内部状态机。

## 验收

- 页面布局、主要操作入口和返回路径可用。
- 1440、1024、768、390 四档核心内容可访问。
- Route、Query Codec、API Schema、Adapter、Query Key 和 Mock 可测试。
- 主要正常与失败场景有前端自动化证据。
- 未确认业务不显示为生产可用。
