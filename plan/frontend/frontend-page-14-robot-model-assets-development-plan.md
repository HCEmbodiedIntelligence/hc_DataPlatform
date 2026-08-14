# P14 机器人模型资产前端开发计划

> 范围：前端页面与前端 API 需求。
> 后端业务、数据模型和实现尚未确认。
> 本页所有 API、状态和操作均为前端原型草案。

## 页面身份

- 页面 ID：P14
- 页面类型：管理
- 路由：
- `/settings/robot-models`
- 设计与验收基线：`../../docs/FRONTEND-UI-BASELINE.md`

## 前端区域

页面头、模型表、版本详情、文件选择和预览区。

## 前端交互

搜索、打开版本、选择本地文件、展示发布候选入口。所有跨页跳转使用稳定 ID 和目标页 route builder。

## API 需求

- 合同状态：读取为 `Mock-backed draft`；消费页：P14；资产上传与发布为候选能力。
- `GET /organizations/{organizationId}/robot-models`：请求 `q, manufacturer, sort, after | before, limit`；响应 `items, pageInfo, snapshotAt, scope, requestId`。`GET .../robot-model-versions/{versionId}` 返回固定版本、资产/校验状态、哈希、`etag, allowedActions, blockedReasons`。
- `POST .../robot-model-versions/{versionId}/upload-sessions`：请求文件 `relative_path, size_bytes, sha256` 与 `Idempotency-Key`；响应一次性上传交接信封，交接材料不得进入缓存、日志或持久化状态。
- `POST .../robot-model-versions/{versionId}:preflight-publish`：请求期望哈希/ETag、校验报告和变更说明；响应 `allowed, preflightToken, expiresAt, impacts, warnings, blockers, requestId`。`POST .../robot-model-versions/{versionId}:publish` 仅提交有效 token，带 `If-Match, Idempotency-Key`。
- 分页使用不透明游标；错误覆盖 400、403、404/410、409/412、413、422、429、离线、校验陈旧与 `CONTRACT_MISMATCH`。
- 上传、预检和发布状态为 `draft / user-confirmation-required`；未确认时本地文件只用于前端预览，操作显示 `feature-unavailable`，不得声称资产已上传或发布。

## 页面状态

按页面需要覆盖 loading、refreshing、empty、filtered-empty、partial-error、fatal-error、forbidden、not-found/gone、conflict、rate-limited、offline、contract-mismatch、unknown-enum 和 feature-unavailable。

这些是前端显示状态，不是后端内部状态机。

## 验收

- 页面布局、主要操作入口和返回路径可用。
- 1440、1024、768、390 四档核心内容可访问。
- Route、Query Codec、API Schema、Adapter、Query Key 和 Mock 可测试。
- 主要正常与失败场景有前端自动化证据。
- 未确认业务不显示为生产可用。
