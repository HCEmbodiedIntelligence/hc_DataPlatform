# P03 上传任务前端开发计划

> 范围：前端页面与前端 API 需求。
> 后端业务、数据模型和实现尚未确认。
> 本页所有 API、状态和操作均为前端原型草案。

## 页面身份

- 页面 ID：P03
- 页面类型：列表/操作
- 路由：
- `/ingest/uploads`
- 设计与验收基线：`../../docs/FRONTEND-UI-BASELINE.md`

## 前端区域

页面头、状态筛选、任务表、分页、创建入口。

## 前端交互

筛选、打开任务、选择文件、展示暂停/取消等候选操作。所有跨页跳转使用稳定 ID 和目标页 route builder。

## API 需求

- 合同状态：列表为 `Mock-backed draft`；消费页：P03；创建/暂停/取消均为候选能力。
- `GET /projects/{projectId}/regions/{regionCode}/upload-sessions`：请求 `q, status, sourceId, sort, after | before, limit`；响应 `items, pageInfo, snapshotAt, scope, requestId`。`GET .../upload-sessions:creation-options` 请求所选数据源，响应可选目标与前端创建约束。
- `POST .../upload-sessions`：请求所选文件的相对路径、大小、摘要及目标标识，带 `Idempotency-Key`；响应资源与一次性交接信封。交接材料不得进入 Query Cache、日志或持久化状态。
- `POST .../upload-sessions/{uploadId}:pause|:resume|:cancel`：请求动作确认字段，带 `Idempotency-Key` 与可用时的 `If-Match`；响应更新资源或接受任务。
- 分页使用单向不透明游标；切换筛选后清空游标。错误覆盖 400、403、404/410、409/412、413、429、离线与合同不匹配。
- 候选写操作状态：`draft / user-confirmation-required`；未确认时创建、暂停和取消仅走 MSW 或显示 `feature-unavailable`，不生成假的上传成功态。

## 页面状态

按页面需要覆盖 loading、refreshing、empty、filtered-empty、partial-error、fatal-error、forbidden、not-found/gone、conflict、rate-limited、offline、contract-mismatch、unknown-enum 和 feature-unavailable。

这些是前端显示状态，不是后端内部状态机。

## 验收

- 页面布局、主要操作入口和返回路径可用。
- 1440、1024、768、390 四档核心内容可访问。
- Route、Query Codec、API Schema、Adapter、Query Key 和 Mock 可测试。
- 主要正常与失败场景有前端自动化证据。
- 未确认业务不显示为生产可用。
