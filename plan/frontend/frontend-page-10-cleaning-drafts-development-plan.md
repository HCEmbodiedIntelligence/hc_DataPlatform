# P10 清洗草稿前端开发计划

> 范围：前端页面与前端 API 需求。
> 后端业务、数据模型和实现尚未确认。
> 本页所有 API、状态和操作均为前端原型草案。

## 页面身份

- 页面 ID：P10
- 页面类型：列表/详情
- 路由：
- `/manual/drafts`
- 设计与验收基线：`../../docs/FRONTEND-UI-BASELINE.md`

## 前端区域

页面头、范围筛选、草稿表、分页和 Inspector。

## 前端交互

筛选、打开固定草稿、进入 P11。所有跨页跳转使用稳定 ID 和目标页 route builder。

## API 需求

- 合同状态：只读合同为 `Mock-backed draft`；消费页：P10；本页不提交清洗写操作。
- `GET /projects/{projectId}/regions/{regionCode}/cleaning-drafts`：请求 `scope, status, q, datasetId, baseVersionId, episodeId, robotId, creatorId, updatedFrom, updatedTo, previewStatus, commitStatus, versionReviewStatus, findingType, findingSeverity, sort, after | before, limit`；响应 `items, pageInfo, snapshotAt, scope, requestId`。
- `GET .../cleaning-drafts:summary`：复用筛选但不带游标，响应计数与容量摘要。`GET .../cleaning-drafts/{draftId}/summary` 返回固定草稿、来源、当前 EDL/预览/提交摘要及允许动作。
- `GET .../cleaning-drafts/{draftId}/events`：请求 `limit=10`，响应真实事件项与请求标识；Inspector 不补写缺失事件。
- 分页只传一个不透明游标，稳定排序包含 `id`；错误覆盖 403、404/410、429、离线、局部失败、未知枚举与 `CONTRACT_MISMATCH`。
- 进入 P11 仅使用稳定 `draftId`。跨草稿批量操作为 `draft / user-confirmation-required`；当前无此合同，必须 `feature-unavailable`，不得以本地状态冒充完成。

## 页面状态

按页面需要覆盖 loading、refreshing、empty、filtered-empty、partial-error、fatal-error、forbidden、not-found/gone、conflict、rate-limited、offline、contract-mismatch、unknown-enum 和 feature-unavailable。

这些是前端显示状态，不是后端内部状态机。

## 验收

- 页面布局、主要操作入口和返回路径可用。
- 1440、1024、768、390 四档核心内容可访问。
- Route、Query Codec、API Schema、Adapter、Query Key 和 Mock 可测试。
- 主要正常与失败场景有前端自动化证据。
- 未确认业务不显示为生产可用。
