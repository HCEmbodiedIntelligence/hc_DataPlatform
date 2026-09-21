# HC Data Platform

具身智能数据治理平台，包含上传存储、自动质检、时间对齐、可视化、标注、审核、发布和导出，页面范围为 P01–P20。
原始 MCAP、LeRobot v3、ROS bag 文件原样保存；原生 G1 LeRobot 处理直接引用原视频。

## 默认部署：支持源码热更新

部署到机器后还需要持续更新源码，使用 **`compose.dev.yaml`**。本页的启动、更新、停止命令均以此为准。
默认连接真实 API 和本机 MinIO，无需 OSS 凭据。

- 修改 `frontend/src`：Vite 热更新。
- 修改 `backend/src`：API 和合并 Worker 自动重启。
- 依赖、Dockerfile、环境变量或数据库迁移有变化：按下文“更新代码与依赖”处理。

### 首次启动或重新构建

```bash
cd /home/czy/hc_DataPlatform

# 可选：仅在没有 .env 时创建，保留已有配置
[ -f .env ] || cp .env.example .env

# 构建两个应用镜像并启动；更新前端依赖卷、移除已合并的旧容器
docker compose -f compose.dev.yaml up --build -V -d --remove-orphans

# 查看常驻服务与初始化任务
docker compose -f compose.dev.yaml ps -a
```

`-V` 只更新前端 `/app/node_modules` 匿名依赖卷，数据库、MinIO 和处理缓存使用的命名卷会保留。
清理过构建缓存后，第一次构建会重新安装依赖，耗时会增加。

打开 <http://localhost:8088>，真实账号登录入口为 <http://localhost:8088/auth/login>。
新注册用户需要申请加入项目，由有权限的管理员批准后访问业务数据。
项目内具备上传权限（`upload.manage`）的账号可独立完成上传、标注、自审、发布、导出和下载；
已有账号刷新页面即可获取完整操作入口，业务检查和项目访问范围仍生效。
`compose.real-api.yaml` 用于隔离自动化验收，日常部署无需叠加。

如果机器上仍在运行 `compose.single-server.yaml`，先执行下面的停止命令，再启动热更新版：

```bash
docker compose --env-file .env.single-server -f compose.single-server.yaml stop
```

两套配置默认都使用 8088，当前单机配置还复用了开发版的数据卷，不能同时运行。
不需要源码热更新时，另见[六服务静态部署与迁移](docs/single-server-storage.md)。

### 实际会构建、启动多少个

| 类型 | 数量 | 内容 |
| --- | ---: | --- |
| 本地构建镜像 | 2 | `hc-data-platform-backend:dev`、`hc-data-platform-frontend:dev` |
| 常驻服务 | 8 | `frontend`、`gateway`、`api`、`worker`、`postgres`、`minio`、`object-store-browser`、`temporal` |
| 一次性任务 | 3 | `migration`、`minio-init`、`runtime-cache-init`，成功后退出 |

API、Worker、迁移和缓存初始化复用同一个后端镜像，由 `migration` 服务统一构建。
合并 Worker 同时监听主处理与媒体队列，不再单独启动 `media-worker`。
本地应用镜像禁用远程拉取；基础镜像、第三方服务镜像和软件依赖首次缺失时仍需下载。

日志中的 `Building (30/39)` 表示构建步骤进度，包含安装依赖、复制文件和导出镜像；
实际容器以 `docker compose -f compose.dev.yaml ps -a` 为准。

Temporal UI 按需启动，不计入默认八个常驻服务：

```bash
docker compose -f compose.dev.yaml --profile tools up -d temporal-ui
```

### 更新代码与依赖

| 修改内容 | 操作 |
| --- | --- |
| `frontend/src` / `backend/src` | 保存或同步源码后自动重载，无需重新构建 |
| Python / 前端依赖、锁文件、Dockerfile | 执行下面的重新构建命令 |
| `.env` / Compose 配置 | `docker compose -f compose.dev.yaml up -d --remove-orphans` |
| `backend/migrations` | `docker compose -f compose.dev.yaml up -d migration`，确认退出码为 0 |

```bash
cd /home/czy/hc_DataPlatform

# 依赖或 Dockerfile 改动后重新构建，并更新前端依赖卷
docker compose -f compose.dev.yaml up --build -V -d --remove-orphans

# 数据库迁移后检查执行结果
docker compose -f compose.dev.yaml logs --tail 50 migration
```

### 可选：仅查看演示界面

Mock 模式使用演示账号、权限和示例数据。已有环境启动后，只切换前端：

```bash
HC_FRONTEND_MOCK_MODE=browser \
docker compose -f compose.dev.yaml up -d --no-build --no-deps --force-recreate frontend

# 切回真实后端
HC_FRONTEND_MOCK_MODE=off \
docker compose -f compose.dev.yaml up -d --no-build --no-deps --force-recreate frontend
```

## Unitree G1 LeRobot 上传

开发环境的原生上传流程：

```text
本地 LeRobot（Parquet + MP4）
  -> 向平台创建原生 LeRobot Raw 上传会话
  -> 将文件正文上传到本地平台 API
  -> 向平台提交完成
  -> 平台登记 Raw Source、Episode 和处理任务
  -> 自动质检、对齐、生成可视化与人工标注任务
  -> 人工标注 → 同账号审核 → 数据集手动发布 → 导出并下载
```

网页开发环境固定使用平台 API 代理上传；视频从本机 MinIO 读取。选择文件夹时，先选择
有效采集任务和机器人；目标数据集由任务绑定自动确定，再在确认窗口发布人工标注规则。原始文件保留，不转换成 MCAP。

先按上文启动默认热更新部署，然后在仓库根目录运行交互式脚本：

```bash
cd /home/czy/hc_DataPlatform
backend/.venv/bin/python scripts/upload_lerobot_via_platform.py
```

按提示输入：

1. 本地 LeRobot 文件夹路径，可以直接粘贴 Windows 路径；
2. 平台地址，本机直连 API 通常使用 `http://127.0.0.1:8000`，经过网关可使用
   `http://127.0.0.1:8088`；
3. Organization ID；
4. 平台用户名和密码。

当前 Unitree G1 数据的参数已经预填：

```text
Project:  be22-hf-g1-video-20260819-02-p1
Region:   be22-hf-g1-video-20260819-02-cn
Dataset:  be22-hf-g1-video-20260819-02-p1
Task:     14d16ba1-d95a-5ee3-aaa7-7b7d78091b52
Robot:    robot-d1a17126-b495-59b8-bf48-0ccce0a6ffe7
Episodes: 从 meta/info.json 自动读取
```

也可以使用 Token 非交互上传。Token 通过环境变量传入，不会出现在命令行参数中：

```bash
export HC_DATA_ACCESS_TOKEN='平台 Bearer Token'
export HC_ORGANIZATION_ID='所属 Organization ID'

backend/.venv/bin/python scripts/upload_lerobot_via_platform.py \
  --source-dir 'E:\HC-Unitree-G1-Conversion-Cache\unitreerobotics--G1_WBT_Dex1_Put_Clothes_into_Washing_Machine' \
  --api-base-url http://127.0.0.1:8000
```

Windows 盘符路径会在 WSL 中自动转换成 `/mnt/<盘符>/...`。平台保留原始 Parquet、MP4
及元数据文件，不在机器人端生成 MCAP。

更完整的说明见 [LeRobot 平台上传指南](docs/lerobot-platform-upload.md)。机器人上传只允许走
平台控制面；不提供 OSS AccessKey 直写、`lerobot-inbox` 扫描或 Bucket 自动发现兼容入口。

## 查看当前状态

```bash
cd /home/czy/hc_DataPlatform

# 所有服务
docker compose -f compose.dev.yaml ps

# 当前前端模式
docker compose -f compose.dev.yaml exec -T frontend printenv VITE_MOCK_MODE

# 前端 API 地址
docker compose -f compose.dev.yaml exec -T frontend printenv VITE_API_BASE_URL

# 前端、API、Worker 日志
docker compose -f compose.dev.yaml logs -f frontend api worker

# 只看前端日志
docker compose -f compose.dev.yaml logs -f frontend

# 只看 API 日志
docker compose -f compose.dev.yaml logs -f api
```

其他本地地址：

- API 文档：<http://localhost:8000/docs>
- Temporal UI：<http://localhost:8080>（需启用 `tools` profile）
- MinIO 控制台：<http://localhost:9001>（本机开发账号 `minio` / `minio-local-only`）
- 浏览器媒体入口：<http://127.0.0.1:9000>；数据持久化在 `minio-data` Docker 卷

## 启停与清理

```bash
cd /home/czy/hc_DataPlatform

# 日常启动：镜像和依赖未变化时无需 --build
docker compose -f compose.dev.yaml up -d

# 手动重启应用
docker compose -f compose.dev.yaml restart frontend api worker

# 停止，包括按需开启的 Temporal UI，保留容器和数据
docker compose -f compose.dev.yaml --profile tools stop

# 清理本项目已停止的容器，保留数据卷
docker compose -f compose.dev.yaml --profile tools rm -f

# 需要完整重建容器时：移除本项目容器和网络，保留命名数据卷
docker compose -f compose.dev.yaml --profile tools down --remove-orphans
```

只有需要释放构建缓存空间时执行；这会清理当前 Docker builder 的全部可清理构建缓存，
影响该 builder 上其他项目的后续构建速度，但不会删除数据库或上传文件：

```bash
docker builder prune --all --force
```

不要使用 `down -v`、`docker volume prune` 或 `docker system prune --volumes`，除非明确要删除持久化数据。
清理后按本文“首次启动或重新构建”启动即可。

## 常用测试命令

```bash
cd /home/czy/hc_DataPlatform

# 前端类型、单测、构建
pnpm --dir frontend typecheck
pnpm --dir frontend exec vitest run --reporter=dot
VITE_MOCK_MODE=off VITE_RELEASE_ENV=production pnpm --dir frontend build

# API 类型是否与 runtime OpenAPI 一致
pnpm --dir frontend gen:api --check

# 视觉基线校验（PNG 产物与 SHA-256 基线由验收运行传入）
pnpm --dir frontend verify:visual-baseline -- \
  --artifacts /absolute/path/to/png-artifacts \
  --baseline /absolute/path/to/visual-baseline.json

# 后端全量测试
backend/.venv/bin/python -m pytest -q -c backend/pyproject.toml backend/tests

# 空白和冲突检查
git diff --check
git diff --name-only --diff-filter=U
git ls-files -u
```

更多资料：

- [本地服务器验证里程碑](plan/LOCAL-SERVER-VALIDATION-MILESTONE.md)
- [完整流程修复报告](docs/audits/2026-09-20-full-pipeline-fixes.md)
- [统一发布说明](deploy/README.md)
- [前端实施计划](plan/FINAL-IMPLEMENTATION-PLAN.md)
- [视觉合同](plan/前端-E01-E10-视觉还原合同.md)
- [验收矩阵](backend/tests/system/ACCEPTANCE-MATRIX.md)

### 任务、数据集与 Episode 导出

采集任务绑定一个目标数据集；同一数据集可接收多个任务的数据。处理上传时先选择任务，平台自动使用其绑定的数据集，每个 Episode 保留来源任务。已有数据的任务不能直接改绑数据集。仅存档可直接指定数据集，稍后处理时只能关联该数据集下的任务。

数据导出页通过任务或数据集查找当前 READY 版本的 Episode，支持多选、跨分页全选与取消勾选。同一数据集的所选 Episode 按格式分别生成 ZIP；任务筛选不会包含同一数据集其他任务的 Episode。后端验证版本归属、质检和标注审核，失败重试保留原选择。已发布版本的界面 ID 与发布名称解析到同一份不可变清单，不复制原视频或原始数据。

当前 Lance 和 LeRobot v3 导出包含对齐数据与媒体帧引用，不打包视频实体；LeRobot v3 将命名数值向量保留为数组，其他结构化字段以 JSON 字符串保留，编码说明写入 `meta/info.json` 的 `hc.structured_features`。原始文件下载仍通过原始数据浏览入口。
