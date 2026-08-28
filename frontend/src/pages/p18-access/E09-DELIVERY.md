# E09 / P18 交付记录

## 实现边界

- 页面包含平台“用户管理”、项目“加入申请”和“权限申请”三个正式 API 标签。自助注册账户直接成为 ACTIVE 的空权限主体，相关说明只出现在用户管理上下文。
- 用户管理消费 `/platform/accounts` 及其状态、角色、重置密码和删除动作；项目审批继续消费当前 Shell scope 下的 membership request 与 capability request 接口。
- 平台用户管理只接受 `platform.account.read/manage`（或全局平台管理员能力）；项目 `project.access.manage` / `access.manage` 不会扩大为全平台账号权限。
- 项目申请页要求当前项目作用域及 `access.read`。只有平台账号权限且没有项目作用域时，路由强制落在“用户管理”。
- 当前项目由 Shell scope 提供；列表或决策响应若出现跨项目 identity，前端以 `CONTRACT_MISMATCH` fail closed。
- 管理员可批准/拒绝 PENDING、撤销 APPROVED；申请人可撤回自己的 PENDING。服务端始终是最终授权边界。
- membership 批准只建立项目成员关系；撤销 membership 会停用成员关系及该项目相关 capability 授权。capability 批准直接授予申请中的 keys，撤销只停用该申请来源的授权。
- real-api 错误保留 Problem Details 文案、problem code、request ID 和 403/409/429 恢复入口，不回退 Browser Mock。

## 页面信息架构

- 审批标签初始只显示充分利用主内容宽度的紧凑申请列表；不自动选择第一项，Drawer 默认为关闭。
- 列表只保留申请人、申请说明或权限摘要、状态、提交时间和查看操作。项目由 Shell scope 表达，技术 ID 不进入主列表。
- 用户点击“查看”后打开唯一的 Ant Design 模态 Drawer；标签、筛选、搜索、分页和 scope 变化会关闭详情并清理 request ID，合法深链仍可恢复指定申请。
- Drawer 采用渐进披露：核心信息、capability keys、已处理记录和折叠技术信息；只有识别到真实高影响 capability 时才展示紧凑影响提示。
- 决策默认不预选，只渲染状态机实际允许的动作。拒绝、撤销和撤回要求 UI 原因；membership 撤销另有级联影响确认。
- 1440、1280 的初始列表无占位列；1024 以下 Drawer 不扩张页面宽度；375×812 下筛选换行、列表转为可读行卡、Drawer 全宽且 footer 不覆盖 body。

## 验收产物

- `artifacts/visual/e01-e10/E09/1440x900.png`、`1280x800.png`：初始全宽列表。
- `artifacts/visual/e01-e10/E09/1440x900-drawer.png`、`1280x800-drawer.png`：用户点击后的唯一 Drawer。
- `artifacts/visual/e01-e10/E09/1280x800-403.png`。
- `artifacts/visual/e01-e10/E09/users-1440x900.png`、`users-375x812.png`。
- `artifacts/visual/e01-e10/FE11-repair/375x812-list.png`、`375x812-light-approval.png`。
- axe 报告由视觉测试写入同目录，并要求 0 violations。

## 验证命令

- `pnpm exec vitest run src/pages/p18-access/query-codec.test.ts src/pages/p18-access/AccessApprovalView.test.tsx src/pages/p18-access/page.test.tsx src/pages/p18-access/UserManagementPanel.test.tsx`
- `pnpm typecheck`
- `pnpm build`
- `pnpm exec playwright test -c src/pages/p18-access/visual/playwright.config.ts`

## 2026-08-25 验证结果

- P18 Vitest：4 个文件、41 个测试全部通过。
- P18 Playwright：6 个场景全部通过；1440、1280、375 均无水平溢出，axe 报告均为 0 violations。视觉测试同时以 `prefers-reduced-motion: reduce` 验证减弱动效路径。
- `pnpm typecheck`：通过。
- `pnpm build`：通过；Vite 仅报告仓库既有的大于 500 kB chunk 提示。
