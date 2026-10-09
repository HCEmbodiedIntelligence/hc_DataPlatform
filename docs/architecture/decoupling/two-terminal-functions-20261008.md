# 两终端功能保留与消费核对

源 main `ccc47b7143845450f5dad0e4e5741c8ba5881fed`，A 分支
`integration/platform-two-a-20261008`。本轮独立候选项目
`hc-platform-two-20261008`，不改 48088、原业务数据或机器人。
交接与逐次证据在 `diagnostics/platform-decoupling-two-terminals-20261008`。

## 实时基线与交接

- `feat/chayi-g1` 的 `2840554b` 及 `cb00f3e7` 已在 main；不是功能验收证明。
- `feat/chayi-resume-20261008` 基于同一 main，仍有未提交工作。
  用户明确将两个集合发布后端文件及三个 P06 前端文件交给 A/B 接续，保留原工作区。
  精确源文件 SHA-256、只读快照及后端 patch 记在外部 `handoff.json`。
- A 只在自有树应用后端 diff；B 拥有前端。部署脚本、旧功能进度文件不随该 diff 引入。
- 实时复核原 48088 的 API/frontend 挂载 resume；worker 已退出且挂载 merge。
  本轮建立新 DB/MinIO/Temporal/API/frontend/worker 和新卷，不恢复旧 worker。

## 功能矩阵

| 功能 | 原/当前入口 | 新实现和实际消费者 | 本轮证据与未完成部分 |
| --- | --- | --- | --- |
| 原生/MCAP/LeRobot 摄取、失败原件下载 | 上传、robot-ingest、recordings | runtime resolver 冻结 Episode 计划，workflow commit；raw 授权独立 | 代码保留；统一 worker 的完整摄取回归待执行 |
| RGB-D 与 RGB-only、单位/掩码/映射、官方预览 | P06 EpisodeViewerShell | package reader、preview、官方 Colorizer | 真实 preview 服务与离线官方 LeRobot 0.6.1 读回通过；无采集深度不补造；输入为合成数据 |
| 固定数据人工审核、QC、独立标注审核 | P06/P08 | data_reviews、quality、annotation immutable binding | 保留版本和权限；本轮发布测试验证冻结审核及审核变化冲突 |
| P06 固定详情/文件/范围/完整包 | overview/files/authorize 与 package delivery | episode detail feature → episode_store | 实际 HTTP/持久 worker 完整包与集合交付、官方 reader 读回通过；B 浏览器下载仍待验证 |
| P08 区间、标注交付、Tag 派生与独立 QC | AnnotationDeliveryPanel/TagEpisodePanel | annotation delivery 和 Tag outbox handlers | 实际 HTTP/持久 worker Tag 派生、独立 QC、交付、撤权回归通过；B 浏览器仍待完成 |
| P21 单包/批量/集合/RGB 子集/标注/原件 | PackageBatchPanel 与原件入口 | episode-deliveries；历史业务发布格式仍用 publishing exporter registry | 两种业务语义保留；持久 worker 与完整包/Tag/集合官方离线读回通过；浏览器仍待验证 |
| 历史读取、迁移回退与保留下载 | 显式历史版本/迁移记录 | legacy reader、migration inventory/batches | 仅保留兼容和隔离合成回归；不恢复历史批量迁移 |
| 集合发布 | P06 CollectionReleaseDialog（B 提交 3df9fc78） | collection-releases preflight → publish → GET 固定 version_id | 20 项服务/选择测试通过；普通账号浏览器预检、取消条目、201 发布、固定读取、刷新、同名幂等通过；浏览器审核变化/撤权仍待完成 |

集合发布新增显式 `episode_ids`（null 为旧行为，空数组明确选零项），未选择项返回
`NOT_SELECTED`；预检提供固定数据已审核的 `annotation_task_options`，供重新选择。
提交必须携带预检哈希；空集合、审核变化、旧哈希、同名不同选择不得发布。
省略选择字段的旧请求保持原幂等身份形状，已发布版本不重算历史哈希。
新集合发布不回退到 Lance 写入。

## 第一批验证

- `check-services.py --with-api tests/episode_store/test_collection_releases_services.py tests/episode_store/test_release_selection.py -q --junitxml=/results/collection-release-selection.xml`：20 passed，0 skipped。
- 覆盖预检无写、显式选择/空选/越界/重选、历史请求身份、三处中断、并发、审核变化、工作版本回退、名称碰撞、不可变数据库行、真实会话/撤权/401/403。
- runtime OpenAPI 重新生成，聚合检查、Ruff、502 文件架构门禁通过。
- 以上不是完整 P06/P08/P21 浏览器验收、真实传感器验收或 60 项架构完成证明。


## 已复核的第一批总集成

集成提交 `604bef6b4668978b6e7889004e511b9dbed6d652` 合并 A 后端
`2b68d4b7` 与 B 前端 `3df9fc78`。新增能力的生产消费者如下：

| 能力 | 实际消费链 | 验证边界 |
| --- | --- | --- |
| 不可变流写入 | Episode 提交、完整包和集合交付 → ImmutableObjectWriter | source hash/size、并发赢家、失败中止与真实 MinIO 读回；其他领域适配仍待迁移 |
| 应用实例隔离 | Episode 三组 router → request.app.state.episode_packages | 无本实例服务时 503；其他领域全局装配尚未全部拆除 |
| 历史 QC 规则 | quality evidence 和 Episode quality → 版本冻结 required rule set | v1/v2/v3 金样本和扩展枚举不改旧 JSON/hash；可插拔规则执行仍待实现 |
| 标注冻结 | annotation/collection delivery、Tag → AnnotationSelectionPort | 当前授权、固定修订/审核文件及内容校验；真实 API/worker 回归 |
| Tag 装配 | runtime 统一工厂注入 projection/QC/commit/publish/derive | 移除服务内跨域仓储创建；目录投影私有耦合仍待拆 |

该集成树重新执行：后端 168 passed，真实 PostgreSQL/MinIO/HTTP/Temporal
持久 worker 42 passed，前端 Vitest 1004 passed；均 0 skipped。
前端 typecheck、gen:api --check、54 项 architecture 和 mock=off 的 production
build:container 通过；后端 OpenAPI 聚合检查通过，runtime SHA256
`125732c98e2578f581e198ef81c338c892e34ef54dccc089c8d165e62f375206`。
原始结果在协调目录 `results/A-services/integration-batch-one-*`。
浏览器证据在 `evidence/B/p06-*`；58088 当前仍固定于 `3df9fc78`，与本地
集成树明确区分，不将本地合并视为运行版本升级。剩余浏览器验收、60 项中的
未完成项及真实传感器条件不在这些通过结果中。


## 第二批本地集成

`190d318b4a330740aca0e124dbd173a84ae4ecb7` 包含备份共享装配和独立 Tag
目录投影。精确集成树执行 20 项真实 API/持久 worker/CLI/PG/MinIO 回归、
90 项备份/架构/应用检查，均零跳过；前端生成类型核对、typecheck、架构和
mock=off production 构建通过。前端源码与首批 1004 项 Vitest 通过版本相同。
证据为 `results/A-services/integration-batch-two-*`；runtime SHA256 仍为
`125732c98e2578f581e198ef81c338c892e34ef54dccc089c8d165e62f375206`。

独立后端 next 中已实现逻辑样本及媒体授权，由真实 Lance/Parquet 和 PG/MinIO
读回验证，已公布契约给 B。前端消费者尚未完成，未纳入本地 main 的这两批集成，
不把新接口存在当成前端功能完成。B 浏览器候选仍为 3df9fc78，等待剩余验收回执。

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


## A：B24/B25 验证检查点

纯函数内部循环与 10 个包入口的隐式实现导入已拆除；生产读取仍经过实际
Lance、LeRobot 和 OpenArm 路径。88 项契约/架构/schema 检查、64 项
真实本地格式与读取回归、4 项旧 schema 指纹 golden、5 文件严格 mypy
及 Ruff 通过。6 项真实 PG/MinIO/Temporal 原生/录制处理、5 项真实
HTTP/持久 worker/下载/备份回归通过；另外 27 项 Temporal 工作流、
取消恢复、历史重放和导出版本路由通过。所有通过用例零跳过。

首次 17 项工作流测试因隔离内部网络无法下载测试服务器而失败，保留 XML；
取得官方 v1.40.0 二进制并固定 test_server_existing_path 后全部复验通过，
未替换工作流或活动实现。hash/来源记录与脚本位于本轮协调目录 results/test-tools
和 workflow_tests_offline.py。错误备份测试路径导致的零收集记录也保留。

后端 next runtime OpenAPI 保持 5c2dc66b 哈希；逻辑流前端尚待 B 消费，
稳定浏览器候选继续保持 3df。B 浏览器已确认 P21 四种包与 P08 带标注包
创建、worker、下载/官方 reader；旧迁移 seed 的 Tag 因同摘要多来源被正确拒绝，
另建来源唯一、已审核的原生父条目（browser-tag-seed.json）待 B 点击验收。


## A：第三批本地集成验收

代码 c69c4743b66b439aed72d8dcf3f22c82e8fa1f81 整合录制登记 B18、纯辅助
函数 B24 和包入口 B25。14 项真实服务回归（6 项 API/worker/下载/备份，
8 项登记/旧派生固定/原生处理）及 93 项契约/架构检查通过，无跳过。前端
实际 runtime 生成检查、完整类型、54 架构检查及生产构建通过；前端源码
与此前 1004 Vitest 通过版本一致。协议 SHA256 保持 125732c98e2578f581e198ef81c338c892e34ef54dccc089c8d165e62f375206。
证据：协调目录 results/A-services/integration-batch-three-{runtime,processing,contracts}.xml
及 runtime-integration-batch-three.yaml。前端两次命令准备失败（缺 uv、只读
/app）已修正为读取实际 runtime 契约并在 /verify 构建，最终全部通过。

B013 已给出候选切换窗口：Tag 正向创建、QC、三帧 RGB-D 和官方 reader
通过。B011 撤权 201 尚未通过验收：辅助动作遗漏 upload.manage 的隐含发布
能力，正在修正并复验。B014 逻辑样本把聚合有效性错误叠加到单通道，尚待
代码修复和真实存储回归；逻辑 API 仍不在本批主线。


## A：B014 单通道有效性和 B011 撤权复验

逻辑样本 valid 现在只使用目标通道有效性。真实合成缺 RGB 相机第1帧写入
Lance，再通过正常迁移提交 Episode Parquet；两种存储的物理 sample_valid=false
得到验证，同时 HTTP 中关节/action 仍 valid=true，缺失相机 false；原有样本、
子窗口、固定 snapshot、撤权和跨作用域行为保留。2 项真实读取回归和2项真实
发布授权回归通过；90 项逻辑契约/架构检查及Ruff通过，无跳过。runtime SHA
保持 5c2dc66bfaf284d77163c1b0532a9ec1ad403ad07f44def0f0906274cda643ad。
证据：channel-validity-{revocation,contracts}.xml、runtime-channel-validity.yaml。

B011 的原201源于测试辅助动作只撤直接publish，遗漏 upload.manage 的已有
隐含发布能力；未更改产品授权规则。辅助脚本已同时撤销两种来源、递增能力
revision并读取有效权限。当前普通会话实际预检200→撤权后提交/预检403→
旧固定发布200且内容未变→恢复后200；证据 browser-effective-revocation.json。
B已收到修正动作，浏览器负例仍待复验。前端逻辑消费仍由B接续，未标完成。


## A：处理器公共采样、元数据与执行上下文（B09 第一批）

annotation 拥有 FrameSelectionSamplerPort / FrameSelectionSampler，ingest 拥有
不依赖工作流DTO的 AlignmentMetadataBuilder；runtime 显式注入 MCAP 与原生
LeRobot 消费者，短期投影会话继续传递同一工厂。旧adaptive-2fps-v2输出以
1d29d6bc实际算法冻结golden，包含真实相机时间、事件加密、图像变化和上限。
core.ExecutionScope由机器人处理和录制活动消费；worker注入独立的
ActivityExecutionScope维护tenant上下文、日志关联和写入围栏。API专用录制
handler若被误用来执行活动会明确拒绝。旧工作流函数保留兼容包装。

116项不同基础/架构/摄取回归最终通过；新增上下文测试最初错误地预期已有
请求上下文，修正夹具后3项复验通过。6项真实MCAP/原生处理、6项机器人
基础设施回归（其中3项明确使用receipt doubles验证重试/原子性）和1项生产
worker登记→HTTP/对象读回通过。4文件严格mypy、Ruff、架构及实际OpenAPI
检查通过，runtime哈希仍5c2dc66b。

原始失败保留：机器人测试缺专用DSN/G0环境跳过6项，补只读夹具后零跳过；
首次worker装配误把采样参数传给连续录制resolver，实际启动报错，修复后
生产worker复验通过；只读缓存导致mypy内部错误/Ruff写缓存失败，改用/tmp
后同一检查通过。证据processing-ports-{checks,scope-recheck,services,
robot-services-v2,worker-v2}.xml及runtime-processing-ports.yaml。

B09仍in_progress：LeRobot的任务数据集、标签schema目标和schema候选3个
私有静态调用尚待所属查询/规划端口迁移，不能因本批3种能力已接通就关闭。
## A/B：第四批逻辑读取共同候选

代码5e19013f006fd98b3c1be6940cfdd16ecd8302d9整合后端1d29d6bc和B的
322ac761：P06 EpisodeViewerShell实际调用useLogicalViewer，经固定逻辑
snapshot读取numeric samples和媒体授权；保留按通道valid，不让缺相机抹去
有效数值。旧Lance和Episode两适配、媒体字节/解码/Range、发布撤权、Tag
与完整包真实worker共15项服务通过；92后端检查、1020前端测试、54前端
架构检查、完整类型、实际runtime生成核对及生产构建通过，全部零跳过。
证据results/A-services/integration-batch-four-{runtime,contracts,frontend}.xml。
runtime SHA保持5c2dc66bfaf284d77163c1b0532a9ec1ad403ad07f44def0f0906274cda643ad。

B017/019已在实际浏览器完成有效撤权：预检200后提交403，页面清空预检
并禁用发布；旧固定版本仍可读。原B011辅助撤权不足导致201的证据保留。
共同候选尚待B实际点击P06逻辑链路；P08/上传预览、冻结模型、非时序入口
尚未就绪，不将F05或全部解耦标完成。


## A：B09 剩余私有规划调用已迁移

collection_tasks.ingest_queries 拥有任务数据集查询，annotation.ingest_queries
拥有已绑定的标签schema目标查询；均接收调用者cursor与显式组织范围，不
自行打开连接或提交。ingest.schema_planning拥有纯schema候选构造。
runtime向MCAP resolver和LeRobotPipeline显式注入三个公共端口，原生处理器
不再导入workflow.ingest_plan或调用任何工作流私有函数。错误类移到纯规划
错误模块并保留历史名称/code；旧workflow静态入口仅保留兼容，不由业务调用。

105项基础/架构与纯导入检查、6项真实MCAP/原生RGB-D处理、1项真实生产
worker登记和HTTP读回、27项Temporal取消/重试/历史重放全部通过且零跳过；
4个新增文件严格mypy、Ruff、架构检查及OpenAPI通过，runtime仍5c2dc66b。
首次版本回放命令误写publishing测试目录导致零收集，修正为workflow目录后
27项通过，保留失败记录。证据ingest-planning-{checks,services,worker,
workflows-v2}.xml。B09现在可关闭；B08的DTO依赖、B10启动适配、B14其他
业务查询/配置和B15运行时算法仍待继续，未随本批一起标完成。


## A：第五批本地集成

75334d92继承第四批共同候选并整合B09两批提交（210a27b3、75334d92）。
实际MCAP/原生与生产worker测试均运行此后端源码；最终7项真实服务、27项
Temporal回放、105项基础/架构检查及前批执行上下文/采样golden回归通过。
前端源码与第四批1020测试/54门禁/生产构建通过版本逐字相同，runtime OpenAPI
亦保持同一5c2dc66b，与当前生成客户端一致。候选58088保持91f555df，供B
验收逻辑读取消费，不在其浏览器操作期间改挂载。全部60项保留：27已验证、
7进行中、26待实施。


## A：P08 任务到固定逻辑快照

新增 GET /api/v1/projects/{project_id}/regions/{region_code}/annotation-tasks/{task_id}/logical，
经普通账号 annotation_task.read、dataset_version.read、episode.read 授权，返回
AnnotationLogicalSnapshot（task_id、snapshot_token、snapshot_id、snapshot）。
Episode 任务按冻结 collection version 与完整 DataVersionRef 核对，旧 Lance 任务按
base_lance_version；目标目录负责按来源查找唯一固定 revision，缺失、歧义、混合
来源或窗口不符均拒绝，不读 latest，不把物理定位选择交给前端。app 实例显式装配。

两项真实 HTTP/PG/MinIO 回归分别读取旧 Lance 和新 Episode 的全部4样本及两路
媒体字节，标注修订变化后数据快照保持一致；未登录、跨组织、任务/数据权限撤销
及旧 token 后续读取拒绝均通过。114项逻辑/目录/架构检查、3新文件严格mypy和
Ruff通过。证据 annotation-logical-{services,checks}.xml 与运行时契约
runtime-annotation-logical.yaml。P08 页面尚待 B 消费，不关闭 F05/B06/B07。


## A/B：第六批 URDF 前端集成

集成代码 fcbaf2d6770881511c11e826de817ac0835b0790 将 B 的 a506d1f9 合入
8051faf2 后端基线。模型导入功能由 feature 完整拥有，保留显式组织客户端、
会话/权限变更取消及已发布版本保护；机器人兼容预览来源移到 Episode adapter。
1027 项前端测试、54 架构、完整类型、runtime 生成核对及精确提交生产构建通过；
5 项真实 PG/MinIO 模型资产、发布、行锁并发和租户检查通过，全部零跳过。
后端与第五批相同，已有 B09 生产 worker 回归保持适用；runtime 契约仍5c2dc66b。
证据 integration-batch-six-{frontend,services}.xml 与 integration-batch-six-build.log。
URDF 浏览器导入与 R04 冻结绑定仍待验收；P08 新协议26cf62a3等待前端，不在本批。

### R04 后端冻结模型场景（等待页面消费）

新 MCAP/LeRobot/连续录制在 PACKAGE_PLANNING 经明确组件冻结 robot 绑定、发布模型版本、资产清单/映射及标定到不可变 ledger 和 `hc/robot_scene.json`；不冒充采集时观测。同源重试与换绑保持原场景和配置哈希，Tag 实际 outbox 派生继承同一文件。旧 Lance/旧 Episode 无文件返回 MISSING_HISTORICAL，不猜当前绑定。

固定 logical 新增 GET robot-scene 及 POST robot-scene/assets/{asset_id}/authorize，仍按确切 snapshot_token/id 与读取权限校验，资产额外组织 robot_model.read、发布版本/清单成员/哈希约束。模型位于同组织另一项目时通过模型所有者只读组件读取，保留 PostgreSQL 内容存储与签名下载。新 FK 保留引用版本，ledger 禁止更新/删除。

实际回归：6 MCAP/LeRobot 摄取用例；后补 5 项历史双存储、连续录制含 Temporal、原生绑定完整链；最终原生回归同时通过跨项目模型上传/发布/绑定、换绑旧固定读取和资产字节、4并发重试、篡改标定拒绝、普通HTTP401/403/404/409及撤权、不可变SQL拒绝、真实Tag派生后场景/标定继承。111架构/逻辑/写入检查、5新文件严格mypy、Ruff/实际OpenAPI通过。证据 frozen-scene-processing-v2.xml、frozen-scene-production-services.xml、frozen-scene-concurrent-rebinding.xml、frozen-scene-checks.xml。早期夹具授权/预检key/worker scope失败保留；R04待B新页面实际消费与浏览器换绑，不标功能完成。


## A/B：第七批 P08 统一逻辑读取集成

代码6b2280919e1a3706bc8444f7b924eee0b163ce45整合P08后端26cf62a3与
前端c07cb705/b24913ad。标注bundle实际调用task/logical并复用P06样本/媒体，
核对scope/task/固定版本/hash/count，非零时间映射成标注相对步范围；权限revision
变化使已加载关节缓存与query流对象失效。STORE_ONLY提示明确只保存原始文件。
1045项全量前端、后补22项权限/query回归、54门禁、类型/精确runtime生成/生产
build通过；5项真实HTTP两存储P08/P06样本、媒体字节与撤权回归通过且零跳过。
后端114项目录/逻辑/架构及3文件mypy已经绑定26cf62a3，此批后端无额外改动。
证据integration-batch-seven-{services,frontend,revision}.xml及build日志。契约
SHA256=54db54916dd9dbb9733aba1f1cc7b758bc78d4e6af5b36097faec5a8cf702417。

B031通过浏览器URDF创建/更新/发布、真实WebGL画布和旧固定资产/映射读回；
保留首轮无头浏览器不支持WebGL的失败。该证据不替代R04数据快照换绑验收。
P08新共同候选尚待浏览器实际消费；原始深度预览协议修复和冻结模型均在独立
工作树，不纳入本批已验证结果。

## A/B：原始深度媒体与 R05 验收

原始媒体HTTP响应增加media_kind(RGB/DEPTH/UNKNOWN)、preview_kind
(ORIGINAL_VIDEO/DOWNLOAD_ONLY)及明确原因。源feature声明深度时只提供原文件
下载，不把16位深度视频伪装RGB；缺声明时不从文件名猜模态。原始字节和offset
不改写。15项上传/范围读取用例、严格mypy和架构通过；真实普通HTTP创建dataset、
上传整套合成RGB-D、commit、分类、原文件逐字节下载和隔离/撤权1项通过。
证据raw-media-contract-v2.xml、raw-media-services-v2.xml；首轮HTTP夹具未建
dataset被404拒绝已保留，补正常创建后复验，无放宽产品检查。页面等待B消费。

R05由B的a506d1f9完成feature导入状态机及Episode来源adapter迁移，1027测试
及第六批联合检查通过。B031以普通浏览器完成首次/更新发布、真实WebGL画布、
旧固定模型/映射读取，故R05现在可关闭；R04数据绑定冻结仍单独在实施。

### 第八批联合验证：原始媒体预览适用性

集成代码 `d7c306180415da1238676c52c05ec3302a402568` 包含后端 `5d59ba2e` 与 B `5dc6da66`。页面实际消费明确的 RGB/DEPTH/UNKNOWN 与 ORIGINAL_VIDEO/DOWNLOAD_ONLY，深度保留原字节下载。联合验证 1051 前端测试、54 架构门禁、类型、35356040 runtime 生成核对及精确代码 commit 生产构建通过；1 项真实普通 HTTP 上传/分类/下载/撤权通过，零跳过。证据 `integration-batch-eight-*`。浏览器此前深度 error.code=3 失败保留；新提示/禁止错误播放待共同候选浏览器复验。

本轮候选 CORS 使用专用配置，58088/55174 的 localhost/127.0.0.1 精确来源放行，外部来源拒绝。B 已用原 MCAP 会话真实续传 PUT/complete/commit 成功，worker 的动作缺失 RISK 仍为待修复产品缺口；失败后 Raw 下载 SHA 与源一致。P08 原生及派生任务的 logical 样本、两路解码、编辑/提交/审核浏览器通过；旧 Lance 浏览器因原临时文件已消失失败，不冒充历史读取已通过，另准备持久合成夹具验收。

### LeRobot 元数据处理能力（本轮后端待前端消费）

新增实际 POST `lerobot-imports:capabilities`，显式组织/项目/区域和 upload.manage，返回元数据 SHA、`lerobot-native-episode/1` revision、存储/处理支持及阶段化拒绝原因。调用生产原生 profile 校验，float64 与额外特征保留，不依赖机器人型号前端白名单。只声明元数据适用性；上传 commit 仍核对原始 info 字节内容，worker 仍验证实际表格/视频/深度证据。

新 PROCESS 上传冻结 processing_capability_revision 到 Raw manifest，worker 从固定源读取并校验该 revision；未知 revision 拒绝。旧上传缺省字段不进入历史规范化 manifest，原有会话身份不变。17 上传/能力用例、94 架构用例、2 真实 HTTP/PG/MinIO/Temporal 用例零跳过；实际普通账号检测 float64、未登录/跨组织/撤权拒绝、native 完整包读回通过。证据 diagnostics/platform-decoupling-two-terminals-20261008/results/A-services/lerobot-capabilities-{checks,architecture,services}.xml。前端尚未消费，B03/X01 等整体问题不因新增操作关闭。

### 第九批联合验证：P03 消费后端处理能力

集成代码 `1a9c8e892a79f3ab096af55d131ede0b954f0d5f` 含53a6fb52后端与ecf89fa2页面。P03正式POST capabilities，按返回结果展示处理模式并把revision带入上传/commit；float64不再由页面名单拒绝。1060全量、8项P09独立复验、54门禁、类型/3c6d4335 runtime生成/精确生产build、2实际HTTP/PG/MinIO/Temporal通过，零跳过。首轮P09动画可见性失败保留，未改页面或断言；单worker全量复验通过。证据integration-batch-nine-*。

后台MCAP numeric QC修复与R04冻结场景未包含本批，另有检查点等待共同消费。B046发现撤权后已加载numeric缓存仍可见，服务端媒体/overview均403；B正在修权限刷新，尚未关闭此浏览器负例。

### MCAP 数值 QC 实际消费修复

浏览器 A031/B039 的 /action 频率为30Hz且100%覆盖仍报 ACTION_MISSING；定位到 MCAP 只提供流统计，动作内容从未进入 QualityInput。新 Episode MCAP 计划冻结 `numeric_projection_revision=mcap-numeric-quality/1`，按 profile 的 action/joint topic 实际解码，数值事实以磁盘缓冲提供给同一 QC engine/evidence；具名关节按原名读取，不为无名向量捏造轴名。缺失、非有限、含混和非法向量保留诊断；默认 action 门槛不放宽。旧工作流缺该字段仍保持旧路径，旧报告不改写。

2 项实际 MCAP/Temporal/深度包回归（移除旧夹具的 action minimum=0），111 numeric/原处理器/架构用例，27 Temporal 重放，1 文件严格 mypy、Ruff、架构、实际 OpenAPI 核验通过；HTTP契约仍3c6d4335。证据mcap-numeric-quality-{services,checks-v2,replay}.xml。首轮新测试导入拼写错误保留。候选持久worker和浏览器原RISK重处理仍待联合验收，不把这些软件检查称为浏览器完成。

### 第十批联合验证：MCAP 数值质检与撤权缓存修复

共同代码 `69ff7dd8842da5cd2d5afaa1b5ab864bb40a30eb` 包含 MCAP numeric QC `12bdbe9b` 和 B 撤权后授权刷新 `7bda9314`，保留第九批原生能力。6 项实际 MCAP/native HTTP/PG/MinIO/Temporal、1064 全量前端、54 架构门禁、类型/runtime 3c6d4335 生成核对和精确代码 commit 生产构建通过，零跳过。证据 integration-batch-ten-*；首条服务命令测试路径错误、前端容器无 bash 的启动失败保留，不作为测试通过。B046 撤权缓存浏览器失败须在新候选复验，旧 MCAP RISK 报告不改写。

B050 已在原真实上传批次验证 Raw RGB 实际播放、深度 DOWNLOAD_ONLY 提示、下载 15318 字节及 SHA 与旧上传完全相同，原始媒体分类闭环通过。B048 历史 Lance 深度预览仍为独立缺口；B049 冻结模型 ID 正在下一检查点修复，二者不计本批完成。

### B048 历史 Lance 深度固定预览

Lance 适配器不再把深度原文件声明为video。对已存在且已发布/回退保留的迁移，按原dataset revision完整行哈希查唯一转换计划，并重新核对固定包、迁移源等价报告、通道到feature映射，读取现成官方Colorizer预览。原Lance样本/原视频/原hash/版本指针不改，不自动执行迁移或编码。尚无可信已发布转换预览的历史深度明确425 LEGACY_DEPTH_PREVIEW_NOT_READY；这是受限兼容，不宣称所有未迁移深度均能回放。

2项真实HTTP/PG/MinIO通过：两种存储RGB/depth实际解码、SHA/Range/401/403/409/撤权，以及无转换时425且没有新迁移；107逻辑/架构检查、1文件mypy、Ruff、架构/OpenAPI通过，契约仍3c6d4335。首轮旧SimpleNamespace测试缺正式kind已补真实RGB字段，失败证据保留。证据legacy-depth-preview-{services-v2,checks-v2}.xml。候选原持久Lance浏览器复验待部署，不以HTTP成功替代浏览器。
R04/B049 补齐固定 `model_id`，从发布版本的 `robot_model_id` 原值冻结，不由robot/version猜测；NO_MODEL_BINDING仍为null。实际普通HTTP读取/跨项目资产/换绑/4并发重试/Tag继承完整服务1通过，3文件mypy及OpenAPI生成通过。首轮数据库列名误用model_id导致实际worker失败的证据 frozen-scene-model-id-services.xml 保留，改成真实robot_model_id后v2通过，未放宽验证。runtime-frozen-robot-scene-model-id.yaml SHA256=40ce3f4fde3e763a50b8b39ac3dfb1a057581569ed891d80139d1aea51c21b13。此检查点仍待B消费，不关闭R04。

### R03：模型与实例绑定事务边界

绑定用例显式消费PublishedModelForBinding/PublishedModelIdentity，版本检查归registry且在调用者事务FOR SHARE；模型撤回消费robot_assets拥有的绑定历史查询，包含已被替换的历史绑定。runtime在API/worker工厂共同注入，构造缺依赖直接失败；实例仓储不读模型版本表，模型仓储不直接读实例/绑定表，新AST门禁拒绝回归。历史表名保留，不新建非原子HTTP协作。

真实PG四并发相同幂等key返回同绑定，旧ETag拒绝，写入绑定/指针/回执后的故障使三者同时回滚；模型行锁实际阻止并发FOR UPDATE，失败原key可以正常重试，跨组织模型拒绝且已替换绑定仍阻止误删。7既有模型/机器人PG、1完整native HTTP/冻结场景、1新增事务用例通过，101架构/API检查、3新文件mypy、Ruff/OpenAPI通过。新增事务fixture前几轮错误身份/非规范fingerprint导致失败保留，未放宽产品校验。证据model-binding-{transaction-services-v4,native-services,boundary-checks}.xml；R03实现已验证，尚待总集成，R04浏览器仍待验。

### 第十一批联合验证：固定场景消费者与历史深度

共同代码 `b49a93e782ea060694ee572e18d6c34b3124e932` 整合B055/d3efcab6固定场景页面、R03事务边界、R04真实model_id及B048历史深度固定预览。P06/P08先读取logical/robot-scene；FROZEN资产按固定scene授权，只有MISSING_HISTORICAL才允许显式当前绑定兼容，NO_MODEL_BINDING/失败/撤权不回退。标注为处理规划时固定；未知标定格式不声称已应用到姿态。

1082前端全量、54架构门禁、类型、40ce3f4f运行时契约生成核对、精确代码提交生产构建、10项真实HTTP/PG/MinIO/Temporal联合用例通过，零跳过。证据integration-batch-eleven-*、runtime-batch-eleven.yaml。此批服务统计为10项，以XML为准。R04浏览器换绑与原持久Lance深度仍待统一候选实测，故R04未关闭。

B054在1c57227d已用填充缓存的浏览器实证读权限撤销后403触发授权刷新、旧曲线与视频卸载、恢复成功。B060原float64文件真实PROCESS→worker/QC PASS→P06/P08→标注r1提交审核→完整ZIP及官方reader/DataLoader读回通过。B058另逐值核对归档Parquet为double并保留约1e-12差异；官方reader输出float32 tensor，不能表述为float64训练精度验收。

B063-B067的新MCAP回归只证明自动QC PASS与Raw原字节下载。实际processing接口500且产物action/state为string，是仍在修复的状态投影和数值归一化缺口；原失败包、旧RISK及证据保留，不计MCAP完整正向闭环。

### B063–B067：MCAP 成功目标与数值包修复（等待 P04 联合消费）

上传processing响应保留历史aligned_media，新增互斥episode目标和固定viewer；Episode目标包含真实频率/帧数/数据版本/内容哈希/标注任务，不泄露存储路径。校验上传源SHA、rollout、项目、区域、集合与Episode身份，旧Lance语义不改。实际普通HTTP status200→bootstrap→logical→samples通过，未登录与跨组织拒绝。

新MCAP计划固定mcap-episode-package/2及mcap-numeric-vector/1，按冻结QC profile选择action/joint信号，规范化value/position等字段为float64向量；未提供轴名时保留unknown，含混、非有限或不能精确表示的整数拒绝。原解码消息（含header/velocity/effort）逐帧流式保存在hc/recording/mcap_numeric.jsonl，文件纳入不可变清单；原Raw字节不变。processor/1历史计划仍生成原string内容，不用新语义改旧版本。

3项真实HTTP/PG/MinIO/Temporal分别验证新数值包、缺深度单位失败、历史processor/1；新包30行action/state Parquet double逐值与源一致，普通HTTP logical实际JOINT_STATE/ACTION+VECTOR且30样本读回，故不是只改kind标签。127格式/状态/架构检查、19状态/router检查、27工作流执行/重放、2文件mypy/Ruff/OpenAPI通过。证据mcap-episode-numeric-services-v4.xml、mcap-numeric-package-checks-v2.xml、mcap-numeric-package-replay-v2.xml。前几轮测试授权/响应路径/Path读取错误和禁网测试服务器下载失败均保留；实际生产校验未放宽。P04消费者与持久浏览器新尝试待B接入，原string包不可变且不宣称已修其历史内容。

### B16：LeRobot 格式读取脱离 CLI

data_formats/lerobot_g1拥有G1来源布局、camera/topic/29关节定义、Parquet与数字切片。服务reader/adapter/profiles/source_profile实际依赖该库；CLI只选择本地或固定远程版本/下载/缓存路径，注入文件解析能力后使用同一读取实现，保留旧导入别名。AST门禁拒绝服务反向导入CLI（含静态/相对/动态导入），冷启动服务导入不加载转换CLI。

21项CLI实际四相机MCAP输出、原生适配、上传与并发缓存检查，102架构检查、1库文件严格mypy及Ruff/OpenAPI通过。4项既有RGB-D实际HTTP/PG/MinIO/Temporal正负用例通过；另增G1原生真实上传→Temporal/QC→四路RGB包→29具名关节/action双精度逐值读回→四原视频哈希一致且各6帧真实解码→独立标注提交审核→READY重试与父子历史重放通过。G1只含RGB，不伪造深度或Dex1标定。证据lerobot-library-checks.xml、lerobot-library-architecture.xml、lerobot-library-services.xml及lerobot-library-g1-services-v3.xml。首轮G1夹具缺dataset offsets、次轮错误期待RGB重编码的失败保留；补真实offset后明确断言0次编码和原文件字节复用，不改产品校验。


## 2026-10-09 B17 native processing persistence ports

`LeRobotPipeline` no longer receives a database factory or constructs foreign repositories. Runtime injects Raw reader, native state transaction, source-rollout registration, composed task/schema read query, immutable QC profile writer, native schema registry and committed sample reader. `NativeLeRobotProcessor` receives QC, terminal workflow and published package/collection receipt queries. The worker takes explicit `RobotWorkerComponents` factories from the shared runtime; it no longer accesses pipeline connections or package repository internals. Historical workflow inputs, activity names, retry identity and local-only cancellation behavior remain unchanged.

Actual PostgreSQL/MinIO/Temporal regression: 12 cases include RGB-D and legacy G1 complete upload/QC/media/Tag flows, missing evidence failures, durable publication recovery through the newly injected ports, corrupted QC/wrong dataset rejection, four concurrent state redeliveries, obsolete attempt rejection and a fault after all state/resolution/aggregate writes proving whole-transaction rollback. The separately launched runtime worker completes robot Raw recording registration and ordinary HTTP read/revocation (1 case). Evidence: `native-ports-services-v2.xml`, `native-ports-worker-services.xml`; 113 architecture/cache/projection checks, six strict typed ports/adapters and unchanged runtime contract SHA673be751. 31 real Temporal execution/replay/scope cases also pass (`native-ports-replay.xml`), using the pinned official test server offline. B17 is verified; broader B14/B15 entrypoint and configuration work remains separate.

## 2026-10-09 R04 and original historical depth browser closure

B075 on exact shared candidate637d6ef6 used the normal task page to create independent synthetic target3d1baccb, uploaded the unchanged12-file native input, and obtained real worker SUCCEEDED/QC PASS. P06 displayed a visible fixed scene in software WebGL. A normal switch to the second published model followed by full refresh left P06 and P08 on the same frozen first version-de055573, identical scene c78fac5a and asset e5fe5dc hashes. Binding was restored via a new normal history record. Evidence: `evidence/B/r04-fixed-scene-rebind-validation.json` and its three input reports. R04 is now implemented_verified; capture-time binding and application of an undefined calibration transform are not claimed.

B072 separately verified the original old-task-d95b/version_lance_2/revision_d2cfa historical data, with four logical samples and RGB/depth videos both decoded16x16/ready4. Depth authorization identifies the existing fixed pyrealsense2 Colorizer artifact SHA96095cf4 and0.01–10m range; no new historical migration or original data mutation. Independent HTTP/byte/decode check: `results/A-services/browser-persistent-lance-depth-637d6ef6.json`; browser: `evidence/B/p08-historical-depth-637d6ef6.json`. The unrelated absent historical upload-session manifest discovery404 remains recorded.
