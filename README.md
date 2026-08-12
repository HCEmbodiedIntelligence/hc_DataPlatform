# HC Data Platform Frontend

本仓库当前只实现前端页面与前端工程能力，不包含已经确认的后端业务实现。

## 范围

- P01–P19 页面、路由、交互、权限表现、状态展示和响应式布局。
- 前端 API Client、运行时 Schema、Adapter、Query、Mock、Fixture 和自动化测试。
- `/home/czy/plan/backend/` 中的 OpenAPI 与 Schema 仅表示前端提出的接口需求草案，不代表后端业务、数据库、事务、Worker 或部署方案已经确定。

仓库中的 `backend/` 假实现已移除。在业务需求和后端方案由用户确认前，不得恢复或新增后端实现，也不得把 Mock 流程描述为真实联调完成。

## 开发

```bash
cd frontend
pnpm install --frozen-lockfile
cp .env.example .env.local
pnpm dev
```

本地默认使用浏览器 Mock：

```dotenv
VITE_API_BASE_URL=/api/v1
VITE_SSE_BASE_URL=/api/v1/events
VITE_MOCK_MODE=browser
VITE_BUILD_VERSION=web-local
VITE_RELEASE_ENV=local
```

默认访问 `http://localhost:5173`。

## 验证

```bash
cd frontend
pnpm typecheck
pnpm test
pnpm lint
pnpm build
pnpm e2e
```

## 接入真实后端

只有在业务需求和接口合同经用户确认、真实后端另行实现后，才能将 `VITE_MOCK_MODE` 改为 `off`。真实联调必须单独记录环境、接口版本和验收结果。
