# HC Data Platform 项目目录说明

## 1. 阅读范围

本文说明 `/home/czy/hc_DataPlatform` 当前项目目录，采用 ASCII 树展示。只展开项目自有目录；`.git/`、`node_modules/`、`.venv/` 和各类缓存目录只说明用途，不展开内部内容。

这个仓库目前同时包含 **React 前端、浏览器 Mock、Python 后端、API 合同、数据库迁移、部署/可观测性资产和设计实施文档**。后端已具备可运行的应用骨架和多个领域实现，但不能仅根据目录存在就推断所有前端页面都已接入生产适配器；具体完成度应以模块代码、运行装配和测试为准。

## 2. 项目总览

```text
hc_DataPlatform/
+-- .github/
|   `-- workflows/           # GitHub Actions；平台 CI、统一发布、部署与整版回滚
+-- backend/                 # Python/FastAPI 数据管线后端
|   +-- docs/                # 后端依赖请求与集成发现记录
|   +-- migrations/          # 按领域拆分的 PostgreSQL SQL 迁移
|   +-- observability/       # OTLP、Prometheus、Grafana 和日志验证资产
|   +-- openapi/             # 模块级 OpenAPI 合同片段
|   +-- runbooks/            # 管线告警处置手册
|   +-- src/                 # 后端 Python 运行源码
|   `-- tests/               # 单元、集成、系统、故障和容量测试
+-- docs/                    # 使用指南、架构合同、审计记录和设计说明
|   +-- architecture/        # 架构决策、YAML 合同和 JSON Schema；自动化测试会读取
|   +-- audits/              # 历史审计报告、验收证据和运行快照
|   `-- status/              # 历史任务状态快照，不是当前源码事实源
+-- deploy/                  # 唯一平台部署目录：Helm、环境、Compose 网关和发布脚本
|   `-- compose/             # 测试/验收/演练 Compose、Nginx 配置和配套资源
+-- compose.dev.yaml         # 日常开发入口，支持源码热更新
+-- compose.single-server.yaml # 单机静态部署入口
+-- frontend/                # 可安装、开发和构建的 React/Vite 前端
|   +-- container/           # 前端镜像内部的 Nginx 与入口脚本，不是部署入口
|   +-- docs/                # 前端架构、交接和交付记录
|   |   +-- adr/             # 架构决策记录
|   |   +-- dep-requests/    # 跨任务依赖请求快照
|   |   `-- status/          # 各页面组历史交付状态
|   +-- node_modules/        # pnpm 安装的依赖，可重建且不应手改
|   +-- public/              # 构建时原样发布的静态资源和 MSW Worker
|   +-- scripts/             # OpenAPI 类型生成等维护脚本
|   `-- src/                 # 前端运行源码
`-- scripts/                 # 测试门禁、验收产物整理等仓库维护脚本
```

`backend/.venv/`、`frontend/node_modules/` 和工具缓存都是可重建的本地依赖/缓存，不属于业务源码。`frontend/dist/` 会在前端构建后生成；`backend/openapi.generated.yaml` 则是由模块合同聚合后提交到仓库的生成文件。

Compose 文件的用途和运行组合见 [`deploy/compose/README.md`](../deploy/compose/README.md)。
`docs/` 不能作为缓存整目录清理：`architecture/` 的合同和 Schema 被备份、恢复、发布、维护和运行状态测试直接读取，部署及上传指南也被根目录 README 引用。
`audits/`、`status/` 和 UI 计划属于历史记录，可在确认无需追溯并检查引用后单独归档。

## 3. 后端目录结构

```text
backend/
+-- docs/
|   +-- dep-requests/               # 各后端任务提出的依赖/运行时请求
|   `-- integration-findings/       # 跨模块集成检查结果
+-- migrations/                     # alignment、annotation、ingest 等模块的 SQL
+-- observability/                  # 指标合同、Collector、Dashboard、告警和验证器
+-- openapi/                        # 按模块维护的 OpenAPI YAML 片段
+-- runbooks/                       # 运行故障与告警处理说明
+-- src/hc_data_platform/           # 可打包的 Python 源码
+-- tests/                          # 与源码模块对应的测试和跨模块验收
+-- .env.example                    # 本地环境变量示例，不存放真实密钥
+-- Dockerfile                      # 独立的 api 和 worker 镜像目标
+-- Makefile                        # 安装、静态检查、测试、合同和镜像命令
+-- openapi.generated.yaml          # 确定性聚合的 OpenAPI 合同，禁止手改
+-- pyproject.toml                  # 包元数据、依赖、命令入口和工具配置
`-- uv.lock                         # uv 锁定依赖
```

后端采用“合同优先的模块化单体”。`core` 会按模块名发现可选的 `router`，因此领域路由通常留在自己的模块内，而不是维护一份集中路由注册表。

## 4. 后端源码模块

```text
backend/src/hc_data_platform/
+-- alignment/              # 多模态纳秒时间线对齐和 Arrow 分片写入
+-- annotation/             # 标注任务、草稿/修订、排除区间、审核和权限 API
+-- core/                   # FastAPI 装配、配置、请求上下文、错误、健康检查和合同聚合
+-- ingest/                 # 采集任务、分片直传、Raw 清单提交和离线导入 CLI
+-- lance_catalog/          # Lance Schema、逻辑版本、Rollout 血缘和稳定 Step 读取
+-- aligned_media/          # 接入期逐相机固定 30 Hz H.264 MP4、发布收据和直接授权
+-- publishing/             # 冻结审核快照并导出 Lance/LeRobot V3 发布版本
+-- quality/                # 频率、丢帧、图像/点云等规则驱动的数据质量评估
+-- security/               # JWT、Scope/RLS、工作单元、幂等、审计和 Outbox
+-- verification/           # 流式 MCAP 结构、索引、CRC、Topic 和解码探针校验
`-- workflow/               # 异步 Job API、管线编排、Temporal Workflow/Activity 和 Worker
```

领域模块通常按以下职责拆分；并非每个模块都需要全部文件：

```text
models.py                   # 稳定输入、输出、状态和领域值对象
ports.py                    # 存储、目录、编码器等外部边界协议
service.py / engine.py      # 用例编排或纯领域计算
adapters.py / postgres.py   # MinIO/OSS、Lance、PostgreSQL、FFmpeg 等实现
router.py                   # FastAPI HTTP 边界
memory.py / InMemory*       # 单元测试和合同测试使用的内存实现
```

其中 `core/app.py` 是 API 应用工厂；后端还通过 `pyproject.toml` 暴露四个命令入口：`hc-data-api`、`hc-data-worker`、`hc-offline-import` 和 `hc-openapi`。

## 5. 后端合同、持久化与验证

```text
backend/
+-- openapi/                # core、安全、接入、校验、质量、对齐、目录等合同片段
+-- migrations/             # 各领域拥有自己的表、约束、RLS 或不可变性迁移
+-- tests/
|   +-- alignment/          # 多模态对齐与 Arrow 写入
|   +-- annotation/         # 标注服务、API、合同和 PostgreSQL
|   +-- core/               # 应用公共能力
|   +-- ingest/             # 上传控制面、CLI、路由和 MinIO
|   +-- lance_catalog/      # Schema、目录、Port 和 Lance 集成
|   +-- aligned_media/      # 固定帧率 MP4、精确对象发布、提交门禁和 FFmpeg
|   +-- publishing/         # 发布冻结和导出
|   +-- quality/            # 质量规则和合同
|   +-- security/           # 鉴权、迁移、RLS 和 PostgreSQL
|   +-- verification/       # MCAP 流式验证
|   +-- workflow/           # 内存编排和 Temporal Workflow
|   +-- fixtures/           # 合法/损坏 MCAP 与结构化边界样本
|   +-- load/               # API/MinIO 容量探针和容量报告
|   `-- system/             # E2E、故障注入、恢复、Helm、可观测性和发布门禁
`-- observability/          # 指标语义合同、OTLP 管线、Dashboard 和告警
```

`openapi/*.yaml` 是模块维护的事实源，修改后通过 `hc-openapi` 聚合到 `openapi.generated.yaml`。`migrations/` 与源码模块同名，方便领域代码和数据库约束一起审查。测试中的 `InMemory*`、合同 Fake 和本地服务适配器用于验证边界，不等同于生产环境已经完成所有依赖注入。

## 6. 前端源码分层

```text
frontend/src/
+-- app/                     # 应用装配：环境、Provider、路由、外壳和主题
|   +-- providers/           # Query、Toast、Scope、UI 和错误边界
|   +-- router/              # 路由聚合、懒加载和权限守卫
|   +-- shell/               # 顶栏、导航和 Scope 切换
|   `-- theme/               # 设计 Token、Ant Design 主题和全局样式
+-- entities/                # 跨业务复用的领域实体、状态和类型规则
+-- features/                # 按业务域组织的 API、规则、状态机和局部 UI
+-- mocks/                   # MSW Handler、固定样本和场景切换
+-- pages/                   # P01-P19 路由级页面
`-- shared/                  # 跨业务公共基础设施和 UI
```

简单理解：`pages` 负责组装页面，`features` 负责业务能力，`entities` 负责领域模型，`shared` 负责公共能力，`app` 负责把整个应用装配起来。

## 7. 前端业务功能目录

业务目录中的 `api/` 统一负责响应校验、数据适配、请求函数、Query Key 和 React Query Hook，下面不重复解释。

```text
frontend/src/features/
+-- access/                  # 成员、角色、Capability 和 ScopeGrant
+-- annotation/              # 标注任务领取、草稿、提交、审核和 Rebase
|   +-- drafts/              # 浏览器短期草稿恢复
|   +-- forms/               # Schema 驱动的标注表单
|   +-- handoff/             # 领取、继续或创建任务的入口交接
|   `-- ui/                  # 标注专用状态和确认界面
+-- audit/                   # 审计事件、筛选和字段可见性
+-- calibrations/            # 标定集、数据校验和发布规则
+-- cleaning/                # 人工问题、清洗草稿、EDL 和工作台状态
+-- dashboard/               # 工作台指标、覆盖率、趋势和待办
+-- data-governance/         # 数据治理流程阶段、状态和工作台视图模型
+-- data-schemas/            # Data Schema 版本、兼容性和发布规则
+-- datasets/                # 数据集、版本、Episode、审核和删除预检
|   `-- components/          # 数据集业务内复用组件
+-- ingest/                  # 数据源、上传会话和校验流程
|   +-- connectors/          # 接入连接器定义和凭据边界
|   `-- upload/              # 浏览器 OSS 分片上传运行时
+-- lifecycle/               # 存储生命周期策略、模拟、执行和恢复
+-- robot-models/            # 机器人模型、版本、资产上传和发布
+-- robots/                  # 机器人、组件拓扑和挂载约束
+-- storage-overview/        # 存储容量、对象、Multipart 和成本
`-- viewer/                  # Episode 媒体、时间轴和机器人可视化
    `-- runtime/             # Three.js/URDF、签名资源和资源释放
```

## 8. 前端页面目录

页面目录通常包含 `page.tsx`、`routes.tsx`、`query-codec.ts` 和页面样式；标有“含 `components/`”的目录还包含仅供本页使用的组件。

```text
frontend/src/pages/
+-- p01-dashboard/           # 数据工作台（含 components/）
+-- p02-data-sources/        # 数据源管理（含 components/）
+-- p03-upload-jobs/         # 上传任务中心（含 components/）
+-- p04-upload-detail/       # 单次上传详情（含 components/）
+-- p05-datasets/            # 数据集目录（含 components/）
+-- p06-dataset-detail/      # 数据集详情和 Episode Viewer（含 components/）
+-- p07-version-detail/      # 数据集版本详情与审核（含 components/）
+-- p08-data-annotation/     # 标注队列和标注工作台
+-- p09-manual-issues/       # 人工问题清单与分诊
+-- p10-cleaning-drafts/     # 清洗草稿列表与详情
+-- p11-manual-cleaning/     # 手动清洗工作台
+-- p12-storage-overview/    # 存储容量总览（含 components/）
+-- p13-storage-lifecycle/   # 生命周期策略与任务
+-- p14-robot-models/        # 机器人模型资产
+-- p15-robots/              # 机器人与组件
+-- p16-calibrations/        # 标定管理
+-- p17-data-schemas/        # Data Schema Registry
+-- p18-access/              # 用户与权限
+-- p19-audit/               # 审计日志（含 components/）
`-- ui-011e/                 # P14-P17 共用的管理工作区样式，不是独立页面
```

页面私有组件放在本页 `components/`；跨页面 UI 放在 `shared/ui/`；业务规则和请求放在对应 `features/`。

## 9. 前端 Mock 目录

```text
frontend/src/mocks/
+-- fixtures/                # 稳定的模拟接口响应数据
|   +-- annotation/          # 标注样本
|   +-- audit/               # 审计样本
|   +-- cleaning/            # 人工清洗样本
|   +-- common/              # Scope、用户、权限和任务等公共样本
|   +-- dashboard/           # 工作台样本
|   +-- datasets/            # 数据集、版本和 Episode 样本
|   +-- ingest/              # 数据源和上传样本
|   +-- management/          # P13-P18 系统管理样本
|   `-- storage-overview/    # 存储总览样本
+-- handlers/                # 按业务域实现的 MSW API 路由
`-- scenarios/               # happy、empty、错误、权限等场景注册与切换
```

Mock 只用于前端开发和演示；即使 Mock 与后端存在同名接口，也不表示该页面已经切换到真实 API 或生产持久化实现。

## 10. 前端 Shared 目录

```text
frontend/src/shared/
+-- api/                     # 公共 HTTP、错误、校验、分页和 Query Key
|   `-- generated/           # OpenAPI 生成类型，禁止手改
+-- auth/                    # Capability 和资源操作权限判断
+-- config/                  # API、SSE、版本和发布环境配置
+-- jobs/                    # 异步任务跟踪和全局任务中心
+-- lib/                     # BigInt、时间范围和命名转换等纯工具
+-- routing/                 # 路由注册、安全回跳和查询参数编解码
+-- scope/                   # 当前用户、组织、项目、区域和授权状态
+-- telemetry/               # 遥测记录和敏感信息脱敏
`-- ui/                      # 跨页面公共 UI
    +-- actions/             # 危险操作确认
    +-- data/                # DataTable 和游标分页
    +-- forms/               # RHF/Zod 表单适配与文件选择
    +-- layout/              # 页面骨架、Header、筛选栏和 Drawer
    `-- state/               # 页面状态、StatusTag 和 MetricCard
```

## 11. 放置代码的快速判断

```text
后端应用装配和公共协议    -> backend/src/hc_data_platform/core/
后端领域规则和用例        -> backend/src/hc_data_platform/<domain>/
后端外部依赖抽象与实现    -> 对应领域的 ports.py 与 adapters.py/postgres.py
后端 HTTP 合同            -> backend/openapi/ 与对应 router.py
后端数据库结构            -> backend/migrations/<domain>/
平台部署与回滚            -> deploy/
后端运行监控              -> backend/observability/、backend/runbooks/
前端全局装配、路由或主题  -> frontend/src/app/
前端路由页面组合代码      -> frontend/src/pages/
前端业务规则和请求        -> frontend/src/features/
稳定的前端领域类型        -> frontend/src/entities/
多个前端业务复用的能力    -> frontend/src/shared/
前端开发模拟接口和数据    -> frontend/src/mocks/
计划、状态和架构说明      -> docs/
```

边界提醒：

- UI 计划和历史状态说明不代表当前实现状态，应以源码、合同和测试为准。
- `docs/**/status/`、`backend/docs/dep-requests/` 和 `frontend/docs/dep-requests/` 是状态或协作快照，应以当前源码、合同和测试为准。
- `backend/openapi.generated.yaml`、`frontend/src/shared/api/generated/` 和 `frontend/public/mockServiceWorker.js` 是生成文件，不应直接修改。
- `backend/.venv/`、`frontend/node_modules/`、`dist/`、`__pycache__/` 和工具缓存不应作为源码提交或手改。
- `.env`、`.env.local` 只用于本地运行，不应写入真实密钥；可提交的示例值放在 `.env.example`。
