# HC Data Platform

## 运行环境与依赖

- Linux 或 WSL 2
- Python `3.12.x`
- uv `0.12.3`（后端依赖与运行）
- Node.js `22.x`
- pnpm `10.x`；项目锁定版本为 `10.14.0`
- Docker Engine 与 Docker Compose

后端使用 PostgreSQL 16 和 MinIO。前端也可以启用 MSW 浏览器 Mock，在不启动后端的情况下独立运行。

## 后端

### 安装依赖

```bash
cd backend
cp .env.example .env
uv sync --all-groups --frozen
```

`.env.example` 中的 `postgres` 和 `minio` 主机名用于 Docker Compose 容器网络。

### 使用 Docker Compose 运行

在 `backend` 目录执行：

```bash
docker compose up -d postgres minio minio-init
docker compose run --rm api uv run alembic upgrade heads
docker compose up -d api
```

后端地址为 `http://localhost:8000`：

- 健康检查：`http://localhost:8000/healthz`
- 就绪检查：`http://localhost:8000/readyz`
- OpenAPI 文档：`http://localhost:8000/docs`

停止服务：

```bash
docker compose down
```

如需同时删除 PostgreSQL 和 MinIO 的本地数据卷：

```bash
docker compose down -v
```

### 在宿主机运行后端

先启动基础设施：

```bash
cd backend
docker compose up -d postgres minio minio-init
```

编辑 `backend/.env`，将容器网络地址改为宿主机地址：

```dotenv
DATABASE_URL=postgresql+asyncpg://platform:platform@localhost:5432/platform
S3_ENDPOINT_URL=http://localhost:9000
S3_PUBLIC_ENDPOINT=http://localhost:9000
```

然后执行数据库迁移并启动 API：

```bash
uv run alembic upgrade heads
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 前端

### 安装依赖

```bash
cd frontend
pnpm install --frozen-lockfile
cp .env.example .env.local
```

### 使用浏览器 Mock 运行

编辑 `frontend/.env.local`：

```dotenv
VITE_API_BASE_URL=/api/v1
VITE_SSE_BASE_URL=/api/v1/events
VITE_MOCK_MODE=browser
VITE_BUILD_VERSION=web-local
VITE_RELEASE_ENV=local
```

执行 `pnpm dev`，默认访问 `http://localhost:5173`。

### 连接本地后端运行

先启动后端，再编辑 `frontend/.env.local`：

```dotenv
VITE_API_BASE_URL=http://localhost:8000/api/v1
VITE_SSE_BASE_URL=http://localhost:8000/api/v1/events
VITE_MOCK_MODE=off
VITE_BUILD_VERSION=web-local
VITE_RELEASE_ENV=local
```

执行 `pnpm dev`。后端 `.env` 的 `CORS_ALLOWED_ORIGINS` 必须包含前端地址；示例配置已包含 `http://localhost:5173`。

## 构建

构建前端生产产物：

```bash
cd frontend
pnpm build
```

构建结果输出到 `frontend/dist/`。

构建后端容器镜像：

```bash
cd backend
docker compose build api
```

## 验证

后端：

```bash
cd backend
make test
uv run ruff check .
uv run alembic heads
```

前端：

```bash
cd frontend
pnpm typecheck
pnpm test
pnpm lint
pnpm build
```

运行端到端测试前安装 Playwright 浏览器：

```bash
cd frontend
pnpm exec playwright install
pnpm e2e
```
