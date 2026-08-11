# 前端脚手架交接说明

## 环境与命令

要求 Node.js 22 LTS 与 pnpm 10。首次安装：

```bash
cd frontend
corepack enable
pnpm install
```

CI 可使用 `pnpm install --frozen-lockfile`。首次运行 E2E 前执行 `pnpm exec playwright install chromium`。

常用命令：`pnpm dev`、`pnpm typecheck`、`pnpm test`、`pnpm e2e`、`pnpm gen:api`。

<!-- generated-api-status:start -->
### OpenAPI 生成状态

已生成：

- `ingest.ts`
- `datasets.ts`
- `cleaning.ts`
- `storage.ts`
- `robotics.ts`
- `access.ts`
- `platform.ts`
- `annotation.ts`

失败域：

- 无
<!-- generated-api-status:end -->

八个域级输入合计覆盖 223 个 operation；生成器不会读取会触发解析失败的合并文件。

## 公共模块

公共入口及核心导出如下：

- `src/shared/api`：`request<T>(opts)`、`DomainError` / `isDomainError`、`makeQueryKey`、`normalizeFilters`、`parseWire`、`makePageSchema` 与作用域 transport/resource 清理注册。
- `src/shared/auth`：`useCapabilities()`、`useAllowedActions(actions)`；未知能力、快照失败和 scope 不匹配均 fail closed。
- `src/shared/jobs`：`useAsyncJob(jobId)`、`jobQueryKey(jobId)`、`GlobalJobCenter`、任务中心 store；查询与 SSE 按 `resourceVersion` 去重。
- `src/shared/routing`：`registerPageRoutes(pageId, routes)`、`defineQueryCodec(cfg)`、`safeReturnTo(raw)`。
- `src/shared/telemetry`：`track(event)` 与 `configureTelemetrySink`；敏感字段在 sink 之前脱敏。
- `src/shared/lib`：int64 十进制品牌字符串、安全 bigint 运算、半开时间范围、casing 转换与只读 `UNKNOWN` 枚举投影。
- `src/shared/ui`：`PageHeader`、`StandardTable`、`FilterBar`、`CursorPager`、`StatusBadge`、`EmptyState`、`SkeletonBlock`、`ErrorPanel`、`ForbiddenPanel`、`ConfirmDialog`、`SideDrawer`、`DetailTabs`、`MetricCard`、`RelativeTime`、`CopyableId`。
- `src/entities`：公共 `Scope`、`ActorSummary`、76 项 canonical capability、34/19 两个非管理员角色 ceiling、142 项 canonical audit event 与 `AsyncJob` 类型。

## Mock handler 与场景注册

新增域 handler 只创建 `src/mocks/handlers/<domain>.handlers.ts`，默认导出 MSW handler 数组；`handlers/index.ts` 会通过 `import.meta.glob('./*.handlers.ts', { eager: true })` 自动聚合，不修改公共文件：

```ts
import { http, HttpResponse } from 'msw';

export default [
  http.get('/api/v1/example', () => HttpResponse.json({ items: [] })),
];
```

场景在域初始化文件中注册：

```ts
import { registerScenario, setScenario } from '../../mocks/scenarios/registry';

registerScenario('datasets', 'happy', () => {
  // seed deterministic domain state; may return a cleanup callback
});

await setScenario('datasets', 'happy');
```

浏览器可用 `?mockScenario=<domain>:<name>`，例如 `?mockScenario=datasets:happy`；测试可直接 `await setScenario(...)`。业务组件不得读取 `VITE_MOCK_MODE` 或 `mockScenario`。

`REQUIRED_SCENARIOS` 导出全站基线场景名（并包含新版 Fixture 合同增加的 `gone` 与 `scope-switch-race`），供各域注册器和合同测试复用。

公共 fixtures 位于 `src/mocks/fixtures/common/`，基准时间固定为 `2026-08-05T08:00:00Z`，包含 scope、三身份、三角色授权快照、审计骨架和 AsyncJob 全状态。

## 新增页面最小文件集

页面终端只需创建：

```text
src/pages/pXX-<page-name>/routes.tsx
src/pages/pXX-<page-name>/index.tsx
```

`routes.tsx` 默认导出或命名导出 `RouteObject[]` 均会被 `src/app/router/index.tsx` 自动发现；缺页会跳过。P08/P11/P14/P16 的路由记录必须使用 `lazy` 并在页面文件内动态 import，确保各自成为独立 chunk。新增页面不要修改公共 Router、NavigationManifest、Mock handler index 或场景 registry。

## 验证

共享层合同测试位于 `tests/contracts/shared-*.spec.ts(x)`，覆盖 scope Query Key、筛选规范化、错误映射、wire 校验、分页、returnTo、Query Codec、AsyncJob 版本去重、HTTP Header/casing、权限 fail-closed 与遥测脱敏。
