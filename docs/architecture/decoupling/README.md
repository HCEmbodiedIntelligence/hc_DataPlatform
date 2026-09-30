# 数据平台解耦实施记录

目标仍是审计中的全部模块边界与扩展能力，未缩减为存储重构。逐项状态见
[issues.json](issues.json)；`implemented_verified` 只用于已有实际消费迁移及测试证据的项目。

源基线为茶艺分支 `f9a0804ebd7f5b897acc795a24ce8ca9e69afc5a`，实施分支
`codex/platform-decoupling-20260930`，独立目录
`/home/hc_op/workspace/hc_DataPlatform-decoupling-work`。
源工作区已有迁移工作不动；`activate=False` 和集合版本高水位语义保留。

## 已落地的对象契约

公共接口归 `hc_data_platform.object_storage`，包入口不加载实现。

| 导入模块 | 责任 |
| --- | --- |
| `models` | ObjectMetadata、MultipartPart、CompletedPart、只读 CompletedPartReference |
| `ports` | head、分块读取、范围读取、下载授权、JSON 写入和 multipart 的小接口 |
| `checksums` | 保持原算法的 CRC64/XZ 与 ETag 规范化 |
| `validation` | 分片完整性检查，保持旧 ProblemDetails 错误码 |
| `memory` | 显式导入的内存实现 |
| `providers` | S3/OSS 适配，不引用上传领域模型 |

需要读取的消费者使用 ObjectChunks、ObjectReader 或 BrowsableObjectReader；
标注元数据使用 JsonObjectStore。完整 ObjectStoragePort 暂保留给使用多种能力的上传用例。
尚未全部拆完的领域 sink、原始 SDK 注入及服务端 multipart 编排继续列为待办，不算已完成。

`ingest.ports` / `ingest.adapters` 保留旧导入兼容，应用源码已改用新归属。
HTTP `ingest.models.CompletedPart` 保持原名称与校验；上传用例显式转换到存储值，
LeRobot 完成响应再转换回原 wire 类型。没有变更存储字节、对象路径、数据库或公开 JSON。

## 验证

测试使用现有 `hc-chayi-backend:realsense-2.58.4` 镜像，在无网络、只读源码的临时容器执行。
命令入口为 `/home/hc_op/workspace/diagnostics/platform-decoupling-work/check-backend.sh`。
结果写入同目录 `results/`，不连接现有业务 PostgreSQL/MinIO。

- 修改前的上传、适配器及机器人模型 API 基线：38 passed。
- 存储契约、架构门禁、上传、机器人接入、原始存储、原生上传、媒体及连续录制回归：114 passed。
- 同一对象行为契约覆盖内存和实际本地文件适配器，包含失败后重试、不可变写、abort、范围读取。
- 只有 head/read_chunks 的只读实现可以执行 Manifest 解析；独立进程导入存储契约不加载上传、SDK、数据库或 FastAPI。
- 新存储模块及主要已迁移消费者的 mypy：15 个源码文件通过。
- `python -m hc_data_platform.core.openapi --check` 通过；公开 API 生成文件未改变。
- 导入门禁检查 421 个后端源文件，并通过故意注入反向依赖的失败测试；已接入两个后端 CI 工作流。

以上不代表全仓类型检查、真实云存储或整个架构改造已完成。
扩展检查 publishing/export_assets 时仍有三个类型错误：两处调用旧的无类型注解函数，
以及 raw_source_id 的可空推导。它们涉及后续来源元数据/格式抽取，仍需修复并完成全仓检查。

## 后续实施顺序

继续隔离对象 sink 和 staging，建立可复用的不可变流写入接口；再移除存储对发布 codec 的反向依赖。
并行分工文档中的逻辑版本、任务、规则、模型绑定和前端公共契约尚需逐项落地，
不能把本批对象接口提交当作整个 T0 或 G0 完成。

原始审计和完整任务说明位于 `/home/hc_op/workspace/diagnostics/platform-coupling-20260930/`。
后续扫描使用新输出目录，保留原始审计证据。任何发布、真实资产迁移或机器人操作都不属于此次代码实施。

## 临时产物接口（B11）

`object_storage.artifacts` 提供 ArtifactFileReader、ArtifactReader、ArtifactPublisher、
ArtifactDeleter、ArtifactWorkspace、StagedArtifactStore；媒体只接收文件读取接口。
本地/S3 实现在 `object_storage.artifact_adapters`，TTL 清理策略仍由 workflow 持有。
旧 workflow 导入仅作为外部兼容入口；应用消费者和 runtime 已迁移。

本地发布改用条件创建，避免并发覆盖已有不可变内容；坏摘要不产生可见文件。
S3 流下载、大小或校验失败以及读取方异常均释放临时副本并关闭已打开的流。
新增替换测试证明媒体读取无需发布/删除能力。

验证：46 项对象、媒体、连续处理、缓存测试通过，4 个源文件 mypy 通过。
最初无网络容器中的 7 项 Temporal 工作流测试因无法下载测试服务失败；
随后独立临时容器允许下载服务，7 项全部通过（artifact-workflow.xml）。
没有使用业务 Temporal 或真实对象存储。

## 共享格式契约（B21、B23、E01）

`data_formats` 拥有独立的 JSON、Arrow 类型/值、LeRobot v3 目录/特征命名/任务表、
深度来源声明、物理量化、HEVC 和证据格式，以及本地 RGB/深度媒体检查。
codec 不引用上传、来源适配器、存储、发布业务、工作流、数据库或运行时。
已有结构化错误沿用无框架依赖的 core.errors，保留历史错误码。
同时把 core 的 ModuleRouter 导出改为延迟加载，防止纯契约导入间接加载 FastAPI。

Episode、录制、原生导入、发布和工作流消费者已迁移；原发布路径及旧私有格式函数
仅作为外部兼容导入。Episode 源码不再导入 publishing。发布档案构造也不再
回调 exporter，步骤校验策略归 publishing.source_validation。
持久化版本和导出 revision 仍分别拥有；没有统一两者的 JSON 转义或哈希。

所有格式公开函数补充参数及返回类型，包括明确的 NumPy dtype、深度来源检查结果
和 ROS 图像元数据小接口。存量动态 JSON 元数据继续允许扩展键。
非法媒体缺少 time_base/像素格式时明确失败，不影响正常编码字节。

验证：192 passed，3 skipped（未配置独立 PostgreSQL），15 integration deselected；
18 个格式/发布/核心源码文件 mypy 通过，OpenAPI check、ruff 和格式门禁通过。
相较提交 308dd949，独立进程实际生成的 ZIP 和全部 8 个成员 SHA-256 相同，
深度视频/编码值/profile、两种 JSON 序列化和任务表 metadata 相同。证据：
`results/format-bytes-before.json`、`format-bytes-after.json`、`formats-regression-final.xml`。
比较脚本与旧源码快照位于外部 diagnostics/platform-decoupling-work。

新增的运行时隔离测试检查 codec 导入不会加载任何领域服务、FastAPI、数据库、
存储 SDK 或 Temporal；AST 门禁禁止恢复这些反向依赖。深度禁止重新量化的故障
测试已改为拦截新模块中的实际实现，避免只拦截旧兼容入口而得到虚假通过。

仍未完成：格式能力注册和冻结路由、导出器专属 preflight、流式归档/发布、
逻辑数据版本等后续接口。这些不计入本批完成项。

## 版本化能力与导出预检（X07 进行中）

`capabilities.registry` 仅依赖标准库。ImplementationRef 绑定 key/version；
CapabilityDescriptor 声明 required_inputs/outputs；VersionedRegistry 必须显式指定
默认实现，重复 key/version 或重复默认值失败且不覆盖旧注册。指定版本缺失不自动
回落到当前默认值，输入能力缺失返回完整原因。

生产的 Lance/LeRobot 和两个内存参考导出器已声明 descriptor/preflight，
ExportCoordinator 按能力注册并调用导出器自己的 preflight。只有选择了逐步数据
策略的导出器才收集 Step；替换预检测试证明元数据校验不会读帧或修改存储。
发布审核与源快照策略不变。

验证：108 passed（15 集成测试排除），后续兼容导入调整后的定向测试 50 passed，
8 个源文件 mypy 通过。X07 尚未完成：公开格式枚举、前端发现，以及 durable
workflow 中版本冻结/重试仍须接入，不能把此注册表当作所有新格式均已可用。

## 同步源分支及独立服务复验

已合入源茶艺分支至 e25c0afc 的三个提交（迁移转换、原对象核验、工作版本分支选择），
合并提交 c308ba08；源目录保持不变。架构门禁发现新增 migration_conversion 仍从
publishing 获取格式函数，消费接口已迁移到 data_formats，迁移业务逻辑不改。

新增外部测试入口 `check-services.py`：每次创建独立 internal 网络、tmpfs PostgreSQL/MinIO，
使用工作区的完整迁移清单初始化，仅写合成测试数据，结束后删除创建的容器和网络。
初次执行误用镜像内的旧迁移目录，已显式设置 HC_MIGRATIONS_DIR 修复。
完整 Episode 套件还需要单独启动 HTTP API/Temporal；当前纯服务环境没有它们，
完整套件不能记作通过（correct-schema 记录 60 passed、3 HTTP 连接失败后停止）。

针对合并与 codec 消费的服务复验为 **69 passed、无 skip**：迁移转换/对象核验/工作版本、
提交中断恢复/并发/延迟激活、真实 PostgreSQL 与 MinIO、RGBD 读取、深度窗口和
集合/标注导出。证据 `results/merged-migration-storage-services.xml`。
这补足了前一批仅本地编码回归对真实存取适配的验证，不代表整个应用端到端套件已通过。

## 前端基础身份边界（F01）

`shared/scope/model.ts` 是 Scope/ScopeKey 与作用域键函数的唯一来源；
`shared/auth/contracts.ts` 保存登录身份、项目角色标识与授权快照；
`shared/auth/capabilities.ts` 保存跨领域权限标识目录。角色权限组合策略仍在 entities。
原 entities 路径重导出同一实现和类型，shared 的实际消费者已不再引用 entities。
未更改授权判断、会话存储格式、请求头或 scope 缓存键。

TypeScript AST 门禁（`pnpm check:architecture`）解析静态导入、类型导入、重导出、
require 与动态 import，禁止 shared 引用 entities/features/pages/app，已接入 CI。
运行时测试仍通过旧兼容导入路径，类型检查验证完整调用链；没有复制第二套 Scope。

验证：47 个作用域/会话/权限/HTTP 测试、8 个门禁用例、完整 typecheck、
所改文件 ESLint 和 `pnpm build:container` 均通过。使用现有前端依赖镜像内
Node 22.23.0 / pnpm 10.14.0，未改锁文件。构建仅写本地 dist，未部署。
命令入口：外部 diagnostics/platform-decoupling-work/check-frontend.sh。

页面注册、跨页面复用、显式作用域 client 与 renderer 接口仍列为待办。
