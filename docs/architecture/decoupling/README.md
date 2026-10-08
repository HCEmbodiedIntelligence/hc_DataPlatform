# 数据平台解耦实施记录

本轮 A/B 已按 2026-10-08 新指令恢复持续实施，原暂停记录仅为历史。
当前逐项状态以 issues.json 为准；本地 main 第一批集成见
[two-terminal-functions-20261008.md](two-terminal-functions-20261008.md)。

## 2026-10-08 生产调用入口修复

合并后的检查发现，43 处前端引用仍经过旧身份或页面兼容入口，
机器人、模型及数据源页面也仍从隐式会话 API 入口获取能力；
7 个后端工具仍导入旧上传存储或发布格式路径。
这些消费者已直接迁到 shared 身份契约、所属 feature、显式组织 hooks/client、
object_storage.providers 和 data_formats。兼容文件继续供历史调用者与兼容测试使用，
已迁移能力的生产消费者不再依赖这些入口。

数据源弹窗新增必需的 organizationId 输入；弹窗和机器人身份面板使用父页面提供的
组织，模型与实例页面将选定组织显式传给全部查询和修改接口。
已验证组织切换与 null 输入均传给新 hooks，不在 feature 客户端重新选择当前组织。

前端门禁新增兼容入口检查，覆盖 app/entities/mocks，并禁止同一页面继续通过
自己的迁移兼容文件获取业务能力。后端门禁扩大到 tools/scripts，
同时禁止通过 ingest 包入口回读旧存储实现；上传 HTTP DTO 的公共入口保持可用。
实际主入口核对确认，API/worker 的对象存储、发布事务、版本化导出和 renderer 注册
使用已有新实现；新摄取任务显式选择 Episode，旧工作流的 Lance 默认值用于历史兼容。

验证：前端 159 文件 / 990 测试、54 项架构检查、完整类型检查、生产构建与修改文件 ESLint；
后端格式/存储/架构回归 49 项、机器人/模型实际 HTTP 契约 16 项通过。
迁移批次 CLI 在独立 PostgreSQL/MinIO 中完成发布、回滚和保留下载验证，1 项通过。
后端门禁在本次修复基线上检查 500 个源码与工具文件，修改文件 Ruff 通过。
合入期间主分支新增集合发布提交 `662ce5da`，已保留并整合；整合后门禁检查
502 个文件，类型检查、54 项前端架构检查、167 项集合/数据集/导出相关前端回归、
18 项真实数据库/对象存储/HTTP 服务回归，以及运行时 OpenAPI 与生成客户端一致性检查通过。
逐次证据保存在外部 results/canonical-*；此次只修复已迁移能力的消费入口，
不将其余架构审计项记为完成。

## 2026-10-08 合并与暂停

用户要求先合并到 `main`，暂停后续重构。此次仅整合已提交的解耦成果
`5cec0214`、茶艺分支历史标注迁移 `cb00f3e7` 和原主分支 `89df980d`。
未提交的 URDF 导入抽取与机器人场景来源工作保留在原独立工作区；
茶艺工作区中的未提交集合发布工作也不包含在此次合并中。

合并保留原生 Episode 路径与主分支并行准备、版本提交保护、HEVC 帧解码和边界裁剪。
旧导出任务仍精确路由到 v4，新导出默认使用 v5；旧 QC 引擎的规则证据与哈希保持原值，
v3 引擎额外记录边界信息。前端历史标注迁移能力归入已有 feature，页面兼容入口保留。

合并验证包括前端 159 文件 / 989 测试、46 项前端架构检查、完整类型检查和生产构建；
后端常规回归 341 项通过、Temporal 工作流及历史重放 24 项通过，
后端架构门禁检查 461 个源文件，OpenAPI 聚合与运行时契约生成检查通过。
独立 PostgreSQL、MinIO、Temporal 和 HTTP 服务累计 60 项不同用例通过；
旧数据库测试夹具补齐区域和标注提交记录，以满足新增不可变标注文件约束。
数据库、对象存储与 HTTP 验证的逐次结果保存在外部
`diagnostics/platform-decoupling-work/results/merge-*.xml` 与日志中。
使用独立临时服务，不部署、不迁移业务数据。

全量审计状态仍为 15 项 `implemented_verified`、5 项 `in_progress`、40 项 `pending`。
以下实施记录与后续顺序作为恢复工作时的上下文保留；当前没有继续执行后续重构。

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


## 开放导出格式与固定执行版本（X07）

发布版本导出接口现在接受有长度和字符限制的格式标识。`ExportFormat` 仅保留为旧 Python
调用方的兼容枚举，不再限定 HTTP、任务或前端可接受的格式集合。
`GET /api/v1/datasets/export-formats?project_id=...` 在 export.read 授权后返回默认实现、显示名、
输入要求和缺失能力；P07/P21 使用同一 feature 查询，目录失败或无可用能力时禁止新建。
历史格式即使已从目录撤下，已有任务仍可查询和授权下载。文件名按产物 MIME 类型处理。

新增实现需提供 `ExporterPort` 的 descriptor、presentation、format、preflight 和 export，
然后在应用 composition 中传入 ExportCoordinator。需要保留旧实现时同时注册多个版本，
通过 `defaults={format_key: version}` 显式选择新任务的默认值；不要修改页面或中央格式枚举。
每个实现自己选择所需输入和预检策略；格式目录的 available 表示服务具备输入能力，
实际快照是否满足格式要求仍由该实现预检判断。改变输出字节的实现须使用新的版本标识，
并遵守 artifact sink 的不可变对象协议，不能覆盖同路径旧产物。

新建任务先解析选定实现，保存 key/version 到任务输入和 result.export_implementation；
Temporal preflight/materialize 与显式重试均使用精确版本。未安装实现返回 503，且不创建任务；
已安装但暂时执行失败仍保留可重试的失败任务。旧任务无该字段时仅映射到两个历史版本，
不会使用变化后的默认实现。未固定版本的历史输入序列化保持旧 JSON 形状；提交 76150bb5
实际运行产生的历史保存在 tests/workflow/fixtures/export-workflow-legacy-v1.json 并通过新代码回放。
实现返回错误的 project/dataset/version/format/attempt/hash 时，协调器拒绝该结果。

第三种 metadata_json 测试实现完成了目录发现、创建、查询、下载和版本升级后的重试，
不读取 Step、媒体或机器人实例。前端测试从服务端接收新格式和缺失能力并执行真实页面操作。
这证明发布导出扩展接口可用，不代表全链路无本体数据、Episode 交付和训练能力已完成。

验证：后端回归 131 passed、8 skipped（独立 PostgreSQL/MinIO 验证另列）；
Temporal 新版本路由测试 5 passed，旧历史回放和输入兼容 4 passed，已有真实提交/导出执行 1 passed。
前端 19 tests、完整 typecheck、ESLint、生产构建通过，正式 OpenAPI 与 runtime 对齐后重新生成客户端。
8 个发布/注册源码文件 mypy 通过；workflow 三个文件的 36 个历史错误与改造前一致，仍需后续治理。
相较旧 codec 基线，实际 ZIP/8 个成员、深度视频/编码值/profile、JSON、任务表摘要逐项相同。
证据位于 results/export-extension-regression-final.xml、export-temporal-versions.xml、
export-legacy-replay.xml、export-legacy-execution.xml、export-frontend-final.log、export-format-bytes.json。


## 清洗查询契约归属（F02）

`CleaningDraftsSearch`、查询编解码和投影链接由 features/cleaning/query-codec.ts 拥有，
清洗 API hooks 与 P10 页面直接消费同一实现。旧页面路径保留重导出，既有 URL 测试继续通过。
AST 门禁扩展为禁止 feature 从 pages 导入类型、函数或动态模块，避免新页面再次成为底层契约来源。
验证：11 个边界测试、6 个现有清洗 query/routing/command 测试、完整 typecheck 和修改文件 ESLint 通过。

已合入源分支 e1da84fd（迁移批次的原子发布/回滚），合并提交 97dc35a8；原源工作区未改。
首次独立服务复验为 22 passed、3 failed、2 fixture errors：两项迁移测试硬编码 /app/.g1-artifacts
及 /app/tools，测试容器补充独立 tmpfs 和当前工具挂载后重跑；三项 publication lineage 测试
暴露发布仓储直接依赖 continuous_recordings 表的历史耦合，后续 B19/B20 治理须处理。
这些失败没有记为已通过，详细证据 export-migration-service-validation.xml。


修正独立测试容器挂载后，迁移批次 19 项与发布审计/MinIO/通知 4 项共 **23 passed、无 skip**。
覆盖原子发布/回滚、中断恢复、并发、作用域隔离、对象损坏、实际 CLI 和历史下载；
服务和产物均位于临时容器中，已清理。证据 export-migration-services-final.xml。
publication lineage 的三项历史依赖失败仍保留，未用跳过或 mock 将其记成通过。


## 发布事务中的模块职责（B19）

`publishing.postgres` 现在只保存发布 Manifest、发布来源关系与标注定版事实，
不直接操作 dataset_registry/account_notifications，也不导入连续录制实现。
`PublicationSideEffects` 明确 prepare_sources、project_release、notify_owner 三个阶段；
领域端口不传游标，不提交事务、不发送外部消息。各 PostgreSQL 适配器由 application
integration 在同一游标上装配，最后仍由发布仓储提交或回滚，维护 fencing 语义。

- dataset_registry.publication：DatasetReleaseProjection、DatasetReleaseProjectionPort、DatasetOwnerLookup。
- dataset_registry.publication_postgres：目录版本、当前指针、内容与子项复制、精确作用域的 owner 查询。
- continuous_recordings.publication/lineage：RecordingPublicationLineage 与相同事务中的具体来源准备。
- security.notification_writer/notification_postgres：账户通知命令、去重写入及原 subject 恢复。
- integrations.publication：跨域请求转换与事务装配，保持已有发布 ID、事件 key、内容摘要和旧错误详情。

直接构造 PostgresPublishedManifestRepository 需显式传入 side_effects：生产 runtime 使用
postgres_publication_effects；独立发布模块使用 publication_only。没有默认暗中装配其他模块，
也没有依赖缺表异常来决定是否发布。原 HTTP 和 runtime OpenAPI 完全相同。
目录 projection 内原缺表兼容检查仍保留；完整应用使用已安装 schema，后续清理按数据目录阶段处理。

真实 PostgreSQL **9 项全通过**：旧三项 lineage 失败已修复；发布/目录/通知重复调用只写一次；
准备、投影或通知发生故障后，目录子项、当前版本指针、发布与通知均回滚，重试正常。
子项测试验证内容快照保持不变且版本/Manifest 引用正确更新。新增端口替换与反向依赖门禁通过，
总回归 **172 passed、24 integration deselected**，参与模块 9 个源码文件 mypy 通过，ruff 通过。
连续录制既有冲突分支缺失 ProblemDetails.detail 导致 TypeError 的问题一并修复，保留原错误码，
并验证歧义来源、错误机器人和不可变身份冲突均在写入前返回 409。
证据：results/publication-transactions-atomic.xml、publication-port-contracts.xml、publication-domain-regression.xml。

B20 仍在进行：目录准备、来源关系及其他模块中的跨域读取没有全部改造；这次不把它记为完成。


## 页面契约与应用装配（F03、F07）

`shared/routing/page-descriptor.ts` 提供 PageDescriptor/PageRoute、definePage、buildPageCatalog。
每个页面在自己的 routes.tsx 声明 id、enabled、URL、requiredCapabilities、lazy 与可选导航信息；
app/router/page-catalog.ts 自动发现声明，生成 32 个实际可用的页面路由、菜单、可用性与返回 URL 清单。
不再按 Pxx 文件名猜测权限，不再维护 pageReadCapability、lazyIngestPages 或第二份页面菜单清单。
新页面只增加自己的声明和组件；分组名称和排序规则是共享 UI 政策，不含页面实现。

详情页显式选择导航归属：数据集详情/版本/回放属于数据集，上传详情属于上传，
清洗草稿/工作台属于标注；机器人兼容入口仍归机器人模型。标定页与 schema 编辑页仍禁用。
不存在的 P10Q 菜单项和返回 URL 不再被静态清单误认为可用。替换目录时会撤销已禁用的返回路径。
路由路径、查询构造、懒加载组件与原有授权要求保持兼容；普通菜单仍可发现，实际路由检查权限。
P18 明确保留页面内的 project access.read / global platform.account.read 两组授权边界。
全局权限词汇独立于项目角色能力清单，项目管理员不会因此获得平台管理员权限。
缺失/未知权限、重复页面、歧义路径和缺失导航归属会使注册失败；未知权限也不能被管理员绕过。

ToastProvider/useToast 由 shared/ui 拥有同一个上下文，保留原敏感信息过滤与 Ant 通知行为；
应用只装配。上传/标注 URL 常量属于 shared/routing；标注 URL 构造器及查询编解码属于
features/annotation。页面和跨页消费者直接依赖这些能力，旧导入入口保留重导出。
页面不再导入 app；业务页面不能导入组件路由文件来构造链接。测试与 testing/ 视觉 fixture
可显式装配应用测试 harness，其余生产入口由 TS AST 门禁约束。

验证：18 个架构门禁测试，36 个文件中的 **297 项** 回归通过（应用壳、授权、作用域、HTTP、
标注、采集任务、切片、账户设置与存储路由）；目录替换补充后相关 **28 项** 再次通过。
新增页面用真实 MemoryRouter 渲染：一次声明即可访问并生成一致菜单，无 grant 时组件不执行。
完整 typecheck、62 个源码/脚本文件 ESLint、生产构建通过。构建仍有原大 chunk 提示，
UI 测试有现存 flushSync/hydration 提示，均未改为跳过。日志位于 results/frontend-pages-*.log。
F04 只完成标注链接/查询能力的跨页迁移，其余页面间 API、组件、媒体和上传处理复用继续治理；
API 校验生成与业务请求显式 scope（F06/F09）仍未完成。


已同步源分支 dc2850d7（历史原生 RGB 迁移准备），使用独立合并提交保留来源历史。
新 migration_native.py 中 inspect_video/feature_names 改用 data_formats 公共接口，
修复边界门禁发现的两条存储→发布反向依赖；源分支工作区未修改。
独立临时 PostgreSQL/MinIO 的 native migration / object verification **54 项通过，无 skip**，
Ruff 与 452 个 Python 源文件的架构门禁通过。证据 results/native-migration-integration.xml。
本轮没有重跑源分支文档中的真实历史副本迁移，也没有据其报告宣称完成现网迁移。
页面注册另增加应用根路径/登录入口的保留路径校验，避免新增页面覆盖应用入口；相关 28 项复验通过。


## 跨页 API、组件与样式复用（F04）

生产页面中的跨页导入已经清零，包括 type、动态组件、路由组件复用和 CSS。
共享业务能力由 feature 拥有；应用壳也不再通过上传页取得队列状态、通过 auth 页取得会话接口。
32 个原有能力文件迁移后，全部生产消费方直接引用新所属模块；旧 TS 路径保留重导出，
旧队列入口与新入口使用同一个 Zustand 实例。组件本身的业务 API、请求、错误码与缓存 key 保持兼容。

- features/ingest/upload：上传契约、正式/原生传输 API、队列与相关上传样式。
- features/ingest/processing、browser：处理记录、目标选择、标签配置、处理路由及原始数据浏览。
- features/ingest/detail：上传详情 API 和 UploadDetailView；视图显式接收 uploadId/pathname/returnTo，
  P04 与问题诊断各用自己的薄路由适配器，后者不再懒加载另一个页面。
- features/datasets/episodes：Episode 详情、数据审核、完整交付、纳秒步骤映射、媒体授权适配和展示语义。
  错误构造独立于步骤读取；旧 p06DataError 名称只在旧页面兼容入口映射至 datasetDataError。
- features/datasets/components/DatasetTables：版本/Episode/来源表及分页组件。
  表格只带自身的四类样式，数据集页继续负责工作区布局，P05/P06 共享表格能力。
- features/annotation/runtime：现有标注运行时、包绑定校验和交付 API；features/exports 提供批量交付 API。
- features/collection-tasks：CollectionTaskGateway 及类型，上传/数据集/导出页使用相同接口。
- features/account/session：登录/会话/作用域装配及错误文案，auth、账户设置、平台设置与 shell 共同消费。
- shared/ui/workspace.module.css：原型页中被多处使用的通用工作区样式归公共 UI。

AST 门禁拒绝任何生产页面导入另一个页面的 API、类型、组件、重导出或样式；
feature 不能从页面或 app 获得实现。测试、testing/ 视觉 fixture 的跨页样例共享保持显式例外。
24 项架构测试通过，包含 type/dynamic/CSS/reexport 负例；完整 frontend typecheck 通过。
172 个修改/新增 TS/脚本文件 ESLint 与生产构建通过。

第一次全量回归为 918 passed、2 failed，改造前 88bfc33b 的两个对应测试也同样失败。
P04 的隔离测试缺少已启用路由目录，现以实际目录初始化并用真正的问题诊断路由适配器测试，
过滤后的列表返回地址得以验证。抽屉测试改为验证 Drawer 的实际容器聚焦政策，继续检验
Tab 到关闭按钮、Escape 关闭、恢复触发器焦点以及移动端不自动聚焦文本输入。
两项定向复验通过，最终 **153 个文件、920 项测试全通过，无 skip**。
初次失败、基线复验、最终全量、ESLint 与构建日志均保留于 results/frontend-features-*.log；
32 文件的所有权映射为 results/frontend-feature-relocations.json。

F05 仍在进行：物理格式兼容逻辑已进入业务适配器，但统一的后端逻辑数据视图与开放格式协议尚未完成。
以上为 F04 批次的边界：当时 F09 尚未实施，后续实现见下节。
F06/F08、机器人后端和训练边界继续按原审计推进。

## 显式作用域客户端、缓存与会话生命周期（F09）

机器人和模型能力分别拆为 api/client.ts（显式 organizationId、wire 校验与转换）、
api/hooks.ts（显式组织查询/修改）及 api/index.ts（旧会话 UI 适配与兼容导出）。
纯客户端不导入 React、Query hooks、shell-store 或另一 feature 的会话 API 入口。
旧 getRobotBootstrap(robotId) / loadRobotBindingTarget(target) 只在兼容入口保留；
生产纯调用及 Viewer/P23 回放使用 client.ts 和显式 hooks。
机器人实例列表的批量详情查询也进入同一显式 hooks，其缓存不再遗漏组织。

useEpisodeRobotScene 收到的组织控制实例 Bootstrap、版本、资产、关节映射和签名授权；
null 作用域停用查询，不回退到 shell 当前组织。两个组织即使使用相同 robot/version ID，
也能在同一 QueryClient 中形成各自场景，切换页面选择不改变场景所属组织。
响应的组织须与请求匹配，结构正确但组织错误的响应按 CONTRACT_MISMATCH 拒绝。

shared/api/scoped-query-keys.ts 是不读取会话的纯 key 契约；旧 makeQueryKey 留在选择适配器。
useScopedQuery/useScopedMutation 为显式工作台提供查询与修改接口：key 同时含显式 scope、
资源身份、本地会话序号与权限版本，不含 token 或签名 URL。修改在调用时冻结 scope，
成功后只写入该作用域缓存；同一会话权限版本变化会刷新该作用域的最新查询。
授权被撤销时立即隔离旧缓存；新登录会话不能读取前一会话缓存，也不能执行旧回调或排队修改。

request 的 scopeMode="explicit" 要求实际会话授权，不将当前选中项目当授权证据。
显式请求在项目选择切换期间保持独立，仍遵守调用方 signal、登录失效、组织/项目/区域授权撤销，
并在返回头和响应体读取后再次检查。会话或授权变化会主动 abort，请求结束会移除监听。
createSessionScopeLease 将多请求操作及外部签名传输绑定到同一个会话，URDF 分片上传已消费，
登录变化或 owner 取消后不会继续授权、上传分片或借新会话完成旧文件。

shell 将 clearActiveScopeState 与 clearSensitiveState 分开：页面切换保留会话授权清单，
退出/过期仍清除所有敏感状态。scope-transaction 与页面权限守卫只取消/删除页面所属查询，
显式工作台查询以 scopeBinding="explicit" 声明独立生命周期；Viewer 资源由各实例 registry 管理。
现有 active/organization/session/public 请求模式保持原行为，业务页面可以继续使用当前选择适配器。

requestWithResponse 提供受相同保护的 data/status/headers；采集任务详情不再复制 fetch、token、
租户头和失效判断，继续使用 active 模式及相同 ETag 缺失错误。
其他业务的逻辑数据接口与 wire 生成仍归 F05/F06，不能把此入口当作其全部完成。

核对后端实际契约后，模型请求不再夹带当前页面的项目/区域头。
registry.resolve_registry_project 从已验证会话推导授权/审计项目，资源身份仍是组织和模型版本；
response.scope.project_id 保留，不被前端误当成所选页面项目。
使用实际 FastAPI 路由和生产 _request_context 复验两个组织的相同版本 ID：只传组织头可分别取得
各自模型，项目授权与 robot_model.read 缺失仍返回 403。没有改动后端权限、公开 API 或资源身份。

验证证据位于 results/frontend-scope-*.log / frontend-scope-backend-http.xml：
双组织真实客户端回放、空 scope、错误组织响应、权限撤销、慢响应体、独立取消、登录替换、
修改缓存归属、权限版本刷新及完整 URDF 分片流程均有回归。
完整前端 **157 个文件、954 项测试通过，无 skip**（最终采用 maxWorkers=4）。
39 项 AST 门禁、完整 typecheck、34 个修改前端源码/脚本 ESLint 和生产构建通过。
曾在测试与构建并行时遇到一项导出对话框等待超时；对应文件单独 11 项通过，
随后降低 worker 并发重跑所有测试通过，保留 concurrent-build / export-rerun 日志，未跳过或延长测试等待。
后端模型/机器人 HTTP 与契约回归 **16 项通过，无 skip**，对应测试 Ruff 通过。
原 React/JSDOM/大 chunk 提示仍保留；此次没有重跑真实云服务或将后端历史模型冻结（R04）记为完成。

仍待处理：URDF 导入用例下沉、冻结 embodiment、来源 provenance、renderer 能力注册，
以及原审计中的数据格式、任务、QC、训练、逻辑版本和其余存储/后端边界。

## 原生 RGB 窗口与旧 ROS 图像修复同步

仅合入茶艺分支已提交的 `01d3cde6`、`ce580918`，没有复制或覆盖源工作区正在进行的
标注迁移和前端未提交改动。保留共享视频窗口的原帧引用、无损 RGB 选帧、冻结回执复核，
以及旧 ROS Image/CompressedImage 名称和显式 PNG 深度声明的修复。

新增 RGB 迁移最初再次引用 publishing 检查工具和 continuous_recordings 的 TrainingVideo。
现在编码器归 `data_formats.rgb_video.RgbVideoWriter`，迁移、MCAP 包、连续录制包和
原始录制预处理直接使用公共格式层；原 TrainingVideo 路径只重导出相同类供旧调用者兼容。
FrameReader 仍属于原始录制来源适配器，没有被当作通用格式能力搬迁。门禁拒绝恢复这些反向依赖。

实际验证：178 项格式、迁移、像素、深度、来源和架构测试通过；隔离 PostgreSQL/MinIO 的
57 个不同服务用例全部最终通过，包含临时 Temporal 的 MCAP/连续录制工作流、重试及历史 replay，
没有跳过测试。最初 3 项因环境缺少 Temporal 变量失败，补充临时服务后 1 项还缺证据输出变量；
补充证据路径后该项也通过，原始失败日志和各次 JUnit 保留，不把失败记为产品缺陷或静默删除。
4 组真实 RGB 编码对比覆盖普通/无损编码和整数/分数 FPS：产物 SHA-256、大小、帧时间与源提交
完全相同，无损解码 RGB 像素一致；新公共编码器 mypy、全部相关 Ruff 和 454 文件依赖门禁通过。
证据位于外部 results/rgb-migration-*.xml、rgb-codec-compatibility.json。
源提交自带的历史数据演练诊断按原证据合入；本批独立验证使用合成数据，没有重新操作真实历史资产。

## 可视化能力注册（F08）

`features/viewer/renderer-contract.ts` 定义 ViewerRendererDescriptor、ViewerRendererRef、
ViewerRendererRegistration、ViewerRendererContext 和窄资源端口 ViewerRendererResources。
renderer-catalog 只选能力，不读取 session、页面、API 客户端，不加载 renderer 代码。
注册时复制并冻结模态/schema/版本/输入声明；同 id/version 或同 id 多个默认值拒绝覆盖。
指定引用 get(ref) 只读精确版本。路由只选显式默认版本：模态与 schema 版本一致且满足输入能力
时才可用，优先级数值较小者优先，同优先级匹配明确降级并诊断。

已有 RGB、深度、点云、关节/数值与事件视图全部通过 builtin-renderers 声明，保留原来的
顺序、Manifest 相机槽顺序、轴卡片及关节原始曲线回退。没有注册的新模态/schema 继续显示
unsupported；缺输入和歧义只停用对应面板。ViewerCompositionResolver 不再解释模态，门禁
同时禁止恢复中央模态映射和 registry 对 UI/运行时装配的依赖。

新模态由所属 feature 声明一个 Registration 和懒加载 Component，工作台组合为
`createViewerRendererCatalog([...builtinViewerRenderers, newRenderer])`。
将同一个不可变 catalog 传给 EpisodeWorkbenchCore/ViewerMediaSurface 的 rendererCatalog，
或 DataVisualizationWorkbench 的 slots.rendererCatalog；无需修改核心工作台、时钟或
创建另一套 viewer。原 renderPanel 插槽和 robotScene 可选性继续保留。
模块导出 Component(props: ViewerRendererContext)，输入来自显式 stream/schema、clock；
只有可见面板才调用 load(signal)。来源读取使用 context.signal；payload/URL/worker 等释放
责任通过 resources 端口登记；异步读取失败通过 reportError 走公共面板错误路径。
资源端口不依赖具体 registry 类，也不允许 renderer 关闭其他面板或共享 clock。
授权媒体 kind 在通用 viewer 端口允许扩展，已有后端媒体 wire 的验证和权限检查仍由来源适配器负责。

加载/组件初始化/异步来源错误在当前面板隔离并可重试；隐藏、切换 stream/catalog/clock、
卸载与失败均 abort/dispose。过期 loader 不挂载组件，迟到 payload 注册立即释放。
能力选择不会发出授权请求。真实懒加载字幕测试模块复用相同数据源与 clock，无需 robot；
同时测试 audio media 能力的选择、不提前授权及缺输入拒绝，未宣称后端已支持新音频来源。

验证证据位于外部 results/viewer-renderers-*：完整前端测试、类型检查、ESLint、46 项
架构反例及生产构建。首次生产构建命令缺少明确的生产环境变量而被原安全检查拒绝；
补齐 off/production/不可变验证版本及 API/SSE 根路径后按原 build:container 通过，
没有放宽构建检查，也没有部署产物。
这仅完成 renderer 扩展接口；逻辑数据快照、标注 target、URDF 导入和历史冻结模型仍是独立待办。

F08 最终完整回归：159 个文件、976 项测试通过且无跳过，其中新增注册/生命周期/错误用例 22 项。
完整类型、受影响 ESLint、46 项门禁与生产构建通过。当前 15 项 implemented_verified、
5 项 in_progress、40 项 pending，全部 60 项目标仍保持 active。

## 2026-10-08 服务端不可变流写入（E07）

`object_storage.ports.ImmutableObjectWriter.write_immutable(key, source, *, size, sha256,
checkpoint=None, progress=None) -> VerifiedObject` 消费调用方持有的文件流；不关闭输入。
`MultipartImmutableWriter` 每次最多读 1 MiB，分片超过 8 MiB 后落临时文件，
在条件提交之前验证输入长度/摘要，提交后完整读回。已有同内容对象幂等复用，
不同内容不覆盖；失败/取消清理本次分片，已提交对象不删除。并发胜者及丢失
成功响应都以实际对象核验恢复。读取失败或取消显式关闭 provider 迭代器。

实际 EpisodePackageService 为提交与完整/集合交付注入同一个 writer。原业务
模块不再编排 multipart，保留回执、租约、取消、进度、延迟激活及事务职责；
架构门禁拒绝在两个生产消费者重新引入 multipart。65 项对象/架构检查、4 文件
mypy 通过。临时 PG/MinIO/API/持久 worker 回归覆盖 53 项：首次 52 通过，
一项因错误措辞变化失败；恢复原 wrong-size 描述后该真实对象损坏用例通过。
原失败 XML、修复复验 XML 和临时服务日志全部保留在本轮协调目录。

范围仅为 E07；SDK 注入统一、公共导出整包 bytes 和样本批量读取等仍待实施。
没有将原 48088 服务重启，也没有改原工作树或真实业务数据。

## Episode 基础契约及应用实例依赖

PackageConflict、assert_scope、collection_scope 归属 errors/scope，所有生产读取者
和迁移 CLI 消费新路径；旧 Postgres/collections 名称重导出同一对象。纯导入不加载
Postgres、集合实现、服务或 FastAPI。E04 的基础规则不再由数据库适配器拥有。

Episode 父路由和两个子路由通过同一显式依赖读取 app.state.episode_packages。
create_app 绑定并拥有关闭职责，构造另一个 app 不再替换或关闭原 app 的服务；
未配置实例返回 503，仍先保持原授权/租户检查。没有恢复进程全局回退。
其他领域全局服务及 Episode 内部服务循环仍未完成，B13/E08 仅记进行中。

35 项契约/架构用例、34 项真实 PG/MinIO/API/持久 worker 用例通过，无跳过；
3 个新模块 mypy、全部修改 Python Ruff 通过。实际 runtime OpenAPI 字节/哈希
与 12c5a744 完全相同。初次门禁发现三个迁移 CLI 的旧错误导入，已迁移并复验；
首次失败 XML 仍保留。OpenAPI 检查容器使用独立可写暂存路径，未改生产校验。

## 历史 QC 规则集合冻结（X04）

qc-evidence.v1 注册 be06-qc/1、/2 的 17 条历史规则与 /3 的 20 条规则，
不再通过当前 QualityCode 枚举确定旧证据 required set。新 evidence/engine
必须显式注册版本；未知历史 engine 仍保留原来的 unsupported/skip 证据语义，
不把它路由到任意新引擎。固定版本 QC 页面也只显示该版本规则及实际已有 finding。

改动前从稳定候选 3df9fc78 冻结的三个 JSON/哈希作为 golden；扩展当前枚举后
旧 JSON 原样验证，原引擎重跑字节、报告和证据哈希完全一致。35 项规则/契约
测试、7 项真实 PG/MinIO/API/持久 worker 测试、Ruff 和新模块 mypy 通过；
服务包含固定版本 HTTP、旧缺失证据、租户隔离与 Tag 独立 QC。证据位于协调目录
qc-archived-rule-sets.xml、qc-rule-set-services.xml。X03 单规则执行插件机制仍待做。

## 授权标注冻结、Tag 装配与 Episode 循环（E05/E06/E08）

AnnotationSelectionPort 提供 authorize、freeze_selection、snapshot_for_plan；
返回 FrozenAnnotationSelection(plan, snapshot)。集合交付和 Tag 实际生产消费者
只获取该端口，不再调用标注交付服务的 _freeze。独立选区服务保留权限、审核回执、
精确修订/文件哈希与包 manifest 绑定；每次冻结快照读取重验当前授权和不可变历史。
文件/审核收据与端口纯导入不会加载 PostgreSQL、Arrow、FastAPI 或交付实现。

API 与 worker 共用装配函数，显式注入 Tag 的 projector、automatic、committer、
quality_evidence、publish 和 derive；派生服务不再创建外域 Postgres 仓储，也不借
自己事务去运行目录私有查询。目录负责 validate_tag_parent 的事务。任务原有租约、
幂等、取消、失败阶段恢复和不重复生成已提交 primary 数据均经服务回归。

delivery_inventory 移出执行器，集合归档不再回读 delivery；加上前批 scope 和
路由应用依赖迁移，Episode 模块（含函数内 import）无循环并有图门禁。
43 项架构/应用检查和新增图门禁、2 个纯模块 mypy、全部修改 Ruff 通过。
37 项实际服务用例中首次 35 通过；2 个 Tag 测试错误地要求 v2 带当前所有规则，
稳定候选 3df9fc78 同样失败，故明确版本为 be06-qc/2 并用改动前 golden 比较。
该文件全部 10 项真实用例复验通过；未放宽产品 QC。保留所有失败 XML、基线复现、
修复复验及说明。实际 runtime OpenAPI 仍为 125732c98e2578f581e198ef81c338c892e34ef54dccc089c8d165e62f375206。

这不代表 EpisodePackageService 大对象、目录投影内部 E09 或所有逻辑数据契约已经完成。

## 逻辑快照与两个真实样本 adapter（B06/B07 进行中）

logical_data.models/ports 提供纯 SnapshotSelection、LogicalSnapshot/Stream/Sample
与 SnapshotReader。选择固定版本/修订和既有 snapshot_token；服务逐次通过
DatasetPageService 公共授权查询，保留当前租户、权限、版本成员、token 和审计。
样本读取额外绑定 snapshot_id，单次最多 4096，迭代默认 128 条并逐批重新授权。
LanceSampleAdapter 与 EpisodeSampleAdapter 注册到同一精确版本 registry；媒体样本
返回逻辑帧标识，不把 Lance 版本、package prefix、object URI 交给浏览器作选择。
模型允许无时间样本但此批仅验证时序数据，没有声称独立图片流程完成。

runtime 与 app 实际装配新逻辑路由：
`GET /api/v1/projects/{project_id}/regions/{region_code}/datasets/{dataset_id}/versions/{version_id}/episode-revisions/{revision_id}/logical`
及 `/streams/{stream_id}/samples`。scope headers/session 与旧页面一致；samples 需要
snapshot_id、snapshot_token、start/end。完整实际 HTTP 用例读取历史版本和混合版本
中两种存储，校验值/纳秒/子窗口/同快照、401/403/409/422 与缓存后撤权。
测试失败先发现隔离账号缺旧 episode.read 权限，再发现容器间独立 tmpfs；修正测试
授权和专有共享目录后完整用例通过。原失败与修复原因都保留。

实际接口来自新 runtime fragment；不手写第二份协议。对应新前端调用尚待 B 接入，
媒体 URL 授权仍走原能力，故 B06/B07 与 F05 不关闭；仅样本读取接口可供消费。


## A：备份与恢复共享显式装配

`BackupInfrastructure(settings)` 冻结传入设置，生产配置缺失时失败；只有显式
compose_functional 使用隔离文件仓库/签名器。备份 create/verify 与恢复
plan/execute/reconcile 均消费公共工厂，去除 restore_cli → whole_cli 私有依赖。
发布、核验与恢复 payload resolver 消费 BackupPayloadRepository，不再把文件仓库
伪装成 S3 类型；Object Lock/KMS/版本/签名策略和恢复批准协议保持原校验。

82 项备份/架构测试、5 文件 mypy 和 Ruff/实际源码门禁通过。新增真实服务回归
经过签名文件仓库、PostgreSQL 目录、backup verify CLI、空 PG/MinIO 目标的
restore plan CLI（含重复计划与精确签名哈希篡改拒绝），2 passed，0 skipped。
证据为外部 `backup-composition-services-recheck.xml`。初次挂载目录错误和负例
异常断言错误保留在原 XML 与 failures.txt。未称真实 Vault/KMS/灾备执行已验收。


## A：统一逻辑流媒体授权

`POST .../logical/streams/{stream_id}/media/authorize` 仅接受已授权固定版本的
snapshot token/id，调用与逻辑样本相同的物理 adapter。Episode 使用已生成预览；
Lance 使用固定 artifact receipt，并保留旧 episode.read 门槛与审计。返回
LogicalMediaGrant，不要求浏览器从 package/Lance 字段选择读取路径。
逻辑第 i 个样本对应 media_start_seconds + i/sample_rate_hz；原视频非零偏移
和深度 colorizer/单位范围保留。授权不启动编码，未准备/失败/退休仍返回原错误。

真实 PG/MinIO/API 的两种存储媒体下载、sha、视频帧解码、Range、隔离、撤权及
审计通过；原逻辑样本 HTTP 回归同时通过，共 2 项零跳过。54 项契约/架构与
3 项非零偏移/冻结收据检查通过，mypy 2 文件通过。运行时 OpenAPI SHA256
`5c2dc66bfaf284d77163c1b0532a9ec1ad403ad07f44def0f0906274cda643ad`。
前端 B 尚待消费，不关闭 B06/B07/F05，不声称非时序图片链路已完成。


## A：Tag 目录投影独立与共享事务写入

API 和持久 worker 的共同装配已将 Tag 切换到 PostgresTagProjector，显式注入
集合/文件/样本读取和 DatasetProjectionWriter。Tag 不再回读摄取服务的
_episode_packages 或调用其私有 SQL；固定父集合、包、来源和子提交校验仍在
Tag 自己的事务中。摄取与迁移也消费同一个目录 SQL 所有者，既有 16 个 SQL/辅助
方法的 AST 保持一致；版本高水位、幂等和完整产品事务仍由服务回归验证。

43 个真实 PG/MinIO/API/持久 worker 用例、55 项架构/实例/原生 Viewer 检查、
5 文件严格 mypy 与 Ruff 通过。原循环及私有调用的注入负例由门禁拒绝。
OpenAPI 字节保持逻辑媒体检查点的哈希。原失败保留：旧夹具 _stable_id 兼容
导入漏出、故障注入点仍指向已迁走的 _put_schema；修正导入与注入落点后复验
全部 43 项，无删除故障测试或放宽校验。B08/E02/E03 的其余边界仍待实施。


## A：Raw 录制登记事务端口（B18）

连续录制拥有 VerifiedRecordingFacts / RecordingRegistrationPort 与目标表 SQL。
机器人桥接和 runtime/worker 显式消费端口，调用方传入同一个事务 cursor；
录制、资产和源任务完成一起提交或回滚。不同上传争用同一录制通过事务锁串行化，
已登记资产的物理对象、摘要和归属不可被重试改写。原始上传与派生产物仍复用，
未经人工切片不会伪造 Episode。源任务查询通过目标模块公共读取方法获取录制状态。

4 项真实 PostgreSQL/MinIO/Temporal 回归和 1 项真实 runtime API/outbox/worker
回归通过，覆盖故障回滚、并发重试、旧收据固定、切片/QC/提交、重放、所有对象
字节读回、作用域与撤权。62 项架构/录制基础检查、Ruff、登记模块严格 mypy
通过，实际 runtime OpenAPI 保持逻辑媒体检查点哈希。

原失败保留：独占故障注入用例错误地同时启动自动 outbox worker，导致抢占；
改用用例专有 Temporal worker 后 4 项通过，另设生产 worker 闭环。新撤权夹具
初次漏设 active=false，仅改 revoked_at 不会撤权；补齐正常撤权状态后复验通过。
证据为协调目录 recording-registration-{services-v2,worker-v2,checks}.xml。
这批没有关闭工作流 DTO、Temporal 适配或私有执行上下文相关 B08/B09/B10。


## A：纯契约入口与内部共享函数（B24/B25）

Lance 的 schema_spec 独立拥有逻辑类型语法和 hc-arrow-schema/v1 指纹；
模型创建、Arrow 编译、Episode 计划和 schema 登记都消费同一实现。LeRobot
路径及本地文件校验归 local_files，capture 不再回读 reader；OpenArm 时钟
证据诊断归 clock_diagnostics，named 与 alignment 单向消费。旧函数导入保留
兼容名称，生产消费者已迁到实际归属。

10 个包入口改为惰性兼容导出，models/ports 的导入不会顺带加载服务或 SDK。
显式实现仍在原 adapters/engine/service 模块；不是创建空接口替代消费者。
16 个独立进程导入实验屏蔽 PostgreSQL、Arrow/Lance、NumPy、FastAPI、Temporal
等依赖，公共契约仍可用；8 个注入反例验证门禁拒绝恢复循环或 eager 导入。
指纹 golden 来自改动前 62555a5b 的实际函数，复杂嵌套类型和 Arrow metadata
与旧哈希保持一致。最终验证数字和提交见本轮功能矩阵及协调目录证据。
