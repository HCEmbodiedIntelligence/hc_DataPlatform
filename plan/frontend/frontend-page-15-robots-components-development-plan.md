# P15 机器人与组件前端开发计划

> 范围：前端页面与前端 API 需求。
> 后端业务、数据模型和实现尚未确认。
> 本页所有 API、状态和操作均为前端原型草案。

## 页面身份

- 页面 ID：P15
- 页面类型：三栏管理
- 路由：
- `/settings/robots`
- 设计与验收基线：`../../docs/FRONTEND-UI-BASELINE.md`

## 前端区域

机器人列表、组件树、详情和关联入口。

## 前端交互

选择机器人/组件、键盘浏览树、跳转 P16/P17。所有跨页跳转使用稳定 ID 和目标页 route builder。

## API 需求

- 合同状态：只读合同为 `Mock-backed draft`；消费页：P15；机器人与组件编辑当前无可用合同。
- `GET /projects/{projectId}/regions/{regionCode}/robots`：请求 `q, lifecycle, connectivity, sort, after | before, limit`；响应 `items, pageInfo, snapshotAt, scope, requestId`。
- `GET .../robots/{robotId}/bootstrap`：响应固定机器人、有效模型绑定、`etag, topologyRevision, allowedActions, blockedReasons`。`GET .../robots/{robotId}/components` 返回 `items, pageInfo, snapshotAt, topologyRevision`，父子关系仅来自响应。
- `GET .../components/{componentId}/frames` 与 `GET .../components/{componentId}/channels`：响应可分页引用项、`scope, snapshotAt, requestId`；跳转 P16/P17 只传稳定 Calibration/Schema 标识。
- 每个列表仅传一个不透明游标，拓扑数据必须共享 `topologyRevision`。错误覆盖 403、404/410、429、离线、局部树失败、未知枚举与 `CONTRACT_MISMATCH`。
- 新建/编辑机器人、绑定模型及修改组件树为 `draft / user-confirmation-required`；P15 当前仅只读，按钮必须 `feature-unavailable`，不得把本地树改动显示为已保存。

## 页面状态

按页面需要覆盖 loading、refreshing、empty、filtered-empty、partial-error、fatal-error、forbidden、not-found/gone、conflict、rate-limited、offline、contract-mismatch、unknown-enum 和 feature-unavailable。

这些是前端显示状态，不是后端内部状态机。

## 验收

- 页面布局、主要操作入口和返回路径可用。
- 1440、1024、768、390 四档核心内容可访问。
- Route、Query Codec、API Schema、Adapter、Query Key 和 Mock 可测试。
- 主要正常与失败场景有前端自动化证据。
- 未确认业务不显示为生产可用。
