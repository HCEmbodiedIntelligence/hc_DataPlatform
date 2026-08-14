# P18 用户权限前端开发计划

> 范围：前端页面与前端 API 需求。
> 后端业务、数据模型和实现尚未确认。
> 本页所有 API、状态和操作均为前端原型草案。

## 页面身份

- 页面 ID：P18
- 页面类型：管理
- 路由：
- `/settings/access`
- 设计与验收基线：`../../docs/FRONTEND-UI-BASELINE.md`

## 前端区域

成员、角色、策略、详情和 capability 展示。

## 前端交互

筛选成员、查看权限原型、未确认写操作保持不可用。所有跨页跳转使用稳定 ID 和目标页 route builder。

## API 需求

- 合同状态：读取为 `Mock-backed draft`；消费页：P18；任何权限写入均为候选能力并默认不可用。
- `GET /projects/{projectId}/access/bootstrap`：响应 `roleVersion, capabilityCatalogVersion, policyRevision, projectPolicyEtag, summary, allowedActions, blockedReasons, scope, requestId`。角色 capability 对照来自前端固定目录版本，不从成员记录推测。
- `GET /projects/{projectId}/members`：请求 `q, status, roleId, sort, after | before, limit`；响应 `items, pageInfo, snapshotAt, scope, requestId`，成员含 principal、状态、角色版本、`etag, allowedActions, blockedReasons`。
- 候选 `POST /projects/{projectId}/access-changes`：仅可请求已确认预检产生的 `preflight_token`，带项目策略 `If-Match` 与 `Idempotency-Key`；响应新策略 revision、受影响范围及 `requestId`，成功后前端必须重新评估授权。
- 分页使用单个不透明游标；错误覆盖 400、401/403、404/410、409/412、422、429、离线、角色目录版本漂移与 `CONTRACT_MISMATCH`。
- 邀请、改角色、禁用成员及其预检整体为 `draft / user-confirmation-required`；当前无已确认端到端写合同，所有写入口必须 `feature-unavailable`，不得仅凭 Mock 宣称权限生效。

## 页面状态

按页面需要覆盖 loading、refreshing、empty、filtered-empty、partial-error、fatal-error、forbidden、not-found/gone、conflict、rate-limited、offline、contract-mismatch、unknown-enum 和 feature-unavailable。

这些是前端显示状态，不是后端内部状态机。

## 验收

- 页面布局、主要操作入口和返回路径可用。
- 1440、1024、768、390 四档核心内容可访问。
- Route、Query Codec、API Schema、Adapter、Query Key 和 Mock 可测试。
- 主要正常与失败场景有前端自动化证据。
- 未确认业务不显示为生产可用。
