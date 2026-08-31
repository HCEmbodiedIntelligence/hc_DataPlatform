# HC Data Platform

具身智能数据治理平台，包含 React 前端、FastAPI API、Temporal Worker、PostgreSQL、阿里云 OSS、迁移和部署资产，页面范围为 P01–P20。

## 最快查看前端（Mock，推荐）

Mock 模式会加载演示账号、项目、权限和示例数据，适合直接查看全部页面。

```bash
cd /home/czy/hc_DataPlatform

# 首次启动：填入已创建的 OSS Bucket、地域和 RAM AccessKey
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

## 查看真实后端模式

完整重建并启动真实 API 环境：

```bash
cd /home/czy/hc_DataPlatform

# 确认 .env 中已配置真实 OSS；Bucket 还需允许前端来源的 PUT/HEAD CORS

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

## 通过平台上传 Unitree G1 LeRobot 数据

推荐让机器端脚本使用平台上传协议，不要让机器持有 OSS AccessKey 后绕过平台直接写
Bucket。上传流程为：

```text
本地 LeRobot（Parquet + MP4）
  -> 向平台创建原生 LeRobot Raw 上传会话
  -> 使用平台签发的临时 URL 将文件正文直传 OSS
  -> 向平台提交完成
  -> 平台登记 Raw Source、Episode 和处理任务
  -> 自动质检、对齐、可视化和标注
```

文件正文仍然直传 OSS，不经过 API 服务器；平台负责身份校验、项目归属、Object Key、
临时上传授权和完成登记，因此不需要定时扫描 Bucket，也不需要在机器上填写 OSS
AccessKey。

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
- 对象存储：`.env` 中配置的阿里云 OSS Bucket（本地不再启动 MinIO）

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
