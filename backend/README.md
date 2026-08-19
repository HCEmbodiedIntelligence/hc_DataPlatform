# HC Data Platform 后端

后端采用契约优先的模块化单体架构。领域模块可以选择导出
`hc_data_platform.<module>.router:router`；应用会按模块名称排序并自动发现这些路由，
因此新增 API 时无需编辑共享注册表。

## 本地开发

支持 Python 3.10 和 3.12。依赖解析由 BE-01 负责并提交至 `uv.lock`；其他工作包通过
`docs/dep-requests/BE-*.md` 申请新增依赖。API 和 Worker 镜像会安装 BE-10 所需的
`ffmpeg`/`ffprobe` 运行时，包括 `libx264` 编码器和 HLS 复用器。API 还会安装
`data` 扩展依赖，因为生产环境中的预览、目录和导出路由读取真实的 Lance/Arrow 数据，
而不是使用内存中的契约替身。

BE-12 所需的 OpenTelemetry 发行包、OTLP gRPC 导出器、FastAPI/日志插桩组件和
Prometheus 客户端均从同一个锁文件安装；领域专用的指标发送器仍归各自模块所有。
BE-03 使用的阿里云 OSS V1 官方 SDK 固定在 `storage` 扩展依赖中，并与 S3/MinIO
客户端一同安装到共享镜像。BE-08/BE-09 使用的同步 psycopg 驱动固定在 `database`
扩展依赖中，也会安装到共享镜像。

BE-11 中依赖繁重训练栈的 LeRobot 校验器被隔离在仅支持 Python 3.12 的
`lerobot-v3-validation` 扩展依赖中，不会安装到 API 镜像、Worker 镜像、常规本地环境
或常规 CI 中。它只用于显式请求的独立消费者兼容性测试。专用 `worker` 镜像会安装
`data` 扩展依赖，并在构建时验证 Arrow、MCAP 和 Lance 能否导入。后端自身的
LeRobot v3 导出器使用 PyArrow 和 Lance，不会导入 LeRobot 官方包。

```bash
uv sync --frozen --extra dev --extra database --extra workflow --extra storage --extra data
uv run uvicorn hc_data_platform.core.app:create_app --factory --reload
```

也可以启动 API 及其所需的全部本地依赖（PostgreSQL、Temporal 和 MinIO）：

```bash
make up
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
```

`/health/live` 只能证明 API 进程能够响应 HTTP 请求。`/health/ready` 会分别探测
PostgreSQL、Temporal 和配置的对象存储桶。任一探测失败时，它会返回 HTTP 503，
并给出各依赖的 `ready`/`not_ready` 状态及安全的失败详情。

API 启动前，Compose 会在 PostgreSQL 咨询锁保护下，按顺序且仅执行一次
`migrations/manifest.txt`。已应用迁移的校验和记录在 `core.schema_migrations` 中；
如果数据库契约与镜像不一致，启动就绪状态将保持为 false。需要覆盖本地默认值时，
请将 `.env.example` 复制为 `.env`。预发布和生产环境会拒绝文档中的本地游标密钥、
对象存储密钥、占位 JWT 颁发者、关闭迁移强制检查的配置，以及缺失的 JWKS URL/签名密钥。

Compose 使用一个明确仅限本地的 HS256 签名密钥，以便在没有外部身份提供方时测试受保护路由。
在所有共享环境中，都必须改用 HTTPS JWKS 端点和 RS256；该签名密钥绝不能在本地开发以外复用。

### 对象存储内外端点与浏览器上传

`HC_OBJECT_STORE_ENDPOINT` 是 API、Worker、Lance、readiness 以及所有对象读写使用的服务端
内部端点；在 Compose 中它是 `http://minio:9000`。`HC_OBJECT_STORE_PUBLIC_ENDPOINT` 只用于
由独立的 path-style SigV4 client 生成浏览器 multipart part PUT URL，本地值是宿主可达的
`http://127.0.0.1:9000`。公开端点完全由服务器配置决定，不能从请求头推导，也不能在签名后
替换 URL 主机名；对象存储凭据不会返回浏览器。

本地 public endpoint 由 `object-store-browser` 直接流式转发到 MinIO，并保留签名所绑定的
原始 `Host`。它只回答 exact-origin 的 PUT/HEAD 预检、只允许实际的 `content-type` 请求头、
暴露 `ETag`，并移除 community MinIO 全局 CORS fallback 自动产生的 credentials 响应头。
MinIO 的宿主管理/集成端口是 `127.0.0.1:19000`；容器内服务仍只用 `minio:9000`。

staging/production 必须显式配置浏览器端点为 HTTPS 公网 FQDN，不能使用 localhost、loopback、
容器服务名或裸主机名。生产对象存储的基础设施 owner 还必须在 bucket 上配置与平台实际域名
一致的 exact CORS allowlist：仅开放浏览器上传所需的 `PUT`/`HEAD` 和实际请求 headers，暴露
`ETag`，且不得使用 `AllowedOrigin=*` 或启用 credentials。Helm 只注入端点合同，不会假装管理
外部 S3 vendor 的 bucket CORS。

## 契约与质量门禁

修改任何 OpenAPI 片段后，重新生成稳定的聚合文件：

```bash
make openapi
make openapi-check
uv run hc-openapi --baseline path/to/previous/openapi.generated.yaml --check
```

前端正式类型必须来自 production-composed runtime schema，而不是直接来自 fragment aggregate：

```bash
uv run hc-openapi --runtime --output /tmp/hc-runtime-openapi.yaml
cd ../frontend && pnpm gen:api --check
```

聚合过程按文件名排序，会拒绝重复的 YAML 键、路径和组件名称，并保留所有组件区段
（包括共享参数）。片段级的 `security` 默认值会落实到该片段的各个操作上，同时保留显式的
操作级覆盖。基线检查会拒绝破坏兼容性的路由、操作标识/认证、响应和模式变更。
CI 会将拉取请求与目标分支中已提交的聚合文件进行比较。

运行 `make check` 可执行与 CI 相同的本地门禁；构建验证使用 `make compose-config`
和 `make build`。`make build-api` 与 `make build-worker` 分别构建两个镜像目标；
API 仍是 Dockerfile 默认的最终目标。

本地技术栈由仓库根目录的 `compose.dev.yaml` 定义，而不是后端私有的 Compose 文件。
`make compose-config`、`make up` 和 `make down` 通过 `COMPOSE_FILE` 指向该文件；
`make up` 只启动后端依赖、API 和 Worker。如还需要前端和网关，请从仓库根目录运行
`docker compose -f ../compose.dev.yaml up --build`。

## 归属范围

权威的模块归属、合并边界和验收标准记录在
`../plan/BACKEND-DATA-PIPELINE-IMPLEMENTATION-PLAN.md` 中。
