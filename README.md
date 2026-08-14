# HC Data Platform Frontend

面向具身智能数据治理场景的 Web 前端，覆盖数据接入、数据资产、标注与清洗、存储治理、机器人资产和系统管理等工作流。

当前仓库交付的是 **P01–P19 前端实现及其前端工程能力**：19 个页面、21 条活动路由、浏览器 Mock、运行时数据校验和 API 适配层。仓库不包含真实后端、数据库、异步 Worker 或部署基础设施；Mock 可用不等于真实接口已经联调完成。

## 功能范围

| 模块 | 页面与路由 | 当前前端能力 |
| --- | --- | --- |
| 数据工作台 | P01 `/dashboard` | 活动趋势、数据快照、覆盖率和待办聚合；仅在浏览器 Mock 下开放，关闭 Mock 后会安全降级且不请求未确认的聚合接口 |
| 数据接入 | P02–P04 `/ingest/sources`、`/ingest/uploads`、`/ingest/uploads/:uploadId` | 数据源管理、上传任务、对象校验、隔离区、异步任务状态与上传详情 |
| 数据资产 | P05–P07 `/datasets`、数据集详情、版本详情、Episode Viewer | 数据集筛选与游标分页、版本/Episode/Schema/容量查看、只读 Viewer、版本 Review |
| 数据标注 | P08 `/annotations`、`/annotations/tasks/:taskId` | 任务队列、Schema 驱动表单、草稿、提交、复核、过期任务 Rebase 与 Viewer 公共内核 |
| 手动清洗 | P09–P11 `/manual/issues`、`/manual/drafts`、`/manual/drafts/:draftId` | 问题分诊、清洗草稿、EDL 编辑、Preview、Commit 和 Review Finding 定位 |
| 存储管理 | P12–P13 `/storage/overview`、`/storage/lifecycle` | 容量/对象/Multipart/费用只读分析，以及生命周期策略、模拟、执行和恢复任务表现 |
| 系统管理 | P14–P19 `/settings/*` | 机器人模型、机器人与组件、标定、数据 Schema、用户权限和审计日志 |

路由会按 capability 隐藏或拦截未授权页面；组织、项目和区域组成前端数据作用域。未知枚举、合同不匹配、权限不足和危险写操作均采用 fail-closed 表现。

## 技术栈

- React 19、TypeScript 5（strict）、Vite 7、React Router 7
- TanStack Query / Table、Ant Design 6、Zustand、React Hook Form、Zod
- ECharts、Three.js、URDF Loader
- MSW 2 与 OpenAPI TypeScript

依赖分为运行时依赖和开发依赖。构建、类型检查、Mock 与 API 类型生成工具只参与开发流程；执行 `pnpm build` 后，部署目标是 `frontend/dist/` 中的静态文件。

## 本地运行

环境要求：Node.js `22.x`、pnpm `10.x`。仓库锁定的包管理器版本为 pnpm `10.14.0`。

这是 Node.js 前端项目，不需要 Python 的 `requirements.txt`。`frontend/package.json` 是直接依赖清单，`frontend/pnpm-lock.yaml` 则锁定直接和传递依赖的精确版本；两者已经组成可复现的安装入口。在仓库根目录、已经启用 Corepack 的环境中可直接执行：

```bash
pnpm --dir frontend install --frozen-lockfile
```

首次准备环境并启动浏览器 Mock：

```bash
cd frontend
corepack enable
pnpm install --frozen-lockfile
cp .env.example .env.local
VITE_MOCK_MODE=browser pnpm dev
```

打开 <http://localhost:5173>。`.env.example` 默认使用 `VITE_MOCK_MODE=off`，因此独立体验前端时需要像上面一样显式开启浏览器 Mock。

Mock 支持通过 URL 选择确定性场景，例如：

```text
http://localhost:5173/datasets?mockScenario=datasets:cursor-pagination
http://localhost:5173/ingest/uploads?mockScenario=ingest:happy
http://localhost:5173/settings/audit?mockScenario=audit:admin-view
```

场景和数据位于 `frontend/src/mocks/`。它们只用于前端开发、合同验证和 E2E，不代表生产数据或后端业务实现。

## 环境变量

| 变量 | 说明 |
| --- | --- |
| `VITE_API_BASE_URL` | HTTP API 基础地址，例如 `/api/v1` 或开发环境的 loopback URL |
| `VITE_SSE_BASE_URL` | SSE 事件基础地址 |
| `VITE_MOCK_MODE` | `browser` 启动 MSW；`off` 请求真实地址；`test` 仅供测试环境使用 |
| `VITE_BUILD_VERSION` | 写入请求头和运行时信息的前端构建版本 |
| `VITE_RELEASE_ENV` | `local`、`dev`、`test`、`staging` 或 `production` |

公开 URL 必须是应用根相对路径或 HTTPS 地址；仅 Vite 开发模式允许 loopback HTTP 地址。项目未配置 Vite API 代理：使用 `/api/v1` 且关闭 Mock 时，需要由同源网关提供转发；本地直连后端时应填写完整的 loopback API 地址。

## 连接真实后端

在后端地址和接口版本已经明确后，将本地配置改为类似：

```dotenv
VITE_API_BASE_URL=https://api.example.com/api/v1
VITE_SSE_BASE_URL=https://api.example.com/api/v1/events
VITE_MOCK_MODE=off
VITE_BUILD_VERSION=web-local
VITE_RELEASE_ENV=dev
```

前端请求层会携带客户端版本、当前组织/项目/区域作用域，以及存在时的会话令牌、幂等键和 ETag 前置条件。当前生成类型和 Mock 所依据的 OpenAPI 均是前端接口需求草案；接入真实后端后仍需单独验证认证、CORS、Scope、分页、错误格式、SSE 和写操作语义。

## 开发与构建

```bash
cd frontend
pnpm typecheck       # TypeScript 项目检查
pnpm build           # 类型检查并生成 production bundle
```

## API 类型生成

已生成的类型位于 `frontend/src/shared/api/generated/`，包含 ingest、datasets、cleaning、storage、robotics、access、platform 和 annotation 八个域。生成输入不随本仓库分发；需要持有对应外部 OpenAPI 草案目录：

```bash
cd frontend
OPENAPI_ROOT=/absolute/path/to/openapi-root pnpm gen:api
```

生成脚本会覆盖相应生成文件并更新 `frontend/docs/frontend-scaffold-notes.md` 中的生成状态。不要直接编辑带有 `AUTO-GENERATED` 标记的文件。

## 目录结构

```text
.
├── frontend/
│   ├── src/
│   │   ├── app/          # 启动配置、Provider、Router、Shell 与主题
│   │   ├── pages/        # P01–P19 页面终端与路由
│   │   ├── features/     # 按业务域组织的 API、状态和交互逻辑
│   │   ├── entities/     # 领域实体与运行时类型
│   │   ├── shared/       # HTTP、权限、Scope、任务、遥测和通用 UI
│   │   └── mocks/        # MSW handler、fixture 与场景注册
│   └── docs/             # 前端架构、状态与交接文档
├── docs/                 # UI 基线和重构记录
└── plan/                 # 当前前端实施计划与逐页计划
```

## 项目边界

- `frontend/src/shared/api/generated/` 的存在只表示前端类型已从外部草案生成，不表示接口已冻结或后端已实现。
- 浏览器 Mock 只用于前端开发，不等同于生产可用或真实接口联调完成。
- 本仓库没有后端业务、持久化、消息队列、对象存储服务或部署方案。
- 真实联调应在用户确认业务和接口合同、提供后端环境后单独记录和验收。

更多背景见 [前端实施计划](plan/FINAL-IMPLEMENTATION-PLAN.md)、[UI 基线](docs/FRONTEND-UI-BASELINE.md) 和 [前端脚手架说明](frontend/docs/frontend-scaffold-notes.md)。
