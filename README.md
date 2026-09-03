# HC Data Platform

具身智能数据治理平台，包含 React 前端、FastAPI API、Temporal Worker、PostgreSQL、对象存储、迁移和部署资产，页面范围为 P01–P20。

## 当前分支目标：本地服务器验证

`codex/local-server-validation` 分支用于实现 GitHub 里程碑“本地服务器验证”。目标是让网页、
脚本和自动化验收只连接本地平台入口；浏览器、API 和 Worker 都不访问 OSS，上传、持久化、
处理和结果读取全部在本机完成。

**该目标尚未完成。** 当前 Compose 仍配置为 OSS provider，LeRobot 网页客户端仍保留对象存储
直传路径，因此现阶段不能把单元测试通过等同于里程碑完成。范围、问题清单和完成标准见
[“本地服务器验证”里程碑](plan/LOCAL-SERVER-VALIDATION-MILESTONE.md)。生产对象存储、备份、发布
和灾备能力不属于本次清理范围。

## 最快查看前端（Mock，推荐）

Mock 模式会加载演示账号、项目、权限和示例数据，适合直接查看全部页面。

```bash
cd /home/czy/hc_DataPlatform

# 当前基线仍读取对象存储配置；本地无云凭据目标尚在实现中
cp .env.example .env

# 首次启动或依赖有变化
docker compose -f compose.dev.yaml up --build -d

# 明确切换到 Mock 模式；已有容器时也可直接执行
HC_FRONTEND_MOCK_MODE=browser \
docker compose -f compose.dev.yaml up -d \
  --no-build --no-deps --force-recreate frontend
```

打开：

- 完整应用：<http://localhost:8088>
- Vite 前端：<http://localhost:5174>

常用页面：

```text
http://localhost:8088/dashboard
http://localhost:8088/ingest/uploads/new
http://localhost:8088/datasets
http://localhost:8088/annotations/annotate
http://localhost:8088/storage/overview
http://localhost:8088/settings/access
http://localhost:8088/settings/audit
http://localhost:8088/collection-tasks
```

## 查看真实后端模式（当前基线）

完整重建并启动真实 API 环境：

```bash
cd /home/czy/hc_DataPlatform

# 里程碑完成前，当前基线仍需要 .env 中的对象存储配置

docker compose \
  -f compose.dev.yaml \
  -f compose.real-api.yaml \
  up --build -d

docker compose \
  -f compose.dev.yaml \
  -f compose.real-api.yaml \
  ps
```

如果完整环境已经启动，只切换前端即可：

```bash
cd /home/czy/hc_DataPlatform

HC_FRONTEND_MOCK_MODE=off \
docker compose -f compose.dev.yaml up -d \
  --no-build --no-deps --force-recreate frontend
```

真实模式登录入口：<http://localhost:8088/auth/login>

真实模式不会自动注入管理员权限。新注册用户是空账户，需要申请加入项目并由另一名有权限的管理员批准 capability；未登录、空账户或权限不足时页面会显示无权访问。只想查看完整界面时请使用上面的 Mock 模式。

## Unitree G1 LeRobot 上传

里程碑目标要求机器端脚本和网页只连接本地平台 API：

```text
本地 LeRobot（Parquet + MP4）
  -> 向平台创建原生 LeRobot Raw 上传会话
  -> 将文件正文上传到本地平台 API
  -> 向平台提交完成
  -> 平台登记 Raw Source、Episode 和处理任务
  -> 自动质检、对齐、可视化和标注
```

上述流程是目标状态，不是当前已完成能力。当前客户端仍可能先尝试对象存储直传，再回退到
平台 API 代理上传；在里程碑完成前，不应把这条链路作为“全本地验证已通过”的证据。

先启动上面的真实后端模式，然后在仓库根目录运行交互式脚本：

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
- Temporal UI：<http://localhost:8080>
- 存储：当前基线仍使用 `.env` 中的对象存储配置；里程碑目标是提供不依赖云凭据的本地存储

## 重启与重新构建

```bash
cd /home/czy/hc_DataPlatform

# 日常启动
docker compose -f compose.dev.yaml up -d

# 重启前端
docker compose -f compose.dev.yaml restart frontend

# 重启 API 和 Worker
docker compose -f compose.dev.yaml restart api worker

# 依赖或 Dockerfile 变化后重新构建
docker compose -f compose.dev.yaml up --build -V -d

# 使用本机缓存构建，不主动拉取镜像
docker compose -f compose.dev.yaml build --pull=false
docker compose -f compose.dev.yaml up -d --no-build
```

源码已挂载到容器：修改 `frontend/src` 会触发 Vite 热更新，修改 `backend/src` 会触发 API/Worker 重启。

## 停止环境

```bash
cd /home/czy/hc_DataPlatform

# 停止但保留容器和数据
docker compose -f compose.dev.yaml stop

# 移除容器和网络，保留 named volumes 中的数据
docker compose -f compose.dev.yaml down
```

不要使用 `down -v`，除非明确要删除本地数据库和对象存储数据。

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

- [统一发布说明](deploy/README.md)
- [前端实施计划](plan/FINAL-IMPLEMENTATION-PLAN.md)
- [视觉合同](plan/前端-E01-E10-视觉还原合同.md)
- [验收矩阵](backend/tests/system/ACCEPTANCE-MATRIX.md)
