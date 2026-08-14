# P13 存储生命周期前端开发计划

> 范围：前端页面与前端 API 需求。
> 后端业务、数据模型和实现尚未确认。
> 本页所有 API、状态和操作均为前端原型草案。

## 页面身份

- 页面 ID：P13
- 页面类型：管理/分析
- 路由：
- `/storage/lifecycle`
- 设计与验收基线：`../../docs/FRONTEND-UI-BASELINE.md`

## 前端区域

页面头、指标、策略列表、模拟结果和操作提示。

## 前端交互

选择策略、展示模拟和执行候选入口。所有跨页跳转使用稳定 ID 和目标页 route builder。

## API 需求

- 合同状态：读取为 `Mock-backed draft`；消费页：P13；模拟、启用策略与恢复任务均为候选能力。
- `GET /projects/{projectId}/storage/lifecycle-page`：请求 `regionCode`；响应 `snapshot, policySetVersion, policyVersions, simulationInputHash, policies, impact, capabilities, scope, requestId`，策略含 `etag, allowedActions, blockedReasons`。
- `POST .../storage/lifecycle-simulations`：请求 `regionCode`、保存策略或草稿策略输入及 `Idempotency-Key`；响应 Simulation 与 `jobId, status, inputHash, impactDigest, snapshotId, protected, unknownSummary, requestId`。`GET .../lifecycle-simulations/{simulationId}` 只轮询已返回的 ID。
- `POST .../storage/lifecycle-policies/{policyId}:enable`：请求输入哈希/模拟证据与确认字段，带 `If-Match, Idempotency-Key`；响应更新结果。`POST .../storage/restore-tasks` 请求对象、恢复方式及幂等键，响应接受任务。
- 本页无列表游标；策略集、快照和模拟哈希必须相互匹配。错误覆盖 400、403、404/410、409/412、422、429、离线、模拟过期与 `CONTRACT_MISMATCH`。
- 三类写操作状态均为 `draft / user-confirmation-required`；无能力、无有效模拟或真实接口未确认时显示 `feature-unavailable`，不得合成节省金额或执行成功。

## 页面状态

按页面需要覆盖 loading、refreshing、empty、filtered-empty、partial-error、fatal-error、forbidden、not-found/gone、conflict、rate-limited、offline、contract-mismatch、unknown-enum 和 feature-unavailable。

这些是前端显示状态，不是后端内部状态机。

## 验收

- 页面布局、主要操作入口和返回路径可用。
- 1440、1024、768、390 四档核心内容可访问。
- Route、Query Codec、API Schema、Adapter、Query Key 和 Mock 可测试。
- 主要正常与失败场景有前端自动化证据。
- 未确认业务不显示为生产可用。
