# HC Data Platform

具身智能数据治理平台，包含 React 前端、FastAPI API、Temporal Worker、PostgreSQL、MinIO、迁移和部署资产，页面范围为 P01–P20。

## 最快查看前端（Mock，推荐）

Mock 模式会加载演示账号、项目、权限和示例数据，适合直接查看全部页面。

```bash
cd /home/czy/hc_DataPlatform

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
- MinIO Console：<http://localhost:9001>

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

# 视觉基线校验
pnpm --dir frontend verify:visual-baseline

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
