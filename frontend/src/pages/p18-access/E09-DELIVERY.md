# E09 / P18 交付记录

## 实现边界

- 运行时只审批“项目加入申请”和“权限申请”；用户注册不进入审批队列。
- 页面只消费 `shared/api/generated/platform.ts` 的正式 runtime 类型与 `/projects/{project_id}/membership-requests`、`capability-requests` 动作端点。
- 当前项目由 Shell scope 提供；列表和决策响应若出现跨项目 identity，前端以 `CONTRACT_MISMATCH` fail closed。
- 内部与外包身份复用同一视图；可见数据由项目 scope 决定，批准/拒绝/撤销由 `project.access.manage`（兼容现有 Shell 的 `access.manage`）决定，申请人可撤回自己的待处理申请。
- real-api 错误保留 RFC 9457 文案、problem code、request ID 和 403/409/429 状态，不回退 Browser Mock。

## 与 E09-v4 效果图的有意差异

- 效果图中的权限模板、region/资源谓词、到期时间、风险评级和再认证输入不在当前 runtime OpenAPI；页面明确显示合同未开放，不把示例数据伪装成服务端事实，也不采集密码或动态码。
- “用户 / 项目成员 / 权限模板”保留为功能 Tab 以还原信息架构，但展示 `feature-unavailable`；只有两类正式申请具有列表和动作。
- 正式列表响应只有 `items`，没有 cursor 或筛选参数；当前搜索、状态筛选、排序和 10/20 条分页作用于服务端已返回的当前项目集合，并在列表底部明确标注。
- 抽屉按最新效果图锚定页面右上方；1440 下保留“列表 + 详情 + 抽屉”，1280 下仍保留三栏密度且无横向页面滚动，1023 以下变为非模态右侧覆盖层。

## 验收产物

- `artifacts/visual/e01-e10/E09/1440x900.png`
- `artifacts/visual/e01-e10/E09/1280x800.png`
- `artifacts/visual/e01-e10/E09/1280x800-403.png`
- `artifacts/visual/e01-e10/E09/axe-1440x900.json`：0 violations
- `artifacts/visual/e01-e10/E09/axe-1280x800.json`：0 violations

## 验证结果

- `pnpm --dir frontend typecheck`：PASS。
- P18 Vitest：3 files / 23 tests PASS；覆盖跨项目 fail-closed、批准/拒绝/撤销/撤回、成功后重复提交保护、再认证 403、409/429、筛选分页与键盘焦点。
- E09 Playwright：1440×900、1280×800、403 三场景 PASS；覆盖比例、首屏抽屉、无横向溢出和 Escape 焦点恢复。
- runtime OpenAPI `--runtime --check` 与前端生成类型 `gen:api --check`：PASS。
- 后端访问合同定向测试：15 PASS；覆盖跨项目授权、重复/冲突审批、批准/拒绝/撤回/撤销与公共端 429。已复核 BE24 real-network 交付记录的 API/Worker 9/9 PASS。
