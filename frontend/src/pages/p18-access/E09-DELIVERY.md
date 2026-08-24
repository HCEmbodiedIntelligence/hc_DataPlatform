# E09 / P18 交付记录

## 实现边界

- 页面包含平台“用户管理”、项目“加入申请”和“权限申请”三个已接入正式 API 的标签；用户注册仍不进入审批队列。
- 用户管理消费 `/platform/accounts` 及其状态、角色、重置密码和删除动作；项目审批继续消费 `/organizations/{organization_id}/projects/{project_id}/membership-requests`、`capability-requests`。
- 平台用户管理接受全局 `platform.admin`（含全部平台权限）或细分的
  `platform.account.read/manage`；项目 `project.access.manage` / `access.manage` 不会扩大为
  全平台用户权限。
- 当前项目由 Shell scope 提供；列表和决策响应若出现跨项目 identity，前端以 `CONTRACT_MISMATCH` fail closed。
- 内部与外包身份复用同一视图；可见数据由项目 scope 决定，批准/拒绝/撤销由 `project.access.manage`（兼容现有 Shell 的 `access.manage`）决定，申请人可撤回自己的待处理申请。
- real-api 错误保留 RFC 9457 文案、problem code、request ID 和 403/409/429 状态，不回退 Browser Mock。

## 与 E09-v4 效果图的有意差异

- 效果图中的权限模板、region/资源谓词、到期时间、风险评级和再认证输入不在当前 runtime OpenAPI；页面明确显示合同未开放，不把示例数据伪装成服务端事实，也不采集密码或动态码。
- “用户管理”已提供服务端分页、搜索、状态/角色筛选、创建、禁用/恢复、平台管理员升降级、管理员重置密码和软删除；项目成员仍由加入申请状态表达，权限模板继续等待独立合同。
- 用户表只显示脱敏恢复邮箱和活动会话计数，不显示密码、密码哈希、令牌或完整邮箱；删除需要再次输入用户名。
- 正式列表响应只有 `items`，没有 cursor 或筛选参数；当前搜索、状态筛选、排序和 10/20 条分页作用于服务端已返回的当前项目集合，并在列表底部明确标注。
- 抽屉按最新效果图锚定页面右上方；1440 下保留“列表 + 详情 + 抽屉”，1280 下仍保留三栏密度且无横向页面滚动，1023 以下变为非模态右侧覆盖层。

## 验收产物

- `artifacts/visual/e01-e10/E09/1440x900.png`
- `artifacts/visual/e01-e10/E09/1280x800.png`
- `artifacts/visual/e01-e10/E09/1280x800-403.png`
- `artifacts/visual/e01-e10/E09/users-1440x900.png`
- `artifacts/visual/e01-e10/E09/users-375x812.png`
- `artifacts/visual/e01-e10/E09/axe-1440x900.json`：0 violations
- `artifacts/visual/e01-e10/E09/axe-1280x800.json`：0 violations
- `artifacts/visual/e01-e10/E09/axe-users-1440x900.json`：0 violations

## 验证结果

- `pnpm typecheck` 与 `pnpm build`：PASS。
- P18、认证 scope 与导航相关 Vitest：9 files / 64 tests PASS；同时覆盖全局账号接口的 session scope、`If-Match`、不提交原密码、软删除二次确认、弹窗内错误恢复，以及既有项目审批行为。
- E09 Playwright：6 scenarios PASS；覆盖用户管理的 1440×900 与 375×812、审批页 1440×900/1280×800、403、成功反馈、无页面横向溢出和 Escape 焦点恢复。
- runtime OpenAPI `--check` 与前端生成类型：PASS。
- 后端认证、迁移、会话、恢复与用户管理定向测试：76 PASS；覆盖平台/项目权限隔离、创建/启停/升降级/重置/软删除、会话撤销、自操作和最后一个平台管理员保护。
