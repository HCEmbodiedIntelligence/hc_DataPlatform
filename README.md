# HC Data Platform

面向具身智能数据治理场景的 Web 前端，覆盖数据接入、数据资产、标注与清洗、存储治理、机器人资产和系统管理等工作流。

当前仓库统一交付 **P01–P19 前端、FastAPI 后端、Temporal Worker、数据库迁移和平台部署资产**。浏览器 Mock 仍只用于本地开发；生产发布由根目录统一 Helm Chart 绑定 Frontend、API 和 Worker 的不可变镜像 digest。

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

### Docker 一键开发（推荐）

环境要求：Docker Engine 和 Docker Compose v2。无需在宿主机安装 Node.js、pnpm、Python 或 uv。

首次在仓库根目录执行下面一条命令，即可构建开发镜像并在后台启动 Frontend、API、Worker 及其 PostgreSQL、MinIO、Temporal 等依赖：

```bash
docker compose -f compose.dev.yaml up --build -d
```

首次构建完成后，日常启动不需要再次构建或拉取基础镜像，直接执行：

```bash
docker compose -f compose.dev.yaml up -d
```

Docker 会复用本机的基础镜像层和 BuildKit 依赖缓存；基础镜像已经固定 digest，不会因为远端 tag 更新而重复下载。除非清理过 Docker 缓存、主动使用 `--pull`，或修改了 Dockerfile/依赖锁文件，否则无需重新下载或构建。

需要重新构建、但不希望主动拉取基础镜像时，可明确使用本机已有镜像和缓存：

```bash
docker compose -f compose.dev.yaml build --pull=false
docker compose -f compose.dev.yaml up -d --no-build
```

构建输出中的 `load metadata` 是解析镜像清单，不代表重新下载镜像层；复用成功的步骤会显示 `CACHED`。如果需要把完整开发环境搬到离线机器，可在联网机器完成一次构建和启动后导出 Compose 使用的全部镜像：

```bash
# 联网机器：导出 Frontend、API、Worker、迁移和基础设施镜像
docker compose -f compose.dev.yaml config --images \
  | xargs docker image save -o hc-data-platform-dev-images.tar

# 离线机器：导入后直接启动，不构建、不拉取
docker image load -i hc-data-platform-dev-images.tar
docker compose -f compose.dev.yaml up -d --no-build --pull never
```

完全离线重新安装前端或 Python 依赖还要求相应 pnpm/uv 包已存在于 BuildKit 缓存或内部包仓库；纯源码修改不触发依赖安装，也不需要重新构建。

启动后可访问：

- 完整应用（经本地网关）：<http://localhost:8088>
- Vite 前端开发服务：<http://localhost:5174>
- FastAPI Swagger：<http://localhost:8000/docs>
- Temporal UI：<http://localhost:8080>
- MinIO Console：<http://localhost:9001>

容器内的 Vite 固定监听 5173，默认映射到宿主机 5174，以避免和宿主机常见的 Vite 5173 进程冲突。需要改端口时可在命令前指定，例如 `HC_FRONTEND_PORT=5180 docker compose -f compose.dev.yaml up -d`；完整应用的 8088 网关地址不变。

Compose 会生成并使用以下三个开发镜像：

| 服务 | 开发镜像 | 热更新方式 |
| --- | --- | --- |
| Frontend | `hc-data-platform-frontend:dev` | Vite HMR |
| API | `hc-data-platform-api:dev` | Uvicorn `--reload` |
| Worker | `hc-data-platform-worker:dev` | `watchfiles` 检测 Python 文件并重启 Worker |

开发 Compose 默认设置 `HC_FRONTEND_MOCK_MODE=browser`，因此尚未落地真实聚合合同的工作台也会展示完整 fixture 数据。需要让前端只访问当前真实 API、并对未实现能力保持降级时，可执行：

```bash
HC_FRONTEND_MOCK_MODE=off docker compose -f compose.dev.yaml up -d --no-build --no-deps --force-recreate frontend
```

源码已经通过 bind mount 放入容器，无需重新复制代码：

- `./frontend` → Frontend 容器的 `/app`；`node_modules` 使用由开发镜像自动填充的独立匿名 volume，避免被宿主机目录覆盖。容器启动时不会重复执行 `pnpm install`。
- `./backend` → API 和 Worker 容器的 `/app`；两个进程共享当前后端源码。

修改 `frontend/src` 后浏览器会热更新；修改 `backend/src` 后 API 会自动 reload，Worker 会自动重启。修改 `package.json`、`pnpm-lock.yaml`、`pyproject.toml`、`uv.lock` 或 Dockerfile 后，才需要执行 `docker compose -f compose.dev.yaml up --build -V -d`；其中 `-V` 会用新前端镜像中的依赖刷新匿名 `node_modules` volume。

常用管理命令：

```bash
# 查看三个开发服务日志
docker compose -f compose.dev.yaml logs -f frontend api worker

# 日常停止（保留容器和全部 volume，下次可直接 start/up）
docker compose -f compose.dev.yaml stop

# 停止并移除容器和网络（保留数据库、对象存储的 named volume）
docker compose -f compose.dev.yaml down
```

### Real API 模式与第一波门禁

显式 Real API 模式使用叠加文件启动；该模式固定 `VITE_MOCK_MODE=off`，并要求迁移状态检查成功后才启动 API 和 Worker：

```bash
docker compose -f compose.dev.yaml -f compose.real-api.yaml up --build -d
docker compose -f compose.dev.yaml -f compose.real-api.yaml ps
```

测试门禁使用独立的 Compose project、tmpfs PostgreSQL/MinIO、Temporal、正式 Worker 镜像和只读源码挂载，不复用开发数据。全新环境可直接运行；`migration`、`worker`、`integration`、`replay` 和 `security*` 每次都会先重置这个专用测试 project，再启动并检查所需依赖，因此这些命令不要并行执行：

```bash
python3 scripts/first_wave_gate.py static              # Compose/runtime OpenAPI/生成类型/typecheck/迁移静态合同
python3 scripts/first_wave_gate.py migration           # 全新数据库与第一波已存在数据库升级/checksum
python3 scripts/first_wave_gate.py worker              # 正式 Worker 启动、健康和 poller
python3 scripts/first_wave_gate.py integration         # PostgreSQL/MinIO 外部依赖
python3 scripts/first_wave_gate.py replay              # Temporal replay 与 Worker kill 恢复
python3 scripts/first_wave_gate.py security             # IDOR/Scope/幂等/恶意 Manifest，严格发布门禁
python3 scripts/first_wave_gate.py security-baseline    # 允许 XFAIL，仅用于记录基线
python3 scripts/first_wave_gate.py regression           # 全量后端；任何 skip/XFAIL 都失败
python3 scripts/first_wave_gate.py e2e                  # 当前明确返回 NOT RUN 和非零退出码
python3 scripts/first_wave_gate.py artifact             # 全量 artifact 脱敏扫描；任一 finding 都失败
```

`security` 对 skip 和 XFAIL 都返回失败；缺少数据库、对象存储、Temporal 或浏览器等依赖不能算通过。第二波真实主链的 Playwright 占位命令也会因当前 skip 返回非零：

```bash
pnpm --dir frontend exec playwright test --config playwright.real-api.config.ts
```

日志和 JUnit 固定写入 `artifacts/test-gates/latest/`，Playwright trace、截图和视频写入 `artifacts/test-gates/latest/playwright/`。当前逐项结果与依赖见 `backend/tests/system/ACCEPTANCE-MATRIX.md`；合同冲突见 `backend/tests/gates/CONTRACT-CONFLICTS.md`。测试完成后可删除本门禁的容器、网络和临时数据：

```bash
docker compose -f compose.test.yaml down --volumes --remove-orphans
```

### 宿主机直接运行前端

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
VITE_MOCK_MODE=browser pnpm dev
```

打开 <http://localhost:5173>。直接打开根地址时，浏览器 Mock 会自动载入管理员、项目作用域和完整 capability 快照；无需先选择场景或登录。

`.env.example` 默认使用 `VITE_MOCK_MODE=off`，用于提醒真实环境不要意外启用 Mock。独立体验前端时应像上面一样在启动命令中显式设置 `VITE_MOCK_MODE=browser`。不要把 `.env.example` 直接覆盖到仓库已有的 `.env.local`，否则会把本地 Mock 配置改回 `off`。修改环境变量后需要停止并重新启动 Vite，仅刷新浏览器不会生效。

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
| `VITE_DEV_PROXY_TARGET` | 仅供 Vite 开发服务器使用的 `/api` 代理目标；Compose 自动设为 `http://api:8000` |

公开 URL 必须是应用根相对路径或 HTTPS 地址；仅 Vite 开发模式允许 loopback HTTP 地址。Docker Compose 会通过 `VITE_DEV_PROXY_TARGET=http://api:8000` 让 Vite 将 `/api` 转发到 API 容器；宿主机直接运行 Vite 时默认不启用该代理，使用 `/api/v1` 且关闭 Mock 需要同源网关，或将 API 地址填写为完整的 loopback URL。

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

正式平台类型位于 `frontend/src/shared/api/generated/platform.ts`。生成脚本先构造 production-composed
`create_app(...).openapi()`，规范化并验证 runtime `$ref`，再调用仓库锁定的
`openapi-typescript`；`backend/openapi.generated.yaml` 仍是 fragment 聚合/兼容性输入，不是类型生成的替代事实源。

```bash
cd frontend
pnpm gen:api
pnpm gen:api --check
```

生成头同时记录 runtime OpenAPI 与 fragment aggregate 的 SHA-256。不要直接编辑带有
`AUTO-GENERATED` 标记的文件；旧的分域生成文件只保留兼容用途，不代表当前 runtime 合同。

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
├── backend/              # API、Worker、迁移、测试和可观测资产（无独立部署入口）
├── deploy/               # 唯一部署目录：统一 Helm、Compose 网关、环境和发布脚本
├── compose.dev.yaml      # 完整本地开发入口
├── docs/                 # UI 基线和重构记录
└── plan/                 # 当前前端实施计划与逐页计划
```

## 项目边界

- `frontend/src/shared/api/generated/` 与 `backend/openapi.generated.yaml` 必须通过合同兼容门禁，类型存在本身不代表真实环境联调已经完成。
- 浏览器 Mock 只用于前端开发，不等同于生产可用或真实接口联调完成。
- PostgreSQL、Temporal、对象存储和身份服务由外部平台提供；仓库保存连接合同、Secret 引用和本地开发依赖，不保存生产凭据。
- 当前后端验收矩阵中标记为 XFAIL/PARTIAL/FAIL 的项目仍是发布阻断项，统一发布流水线会将它们视为失败。

## 统一发布

本地开发使用 `docker compose -f compose.dev.yaml up --build`。生产入口是
`deploy/helm/hc-data-platform`：一个平台 release ID 对应一个 Git tag、一个 Helm revision 和
Frontend/API/Worker 三个不可变镜像 digest。数据库迁移仅向前执行，并必须兼容上一个已验证平台版本。

具体构建、提升和回滚约定见 [统一发布说明](deploy/README.md)。

更多背景见 [前端实施计划](plan/FINAL-IMPLEMENTATION-PLAN.md)、[UI 基线](docs/FRONTEND-UI-BASELINE.md) 和 [前端脚手架说明](frontend/docs/frontend-scaffold-notes.md)。
