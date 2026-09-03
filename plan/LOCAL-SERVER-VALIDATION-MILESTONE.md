# “本地服务器验证”里程碑

> 状态：规划中，尚未完成。
>
> 开发分支：`codex/local-server-validation`

## 目标

这个里程碑把功能验收统一收口到本地服务器环境。浏览器、命令行工具和测试客户端只连接
本地平台入口，由平台 API 完成身份、作用域、上传、任务创建和结果查询。浏览器、API、Worker
和测试进程都不得访问 OSS；文件正文、元数据、处理产物和读取链路全部在本机完成。

目标链路是：

```text
本地浏览器或脚本
  -> 本地网关 / 平台 API
  -> 本地 PostgreSQL、Temporal 和本地磁盘/存储服务
  -> 本地 Worker 处理
  -> 通过本地 API 和页面检查结果
```

这是一份目标规格，不代表当前代码已经达到该状态。

## 范围边界

本里程碑包括：

- 本地 Compose 能在没有 OSS 凭据且 OSS 网络不可达的情况下启动并通过 readiness；
- 网页和脚本上传只以本地平台 API 为验收入口；
- LeRobot、MCAP、机器人模型与其他需要文件正文的功能能在本地完成上传、处理和读取；
- 本地端到端验证覆盖登录、项目作用域、上传、任务处理、结果浏览和失败恢复；
- GitHub Actions 与本地验证命令保持一致并全部通过；
- 文档明确区分目标状态、当前限制和生产部署要求。

本里程碑不包括：

- 删除生产环境的对象存储抽象、备份、发布或灾备能力；生产能力可以保留，但本地验证路径不能调用它；
- 证明阿里云 OSS 的 CORS、签名 URL、容量或高可用性；
- 用本地验证结果替代生产容量、灾备和多节点高可用证据。

## 当前基线与待解决问题

### P0：本地运行时仍依赖外部 OSS

`compose.dev.yaml` 当前固定设置 `HC_OBJECT_STORE_PROVIDER=oss`，API、Worker 和 Media Worker
仍读取 OSS endpoint、bucket 与凭据。本地 Compose 虽能通过配置渲染，但在无 OSS 配置时不能
形成可通过 readiness 的完整验收环境。

需要提供明确的本地磁盘/本地存储服务实现或本地 Compose overlay，并让默认本地验收命令既不
读取云端凭据，也不调用 OSS endpoint。

### P0：上传客户端仍优先尝试对象存储直传

LeRobot 网页客户端当前保留 `direct` 与 `proxy` 两种传输模式，并优先尝试签名 URL；只有直传
失败后才回退到平台 API 代理上传。里程碑要求本地验收模式从一开始就只连接平台 API，不能以
浏览器访问 OSS 成功作为通过条件。

需要把传输策略变成显式运行时合同，并增加“本地服务器模式绝不请求外部上传 URL”的测试。

### P0：缺少可重复的本地端到端验收

当前单元测试覆盖了上传会话、断点恢复和队列状态，但没有一条无云凭据的本地端到端链路证明：

1. 启动本地依赖；
2. 登录并取得组织、项目和区域作用域；
3. 上传一个最小 LeRobot/MCAP 样例；
4. Worker 完成处理；
5. 页面和 API 能读取结果；
6. 暂停、恢复、刷新和失败重试仍使用同一服务端会话。

### P1：本地配置、健康检查和数据生命周期尚未收口

需要定义本地存储目录或卷、容量上限、清理方式、测试隔离方式，以及 `/health/ready` 对本地存储
的检查。停止环境默认应保留验证数据，显式清理命令才删除本地数据。

### P1：文档仍把 OSS 当成本地验收前置条件

根 README、后端 README 和上传指南仍包含“先配置 OSS”“浏览器直传 OSS”等旧流程。文档需要
保留生产对象存储说明，但本地快速开始、上传验收和故障排查必须指向本地服务器链路。

### P1：CI 基线存在失败

在开始里程碑实现前，当前分支需要恢复以下门禁：

- 前端全量测试；
- 前端类型检查与生产构建；
- Ruff 格式和 lint；
- Mypy；
- 后端全量 Pytest；
- OpenAPI 聚合与目标分支兼容性；
- Compose、Helm 和 Dockerfile 静态检查。

## 完成标准

只有下面所有条件满足后，里程碑才能标记为完成：

- [ ] 清空 OSS 相关环境变量并阻断 OSS 网络后，本地完整栈仍能启动；
- [ ] API、Worker、Temporal、PostgreSQL 和本地存储 readiness 全部通过；
- [ ] 浏览器、API 和 Worker 在完整验收期间都没有向 OSS 域名发送请求；
- [ ] LeRobot 和 MCAP 最小样例能完成上传、处理和结果读取；
- [ ] 机器人模型上传与三维预览使用本地服务器链路通过；
- [ ] 暂停、恢复、刷新、重复提交和失败重试有端到端证据；
- [ ] 前后端全量测试、生产构建、OpenAPI 兼容检查和交付静态检查通过；
- [ ] README 中的本地快速开始可由一台新环境按步骤复现；
- [ ] 生产对象存储、备份和发布文档没有被本地策略误删或误写。

## 验证命令基线

```bash
# 前端
pnpm --dir frontend typecheck
pnpm --dir frontend exec vitest run --reporter=dot
VITE_API_BASE_URL=/api/v1 \
VITE_SSE_BASE_URL=/api/v1/events \
VITE_MOCK_MODE=off \
VITE_BUILD_VERSION=local-validation \
VITE_RELEASE_ENV=production \
pnpm --dir frontend build:container

# 后端
cd backend
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src
uv run pytest --cov=hc_data_platform --cov-report=term-missing
uv run python -m hc_data_platform.core.openapi --check

# 交付合同
cd ..
docker compose -f compose.dev.yaml config --quiet
git diff --check
```

里程碑实现完成后，还必须补充一条启动本地完整栈并执行最小真实上传的自动化命令；在此之前，
单元测试通过不能被当作里程碑完成证据。
