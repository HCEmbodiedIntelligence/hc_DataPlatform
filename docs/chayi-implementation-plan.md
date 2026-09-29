# 数据平台差异实施与执行交接

日期：2026-09-28。需求依据：[chayi.md](chayi.md)。

**机器人录制接入补充：实施范围包括 `/home/hc_op/workspace/teleop_ws/src` 的录制、相机元数据、后台封存和独立上传客户端。具体必改/复用文件见 [chayi-teleop-integration.md](chayi-teleop-integration.md)；其中 R1–R4 纳入 G1，R5 纳入 G2。不能以平台手工上传测试代替机器人接入验收。**

**本文件是交给后续 `/goal` 终端的实施方案，不是完成报告。** 本轮仅阅读代码、核查本机容器状态和编写交接文档；未修改业务代码、数据库、对象存储或运行配置，未重新运行软件验收。

**深度最终决策：用户明确选择 HEVC + 12 位量化。新 Episode 主包采用 LeRobot v3，RGB 与深度分别保存为视频，深度使用 MP4/HEVC/gray12le，并记录量化参数、单位、有效性和来源映射。原始录制保持原封保留；不再要求额外生成 16 位 MKV 或源精度数组作为新 Episode 主存储。此决策取代本方案上一版的无损深度扩展要求；用户原始差异文档保留不覆盖。**

## 1. 要交付的结果

把平台完善为以下流程：

> teleop_ws 网页/VR 触发原始录制 → 后台封存 → 独立客户端可靠上传 → 原始录制查看和下载 → 整段或选定窗口创建 Episode → 解析、对齐、QC、生成独立 LeRobot v3 标准包（含已采集深度的 HEVC 12 位表示） → 完整校验后提交数据版本 → 人工数据审核、Tag 编辑和标注审核 → 下载完整标准包，或按明确模态和修订生成交付包。

确定采用以下默认方案，执行时按此推进；发现与真实数据冲突时记录证据并调整实现，不重复讨论已经明确的产品方向。

| 决策 | 实施默认值 |
| --- | --- |
| 新标准数据 | 独立 LeRobot v3 Episode 包，含数值、RGB、HEVC 12 位深度及 HC 来源/标定/有效性扩展；新流程不再写 Lance |
| 深度保存 | 新包采用 MP4 + HEVC Main 12 + gray12le，对数量化为默认；记录单位/scale、量化 profile、无效掩码、时间和标定；原始精度由保留的原录制承载 |
| LeRobot 与训练 | 分别验证格式可读、深度可读、下游模型输入兼容；本任务不承诺未指定模型已支持深度训练 |
| 业务状态与标注 | PostgreSQL 继续管理；标注修订通过可靠任务生成不可变 JSON |
| 对象存储 | 沿用当前 MinIO 和存储抽象，不更换供应商 |
| 原始数据 | 原文件、哈希和上传身份保持不变，标准化失败不影响原件授权下载 |
| 机器人与平台分工 | 机器人保存原始深度 MCAP、分段 RGB MP4 并可靠上传；平台执行对齐、QC 和 HEVC 12 位深度标准化，不在采集回调生成标准包 |
| Episode 与 Tag | Episode 是数据条目，Tag 是其中的语义区间；添加 Tag 不创建 Episode、不复制媒体 |
| 数据版本 | 数据内容版本、标注修订、数据集集合版本分别建模 |
| 下载 | 默认完整数据包包含已采集深度；另选 LeRobot RGB 或 RGB-D 兼容配置，明确纳入/排除模态；批量标准集合重建索引、统计和媒体偏移 |
| 审核 | 自动 QC、人工数据审核、标注审核独立记录和呈现 |
| 页面 | 复用现有 Viewer、时间轴与标注编辑器，增加统一 Episode 详情；不全面更换主题 |
| 分段导出 | 显式指定一条任务分段轨道；轨道内重叠拒绝导出，空隙逐帧编号为 `-1` |
| 历史数据 | 新旧格式混合读取、分批迁移、保留回退；不自动回收旧文件 |
| 本次产品范围 | 数据采集、处理、查看、标注、审核、交付；不包含训练或新建通用 2D/3D 标注系统 |

### 1.1 完整保存、框架读取、模型使用分别验收

| 层次 | 交付内容 | G1 必须满足 |
| --- | --- | --- |
| 原始录制 | 原始 MCAP/视频/配置，包括原始深度消息 | 原封保留，可授权独立下载 |
| 完整 Episode 标准包 | 数值/RGB、HEVC 12 位深度、源帧到对齐网格的映射、有效性、标定、来源和指定标签 | RGB-D 可经官方 reader 离线读取，深度反量化误差可测；“完整”表示包含所声明模态，不表示深度与原始数值逐点无损 |
| LeRobot RGB 交付 | 显式选择的 RGB/state/action 等字段 | 官方 reader/DataLoader 验证；显示“本导出不含深度，完整包保留” |
| LeRobot RGB-D 交付 | 默认直接复用标准包中的 RGB/深度及解码参数 | 验证深度 tensor 的维度、单位和误差，并独立验证 HC 有效掩码；不重复量化已经符合契约的深度 |
| 模型训练输入 | 目标模型选择的特征、通道、预处理/归一化 | 只有指定模型及配置的实测才能称兼容；G1 交付输入契约，不新增训练任务 |

LeRobot 的数据能力与算法使用深度是两个问题。官方 v0.6.1 的[视频解码实现](https://github.com/huggingface/lerobot/blob/v0.6.1/src/lerobot/datasets/video_utils.py)包含单通道深度分支；当前 [dataset API 文档](https://huggingface.co/docs/lerobot/main/api/datasets)也列出深度编码和输出单位设置。但这不能证明任意策略网络会正确消费深度，也不能证明某种深度编码无损保存传感器原值。执行时固定实际 reader 版本验证，不直接以 main 文档代替已安装版本的验收。

### 1.2 深度编码的固定默认值

新数据采用以下初始 profile，与已核查的官方 v0.6.1 默认深度视频方式一致。任务确有不同工作距离时可配置新 profile，但必须版本化，并写入每个包；不能只改环境变量而留下旧元数据。[官方深度编码说明](https://huggingface.co/docs/lerobot/v0.6.1/video_encoding_parameters#depth-streams)

| 参数 | 默认与规则 |
| --- | --- |
| 文件/编码/像素格式 | `.mp4` / `hevc` / `gray12le`，单通道 |
| 量化 | 12 位，码值 0～4095，`use_log=true` |
| 深度范围 | `depth_min=0.01`、`depth_max=10.0`，单位均为米 |
| 对数偏移 | `shift=3.5` 米 |
| 视频编码 | `x265-params: lossless=1`，要求量化后码值编码/解码完全一致 |
| 单位 | 先按真实采集 `depth_scale` 换算；标准 profile 使用毫米输入及 `depth_unit=mm`，读取可选 mm/m；不能仅凭 dtype 猜测硬件单位 |
| 元数据 | `is_depth_map`、`depth_unit`、`video.codec/pix_fmt/channels/fps`、`video.depth_min/depth_max/shift/use_log`；编码器版本/profile 哈希写入 HC 报告 |
| 源数据 | 原件保持原格式和哈希，不因标准包完成而删除 |

`lossless=1` 保障的是量化后码值，深度量化和范围裁剪带来的误差必须在报告中单独体现。同一 LeRobot 集合中一个深度特征的单位、编码和量化参数必须一致；不兼容的 profile 分组交付或显式转换。

**RGB 和深度分别存 MP4：** 每个 RGB-D 相机对应一条 RGB 视频流和一条深度视频流，各有自己的 feature key 和文件，例如 `videos/observation.images.head_rgb/chunk-000/file-000.mp4` 与 `videos/observation.images.head_depth/chunk-000/file-000.mp4`。两路按 Episode 时间轴、帧索引和来源映射同步；多相机分别保存。长数据/集合可能分块成多个 MP4，不把“每路独立视频”误解成无论长度都只有一个物理文件。

## 2. 已核实的代码现状

核查基线：`4933200c1e09dd1f70ecbaabcfa9c6afa404e771`。启动执行时再次记录 HEAD 和工作区状态，不把此基线当作必须回退到的版本。

| 领域 | 当前事实 | 实施含义 |
| --- | --- | --- |
| 标准存储 | `runtime.py` 的业务运行时与 outbox 运行时均构造 `LanceAdapter` / `LanceCatalogService` | 两处装配和所有处理入口都要覆盖，仅改 exporter 无法切换主流程 |
| 处理提交 | `workflow/activities.py` 的 `commit_aligned_bundle`、`commit_fragment` 提交目录；Temporal 流程保留 `lance_commit` 阶段 | 迁移活动输入输出和提交语义；已有 Temporal 历史必须可以继续执行或有排空策略 |
| 读取与追溯 | `StepReaderPort` 已抽象逻辑帧读取，但版本模型、目录、投影仍携带 `lance_version` | 保留逻辑帧读取能力，引入格式无关版本；不得给新包伪造物理 Lance 版本 |
| 标注 | 已有不可变修订、Schema、固定提交、审核和并发控制；多处绑定 `base_lance_version` | 迁移绑定必须同时覆盖任务、草稿、修订、提交、审核、自动标注和导出 |
| LeRobot 导出 | `publishing/lerobot_export.py` 已构造 v3 元数据、数值 Parquet、视频实体、统计、`meta/annotations.json` 和来源清单 | 复用其格式整理、媒体处理与验证能力，避免重新写一套不兼容格式 |
| 深度现有处理 | `publishing/holobrain_depth.py` 的 `codes()` 先做范围裁剪/对数量化到 12 位，再用 HEVC；`continuous_recordings/openarm_raw/prepare.py` 已调用该写入器 | 编码方向与最终选择一致，应复用并统一 profile/元数据；补单位校验、量化前有效掩码、裁剪计数和官方 reader 验证 |
| 深度现有测试 | `test_holobrain_depth_and_source_timestamp_survive_export` 比较的是量化后的编码值 | 保留码值一致性测试，新增源深度→官方反量化结果的误差、mm/m 单位、有效掩码及时间对应验证 |
| 导出说明 | README 末段和 P21 仍描述为“媒体引用”；当前 `LeRobotArchive.build()` 实际将媒体正文放入 ZIP | 同步修正文案；不能据旧说明将视频物化能力误判为从零建设 |
| 导出资产解析 | `PostgresExportAssets` 通过已有目录来源链及已提交媒体授权；连续录制元数据查询要求 Episode `READY` | 不能在新标准包提交前直接调用这套已发布资产查询，会出现前置条件循环 |
| 连续录制 | 已有原始多对象上传、整段预览、软窗口、切片确认、真实媒体处理测试 | 继续保留原始时间线和窗口；不要求先裁短 MCAP |
| QC | 当前总结果为 `PASS/RISK/REJECT`，findings 有 warning/error；不能凭总状态还原所有规则的 pass/skip | 如需逐规则计数，补规则执行证据与 skip 原因；历史无证据显示未知 |
| Episode UI | P06 `ViewerShell.tsx` 已有多相机回放、机器人场景、时间选择、人工问题记录及简单信息抽屉 | 在现有查看入口扩展统一详情，复用回放能力，保留旧链接 |
| 导出 UI | P21 已有原始/数据集/已标注阶段、Episode 多选、批量任务与重试 | 增加单条就近下载，保留原批量页 |
| 本机运行 | 原平台 Compose project 为 `hc-data-platform-restore-test`，页面 8088；本轮看到八个常驻容器运行 | 本轮只核对容器状态，未证明页面、数据或完整链路通过 |
| 热更新 | Compose 将当前 `backend` 挂载进 API/Worker，API 使用 `--reload`；前端亦采用开发挂载 | 实施先创建独立工作树；直接编辑本目录业务源码可能立即改变 8088 |
| 机器人接入 | `teleop_ws/src/humanoid_manager` 已有 raw Session、封存及独立 uploader；camera 元数据有固定 scale，原始包限 256 资产，平台 PASS 回执依赖 Lance | 按 R1–R4 修正明确缺口并验证完整链路；机器人仓库独立提交，G2 联合安装部署 |

主要代码入口：

- [运行装配](../backend/src/hc_data_platform/runtime.py)、[配置](../backend/src/hc_data_platform/core/config.py)。
- [目录端口](../backend/src/hc_data_platform/lance_catalog/ports.py)、[版本模型](../backend/src/hc_data_platform/lance_catalog/models.py)、[目录服务](../backend/src/hc_data_platform/lance_catalog/service.py)、[物理与 PostgreSQL 适配器](../backend/src/hc_data_platform/lance_catalog/adapters.py)。
- [工作流活动](../backend/src/hc_data_platform/workflow/activities.py)、[Temporal 工作流](../backend/src/hc_data_platform/workflow/temporal_workflows.py)、[数据集投影](../backend/src/hc_data_platform/dataset_registry/ingest_projection.py)。
- [标准导出器](../backend/src/hc_data_platform/publishing/lerobot_export.py)、[媒体处理](../backend/src/hc_data_platform/publishing/export_video.py)、[深度量化器](../backend/src/hc_data_platform/publishing/holobrain_depth.py)、[原始录制准备](../backend/src/hc_data_platform/continuous_recordings/openarm_raw/prepare.py)、[资产授权](../backend/src/hc_data_platform/publishing/export_assets.py)、[连续录制来源](../backend/src/hc_data_platform/continuous_recordings/export_metadata.py)。
- [标注模型](../backend/src/hc_data_platform/annotation/models.py)、[标注持久化](../backend/src/hc_data_platform/annotation/postgres.py)、[标注 API](../backend/src/hc_data_platform/annotation/router.py)。
- [详情与回放](../frontend/src/pages/p06-dataset-detail/ViewerShell.tsx)、[标注工作区](../frontend/src/pages/p08-data-annotation/AnnotationWorkbenchView.tsx)、[导出页](../frontend/src/pages/p21-data-export/page.tsx)、[导航](../frontend/src/app/shell/navigation-manifest.ts)。

## 3. `/goal` 的拆分和边界

推荐使用三个先后衔接的目标，避免把“软件功能完成”“现网迁移完成”“真人/真机验收完成”混为一个状态。

| 目标 | 包含内容 | 完成边界 |
| --- | --- | --- |
| G1：P0 功能、机器人接入与隔离验收 | 下文 W0–W6、W7 的迁移工具与隔离演练、W8 软件验收，以及接入清单 R1–R4 | 平台和机器人侧可部署候选、迁移工具、真实上传服务链路、离线 reader 与浏览器证据；不修改现网业务数据或日常机器人安装配置 |
| G2：联合部署与历史迁移 | 原平台 8088 切换、机器人侧 R5 安装配置，执行备份、分批迁移、对账和回退演练 | 核对真实归属、旧审核和新链路；报告迁移数量及机器人真实配置/真机验收状态，保留旧数据与上传队列 |
| G3：P1 效率改进 | ANN-04、UI-04、UI-05、PERF-01 | 结构化语义编辑/导入、步骤卡片、可校验投影、同源性能测量 |

G1 完成不表示 21 条需求全部验收；G2 完成不表示不存在真人/真机验收项。各项状态必须有证据。

当前交接文件没有授权额外采集机器人运动数据。执行终端优先发现并复用已有真实录制；缺少硬件样本时先完成可以独立进行的软件工作，明确留下哪个验收条件未满足。

## 4. 开工准备 W0

1. 阅读本文件、`docs/chayi.md`、`docs/chayi-teleop-integration.md` 和各仓库适用的 `AGENTS.md`；核对源码、安装版本和已有修改。
2. 创建独立分支/工作树，平台建议目录 `/home/hc_op/workspace/hc_DataPlatform-chayi-work`；机器人各仓库按 R1 放入独立 ROS 工作区 `/home/hc_op/workspace/teleop_ws-chayi-work/src`。已存在则核对归属后复用，不能覆盖；不在 `teleop_ws` 根目录假设统一 Git 仓库。
3. 本轮 `docs/chayi.md` 是用户提供的未跟踪文件；本方案、goal 指令和机器人接入清单也可能尚未提交。创建工作树时一并复制这四份文件，保留原文，不依赖 Git 自动携带。
4. 隔离测试必须使用独立 Compose project、数据库、对象前缀或测试桶、Temporal namespace/队列和端口；不要将原平台 `.env` 直接当作测试配置运行。现存 38088 测试环境不自动恢复，也不接收真实机器人上传。
5. 本轮未发现 `backend/.venv`，但已有后端依赖镜像和前端 `node_modules`。选择隔离容器或按锁文件建立环境；不要把依赖缺失写成测试通过。
6. 建立 `docs/audits/chayi-implementation/status.md`，逐项记录负责人终端、状态、提交、测试命令和证据位置。没有明确并行安排时，一个终端顺序推进；多个终端不要同时修改同一工作树。
7. 登记验收样本清单：来源、授权范围、文件哈希、录制时长、相机/深度配置、frame/step 数、所属任务；敏感配置和真实数据留在受限诊断目录，仓库只存脱敏结果。
8. 按 R1 冻结跨端契约、隔离 ROS 域、manager 状态目录、录制根目录和上传队列；确认 OpenArm raw Session 与旧 ROS 诊断录制的不同配置入口。平台/机器人各自提交，报告记录可兼容版本组合。

## 5. 契约与实现任务

### W1：存储无关身份、完整多模态包与提交契约

覆盖 DATA-01/02/04/05/06、ANN-01、MIG-01 的基础，并与 R1 的跨端契约同步。先将以下契约落成模型、数据库迁移和针对性验证，再接主处理流程。

**身份：** 引入明确的数据引用，至少包含 `organization_id/project_id/region_code/dataset_id/episode_id/rollout_id/data_version_id/content_hash/storage_format/schema_snapshot_id`。Episode 独立版本和数据集集合快照分别管理；`episode_id` 与 `rollout_id` 通过显式绑定解析，不根据字符串拆分猜测。

**兼容：** 旧记录保留原始 `lance_version` / `base_lance_version`，通过兼容适配读取。新记录使用正式数据版本引用，不把新版本号塞入旧字段假装是 Lance。数据库采用新增字段/表、回填、兼容读、切换写的顺序，不在一次迁移里删除旧列或重写历史提交。

建议文件布局（在文档目标上增加租户命名空间）：

```text
raw/...                                  # 已有原件路径不改动
episodes/<org>/<project>/<region>/<date>/<robot>/<task>/<episode>/
  v0001/
    meta/info.json
    meta/stats.json
    meta/tasks.parquet
    meta/episodes/chunk-000/file-000.parquet
    data/chunk-000/file-000.parquet
    videos/<rgb_camera_key>/chunk-000/file-000.mp4
    videos/<depth_camera_key>/chunk-000/file-000.mp4  # HEVC/gray12le，12 位量化深度
    hc/depth/<camera_id>/validity/          # 无损像素有效掩码；与目标帧一一对应
    hc/depth/<camera_id>/frames.parquet    # 源帧到目标帧映射、缺帧/重复/裁剪信息
    hc/source.json
    hc/source_frames.parquet              # 非一一映射时必需
    hc/modalities.json
    hc/calibration.json
    hc/conversion_report.json
    hc/quality.json
    hc/manifest.json
  annotations/v0001/<task_id>/r0001.json
collections/<org>/<project>/<dataset>/<version>/manifest.json
exports/<org>/<project>/<episode>/<data_version>/<profile>/<content_hash>/
  ...                                    # 模态筛选/批量/指定标注交付，复用已就绪媒体
```

- 路径用稳定 ID；日期、机器人和任务来自冻结来源记录，缺失时用明确的 `unknown`，不补造业务事实。展示名变更不搬文件。
- `annotations` 增加 task 命名空间，避免多个标注任务的相同修订号冲突。
- 单条根目录内具备离线读取所需的数值、RGB、量化深度与元数据；不得用过期签名 URL 或“原 MCAP 还在”代替标准包的深度视频实体。未采集深度的录制不伪造深度流，采集了深度的录制须生成对应标准特征。
- RGB 和深度都在 `meta/info.json` 声明为官方可读特征；深度按 `is_depth_map=true` 和固定量化参数读取。`hc/` 保存来源、标定、原始单位/scale、有效掩码与审计等扩展，不再把深度视频本身放在只有 HC reader 可读的目录。
- `manifest` 记录相对路径、类型、大小、SHA-256 和包格式版本；它自身的哈希放在提交回执，避免自引用。内容哈希算法、排序、浮点和空值编码必须确定。
- 来源记录原始对象及哈希、录制 ID、父 Episode（若有）、相对窗口、时钟域、时间原点、处理器/对齐版本、重采样/重复/丢帧映射。
- modalities 定义 state/action 顺序、维度、单位、绝对/增量、控制与观测时间关系、有效性；缺少动作含义/深度单位时显式标记缺失及影响。
- 现有 exporter 存在 float32 转换，抽取共用代码时须明确允许的数值转换及误差。用户已选择深度 12 位量化，其 profile 和误差须显式记录；其他模态不因此获得降低精度的默认许可。

**深度为 P0 必交内容，按 HEVC + 12 位量化实现：**

1. 开工样本确认每路深度的 dtype/有效位/字节序、分辨率、单位或 depth_scale、无效值/掩码、时间戳/时钟域、相机内参/畸变、RGB↔深度外参以及是否已注册到 RGB 像素。单位或标定未知必须如实记录，不能默认都是毫米或已经对齐。
2. 按 1.2 的 profile 将有效源深度换算到已声明单位，使用官方一致的量化公式生成 0～4095 码值，再以 HEVC/gray12le/MP4 编码。支持整数和浮点来源，保留原件，不额外要求生成 MKV 或无损浮点深度主副本。
3. 深度标准视频采用 Episode 对齐网格，PTS、帧数和 step 对应。RGB/深度原生频率不同时，按有版本的对齐策略选择源帧，记录源时间、时间差、重复、缺失、超阈值和未选中源帧数量；原始完整时间线保留在 raw，不静默丢弃来源证据。
4. 在裁剪和量化前识别零/NaN/Inf/传感器无效标记及缺帧。以 HC 无损像素掩码保存有效性并区分帧缺失，另记有效值的上下界饱和情况；视频中可填充确定码值，但官方码值 0 对应 depth_min，不能另约定为“官方自动识别的无效深度”。元数据、掩码和视频必须绑定同一版本/帧号。
5. 官方 reader 返回反量化深度（默认毫米，可选择米）；平台数值查看和几何功能在应用有效掩码后使用这些近似物理值，并显示量化精度。伪彩视频仅用于预览。HC 掩码不被官方 reader 自动消费，离线示例需演示如何读取并应用；不能把无效像素的填充值计为有效测量。
6. 复用并完善现有 `DepthVideo`：统一 profile、单位转换、元数据与统计；验证 MP4 解码码值逐点一致，记录源物理值与官方反量化值之间的最大/分位误差和裁剪比例。有效且未裁剪像素的误差应在该量化 profile 的半格误差界加明确浮点容差内；裁剪像素单列，不能混入后用宽松阈值掩盖。
7. 深度 MP4、profile、掩码和映射一起纳入标准包 manifest/content_hash/原子提交。声明采集了深度却缺文件、缺必要单位或验证失败时不能标记“完整包就绪”；RGB 子集就绪与完整包就绪分别展示。原件的访问独立于这些状态。
8. 用固定版本官方 LeRobot reader/DataLoader 直接读取包含 RGB-D 的标准根目录；离线示例同时返回深度 tensor、输出单位、HC 掩码及 source 映射。满足同 profile、时间/帧映射和哈希契约的已编码深度优先原样复用；需要重采样或改 profile 时生成新版本并报告转换，不重复量化无须改变的数据。

**原子可见与恢复：**

1. 为一次输入/处理配置生成稳定幂等键，预留独立版本身份；准备阶段记录独立 attempt。
2. 写新路径，校验必需文件、行数、索引、时间轴、媒体和每个文件的哈希。
3. 写不可变提交清单，再在数据库事务里提交版本可见指针与后续事件。多对象上传期间不显示 READY。
4. 对象提交后数据库失败可按回执补登记；数据库禁止指向未验证的文件集合。
5. 重试相同内容返回原回执；相同幂等键不同内容报冲突。已提交版本不可覆盖，不能以目录重命名假设跨对象原子性。
6. 提交与暂存清理分别恢复；只清理确认无引用的失败 attempt，不能删除重试已提交的媒体。

交付检查：故障注入覆盖缺文件、校验失败、对象已写但 DB 未提交、DB 已提交但响应丢失、重复请求及并发提交；只有完整包可被查询为就绪。

### W2：新多模态文件写入、查询和三条入口接通

覆盖 DATA-01/02/03/04/06、SEG-01。

同时执行 [机器人接入 R2–R4](chayi-teleop-integration.md)：接通实际 `humanoid-upload → robot_ingest → recording_bridge → continuous_recordings`，替换内部成功回执对 Lance 的强制依赖；保留旧公开 API 与上传队列兼容。深度源单位校验、长录制资产限制和机器人录制/切片状态提示也属于 G1，不能留作未排期事项。

- 抽取 `LeRobotArchive` 中可共享的格式、统计和媒体校验；主写入器按文件流式生成，避免把长录制所有数值、视频和 ZIP 一次装进内存。
- 在 `openarm_raw/prepare.py` 等入口复用现有 HEVC 12 位实现，补齐量化前单位/有效性检查及掩码、统一元数据与误差报告。深度属于主包必需模态，编码/校验失败不得假报完整包就绪；已封存原件仍可查看和下载。
- 给提交前处理创建明确的资产访问端口：从当前受权限约束的输入、冻结来源及当前 attempt 的已校验媒体读取。保留现有 `PostgresExportAssets` 的已提交授权条件，不能为了复用导出器全局放宽它。
- 同时覆盖普通 MCAP 上传、原生 LeRobot 导入、连续录制整段/切片。原生包符合完整契约时可复用数值或媒体；多 Episode 分块不能简单当作每条独立根目录。
- 更新 API runtime 与 outbox/worker runtime 两处装配。验证新写入实现从未调用 `LanceAdapter.stage_attempt/commit_staged`，且对象存储没有新增 `.lance` 数据。
- 提供 Parquet 实现的逻辑 `StepReaderPort`：按固定版本、Episode、半开区间和列读取，保持 timestamp_ns 精度、valid/repeated、来源时间与顺序。使用行组/列裁剪及受控缓存，不为每次 seek 重读整个包。
- 增加按版本/相机/step 读取深度的端口，按官方一致的解码和反量化流程返回 dtype、shape、输出单位、量化 profile、有效掩码和源映射。帧 JSON 只携带引用及小型元数据，深度正文使用受控二进制读取，不把二维数组复制进每条 JSON。
- 按实际版本格式路由读取，新旧 Episode 可共存于一个数据集集合；来源链、预览、标注、自动标注、质检复核、导出均使用同一数据引用。
- Temporal 类型/活动变更需要兼容旧历史，采取版本化工作流或明确排空后切换；不得只改阶段名导致重放不确定。
- 就绪投影在完整标准包提交后产生；原始数据的浏览和下载继续独立于处理成败。

交付检查：三类入口分别完成上传→处理→RGB/深度回放与数值读取→创建标注任务→标准包下载；没有深度的输入明确标记未采集。短真实 RGB-D 录制、长录制多窗口、已有原生包分别留证据；深度验证量化码值保持、官方反量化误差、掩码、单位与源帧对应，不要求量化值等于原始值。已有合成样本可以用于故障测试，不能代替真实数据验收。

### W3：版本化标注文件与任务分段导出

覆盖 ANN-01/02/03、SEG-02。

- 标注任务、草稿、修订、submission、review、Schema 和自动标注结果绑定同一不可变数据版本/内容哈希。保留 annotation_id、父子结构、交叉区间、属性、对象、关系与已有并发/幂等规则。
- 保存不可变修订的同一数据库事务中写入快照任务/outbox；worker 按该修订生成不可变 JSON。不要在一次普通 GET 中创建修订、冻结审核或写快照。
- 文件状态至少区分 pending/generating/ready/failed，保存状态与文件状态分开展示。可见失败原因和受权限约束的重试；失败重试不生成新修订。
- JSON 包含数据绑定、修订号/哈希、完整 Schema 或可离线解析的定义、所有标签结构、区间约定和来源。审核是后续不可变记录，绑定精确 submission/revision；新增审核不回写已经生成的修订文件，可生成独立审核附件。
- “带标注下载”默认选当前可交付的已审核修订，并显示版本；如支持草稿/未审核下载，需明确标识且沿用权限策略。不得下载时悄悄改用最新草稿。
- subtask 必须显式指定轨道，固定数据版本和标注修订，导出 `hc/subtasks.jsonl` 与逐帧 `subtask_index` 扩展；段按边界和稳定身份确定排序，编号从 0 开始，空隙为 -1。
- 任务分段轨道重叠返回可定位到标签的错误，其他轨道的交叉标签完整保留；不能把全体 Tag 压扁成单一编号。
- 逐帧分段列写入修订绑定的派生包，普通 Tag 保存不修改基础 Parquet/媒体。映射必须基于同一时间轴；重采样后按 source_frames 重算，禁止照抄帧号。
- “从区间创建 Episode”为显式命令，创建新身份并记录父 Episode、父数据版本、父标注修订及原录制窗口。作为独立派生关系处理，不能破坏原冻结切片“不重叠”的约束或修改父条目。

交付检查：保存后刷新修订一致；快照生成失败可重试；旧修订离线还原完整标签；审核过的修订不被新草稿替换；六段、空隙、同轨重叠、多轨交叉、纳秒/帧边界、Tag 转 Episode 均有针对性验证。

### W4：独立审核状态与统一详情 API

覆盖 QC-01、UI-01/03、DATA-04。

- 提供聚合查询，返回任务要求、Episode 展示名、数据版本、标注修订、三类审核状态、父录制/时长、源窗口、当前时长/帧数/相机数/任务分段数、文件状态、可用动作及受限原因。
- 所有聚合字段固定到请求的数据版本；不能将最新 QC、另一版视频和旧标注拼成一条详情。当前草稿、已提交、已审核各有明确身份。
- 自动 QC 保留 `PASS/RISK/REJECT` 总判定并展示逐规则 pass/warn/fail/skip/info 证据；未实现或不可评估的规则须有原因，缺少历史证据不能显示为通过。
- 人工数据审核评估采集是否符合任务，标注审核评估标签。核查现有数据集审核是否满足单条业务语义；不满足则扩展独立记录，不用“没有问题记录”推断人工通过。
- 人工决定包含审核对象版本、决定、操作者、时间和意见；定位意见可绑定具体区间/标签，返工后旧决定仍可追溯。
- 文件清单按数值、RGB、深度、标注、说明、报告分类，提供路径、大小、哈希及授权下载动作；只列已提交清单，不通过开放任意对象路径实现文件浏览。
- 深度卡片显示源 dtype、输出单位、HEVC/12 位量化 profile、工作距离、误差与裁剪比例、分辨率、源/目标帧数、同步覆盖、缺帧/无效像素率及标定状态；标准包、预览和可选交付各自标记就绪状态，不把 RGB 导出成功当作深度成功。
- 组织/项目/区域授权、对象归属、逐动作 allowed_actions、审计沿用现有机制；分页、错误码、缺失状态进入正式 OpenAPI 与生成客户端。

交付检查：自动 QC 含 warning/skip 时显示不混淆；三类审核独立变化；跨组织/区域、跨版本文件和伪造路径请求不能越权；缺文件、未处理和无权限能区分。

### W5：统一 Episode 详情与就近下载

覆盖 UI-01/02/03，保持现有编辑闭环。

默认从 P06 既有 Episode 查看入口扩展，旧链接继续可用；数据集、上传结果、切片、问题清单、标注/审核任务链接到同一条数据的相应位置。

```text
任务 / 机器人 / 日期 / Episode 展示名
数据版本 · 标注修订 · 自动 QC · 人工数据审核 · 标注审核
父录制总时长 → 原始窗口 → 当前时长 / 帧数 / 相机数 / 子任务数
[回放与分段] [数据文件] [数据质检] [标注与审核] [版本与来源]
[查看原始录制] [下载完整数据（含已采集深度）] [LeRobot 导出] [带标注下载] [进入标注/审核]
```

- 复用 `SynchronizedEpisodeWorkbench`、共享时钟、相机切换与机器人场景；加载文件/报告不重置播放位置。
- 默认时间显示秒；展开精确编辑后显示从 0 开始的帧号与 `[start,end)`。原录制绝对/相对时间与 Episode 时间不能混用。
- 子任务数只统计指定分段轨道，不把所有层级 Tag 数量当子任务数。没有配置轨道显示“未指定”。
- 数据版本、标注修订作为主文案；存储格式、旧 Lance ID、路径和哈希留在技术详情，不删除历史信息。
- 完整包已就绪直接复用文件/缓存归档，不触发重新转换；如果需要首次压缩，显示单独打包进度。带标注下载默认保留完整模态，并显示所选修订与审核状态。
- LeRobot 导出显式选择 RGB 或经验证的 RGB-D profile，显示纳入/排除模态、量化及 reader 版本；不能悄悄把深度从“完整下载”中删掉。未经适配验证的目标标记不支持，提供完整数据下载。
- 深度提供同步伪彩预览及按像素读取反量化的物理深度值/单位/有效状态，说明其量化精度；RGB↔深度未做空间注册时显示原深度坐标，不能假定同一像素索引代表同一空间点。
- 普通用户能区分上传失败、处理失败、文件生成失败、打包失败和无权限；提供对应操作，不用一个通用“失败”覆盖所有情况。
- 保留现有 P08 编辑、提交、审核、返工入口；步骤视图的完整语义表单属于 G3，不用只读卡片替代已有编辑器。
- 原始/已处理/已标注批量导出继续在 P21；更新旧的“仅媒体引用”等与实现不符的文案。
- P0 先串联生产阶段和待办链接；导航的整体归并随 G3 进行，不改变权限边界。

交付检查：列表→文件/下载最多两次页面跳转；1440/1024 宽度、键盘焦点、时间轴、视频和保存状态可用；真实 API 浏览器脚本通过，并明确记录真人角色任务是否实际执行。

### W6：批量交付与现成包复用

覆盖 EXP-01、UI-02、ANN-03。

- 区分多个独立标准包的批量 ZIP 与重组后的 LeRobot RGB/RGB-D 集合；界面/API/归档清单标明类型及模态选择。完整包批量交付包括深度 MP4、profile、有效掩码及源时间映射。
- 集合包重建全局 `index/episode_index/task_index`、任务表、episode 元数据、统计、相机映射及视频偏移；不能直接拼目录。
- 标注修订、数据版本、来源窗口及 frame 映射随选中的 Episode 冻结；保留全部标签及分段扩展。
- 复用当前任务队列、幂等、选择范围、取消和重试机制。相同数据版本/修订/选集/格式/模态选择/深度 profile/reader 版本/处理器版本的结果可缓存；校验清单后复用，不能只凭文件名命中。
- 数据维度、频率、单位或相机契约不同的数据不能无提示合并；给出明确不兼容原因或显式转换方案。

交付检查：至少两个 Episode 的 RGB-D 离线 LeRobot 集合包，通过官方 reader 和跨 Episode DataLoader batch 读取；首尾边界、task、帧数、数值、深度单位/量化参数、媒体与标注对应正确。独立验证随包 HC 掩码和源时间映射；单条重复下载及同 profile 集合交付证明未无故重新量化/转码。

### W7：历史迁移工具、回退与现网切换

覆盖 MIG-01。G1 完成工具和隔离副本演练，G2 执行真实批次。

- 先 inventory/dry-run：按租户、数据集、旧版本清点 Lance、原件、媒体、标注、审核、导出和来源引用；输出可恢复 checkpoint 与迁移映射表。
- 逐批创建新不可变标准包；在离线副本核对数值/允许转换误差、RGB 哈希或解码像素、HEVC 深度码值/反量化误差/profile/掩码、时间轴、标签边界、父子关系及审核固定引用。历史 HEVC 12 位数据若契约一致则直接复用；历史 MKV/数组可从原件或可靠副本按新 profile 转换，记录输入格式与转换误差。原件/单位/profile/有效性证据不足时明确记录无法核对的项，不能补造或声明原始深度无损。
- 历史 revision/submission/review 的内容哈希不改写；用显式映射关联迁移后的等价数据版本。会改变标签所指时间轴的转换必须生成新派生身份，不能声称旧审核自动覆盖。
- 每批提交可见指针前验证清单；失败批次保留旧读路径。中断可续跑，同一批次重跑不重复迁移。
- 回退不仅是代码切回旧版：支持已存在新多模态包的兼容版本必须保留全部模态的可读性；记录软件、schema、配置和指针的回退矩阵，不启用看不到新数据/深度的旧二进制。
- G2 在原平台做备份和恢复校验、运行任务排空/兼容处理，再切换新写入。切换后新旧数据回放、标注、导出均验证；每批对账失败则停止该批推进，其他独立工作继续。
- G2 同时执行 R5：服务端兼容先行，按 G1 实际修改安装机器人 manager/camera 和独立 uploader，保留录制与队列，核对真实身份/任务及配置；缺真实归属时明确列出，不能使用 synthetic 身份替代。回退覆盖跨端版本与旧队列、新大包能力。
- 不自动删除旧 Lance、媒体、原件或 Docker 数据卷。文件回收是有保留期和引用证明的后续生命周期任务。

交付检查：隔离副本完成一批迁移、一次中断续跑、一次指针回退；G2 另有现网逐批结果与未迁移清单，不能用副本测试声称现网迁移完成。

### W8：软件验收与交付收口

- 更新 OpenAPI、生成客户端、配置示例、部署说明、README 与功能状态报告；状态按“已实现/已软件验证/已真实数据验证/待真人或真机验收”分别标记。
- 对每个需求记录代码位置、执行命令、数据集身份/哈希、预期和实际、原始证据路径及未覆盖项。不要把旧截图、旧测试数量、skip/xfail 当本次通过。
- 交付阶段提交记录、数据库变更、配置变化、迁移与回退命令、浏览器截图和运行结果；敏感连接信息/签名下载 URL 不进入报告。
- R1–R4 独立列出代码与证据，交付平台/机器人各仓库候选提交、运行安装路径、schema 兼容矩阵、录制封存上传恢复测试与 R5 安装回退步骤。手工上传成功不计作机器人上传链路通过。
- G1 到此交付可部署候选与 G2 的实际执行说明；G2 才执行现网切换。执行终端不因为还有 G3 优化项就隐藏 P0 阻断项。

## 6. G3 的具体范围

| 需求 | 实现内容 | 验收 |
| --- | --- | --- |
| ANN-04 | 在既有 Tag Inspector 增加执行者、对象、动作、接触、结果、置信度、前/中/后状态等 Schema 驱动字段；外部文件导入先验证数据版本、源哈希、时间轴和 Schema，再产生新修订 | 保存、刷新、导出一致；非法来源/边界拒绝且不改旧修订 |
| UI-04 | 同一份 Tag 的步骤列表与完整标签视图；步骤、视频、时间轴、意见定位共用区间；侧栏可收起、步骤独立滚动 | 点击步骤/审核意见准确 seek，切视图不丢未保存状态 |
| UI-05 | 仅用有证据的标定与时钟映射生成机器人/相机投影，显示标定版本和异常定位 | 缺标定/同步/模型时明确原因；真实样本验证投影误差和定位 |
| PERF-01 | 同源短/长/多相机样本，测处理耗时、峰值内存、存储字节数、对象数量、首帧、seek、同步误差/掉帧 | 冷暖缓存分开，记录硬件/版本/样本哈希；依据结果优化，未实测不声称更快或更省 |

导航建议沿用原文六个业务区；迁移旧路由、权限和返回链接后再切换菜单。P1 不扩大到训练、通用 2D/3D 编辑或未经验证的自动重建算法。

## 7. 21 条需求覆盖表

| 编号 | 执行任务 | 最终证据重点 |
| --- | --- | --- |
| DATA-01 | W1/W2 | 三条新入口无 Lance 写入，回放/标注/交付链路通过 |
| DATA-02 | W1/W2 | 独立拷贝 RGB-D 标准包；官方 reader/DataLoader 读取深度与 RGB，HC 掩码离线读取通过 |
| DATA-03 | W2/W5 | 处理失败时仍可授权下载原件且哈希一致 |
| DATA-04 | W1/W2/W4/W5 | 父录制、源窗口、帧映射在文件/API/UI 一致 |
| DATA-05 | W1/W2 | 缺文件/进程中断/DB 失败/重试/并发均无假 READY |
| DATA-06 | W1/W2 | state/action、RGB、深度 HEVC 12 位 profile/反量化误差/掩码/单位/时钟及标定核对 |
| SEG-01 | W2 | 整段与长录制多窗口，不先裁短 MCAP |
| SEG-02 | W3/W5 | Tag 保存无媒体重写；显式派生 Episode 有父来源 |
| ANN-01 | W1/W3/W7 | 层级、交叉区间、属性、对象关系、固定审核不丢失 |
| ANN-02 | W3/W5 | 每个修订有可重试生成/下载的完整 JSON |
| ANN-03 | W3/W6 | 指定轨道编号、空隙 -1、同轨重叠拒绝、其余 Tag 完整 |
| ANN-04 | G3 | 语义表单与校验后导入产生新修订 |
| QC-01 | W4/W5 | 三种审核独立；warning/skip/未知有证据与说明 |
| UI-01 | W4/W5 | 聚合详情和真实 API 浏览器验收；真人任务另记录 |
| UI-02 | W5/W6 | 原件/完整多模态/LeRobot profile/带标注下载，版本与模态选择可见，现成包复用 |
| UI-03 | W1/W5 | 主文案数据版本/标注修订；历史底层信息仍可查询 |
| UI-04 | G3 | 步骤、视频、时间轴、意见定位一致 |
| UI-05 | G3 | 有效标定/同步前提和投影验证证据 |
| EXP-01 | W6 | 多 Episode reader/batch 与索引、偏移、标签核对；完整批量包深度可离线读取 |
| MIG-01 | W7/G2 | 工具/副本演练与现网真实迁移分别报告，旧存储保留 |
| PERF-01 | G3 | 同源同环境测试原始结果 |

## 8. 必须复用/扩展的验证

现有测试可作为回归入口，但必须新增或调整针对新存储与新契约的有效断言：

- [机器人接入 R4](chayi-teleop-integration.md)：manager/camera/uploader 定向回归、两种录制入口、封存/上传故障恢复、超过旧 256 资产限制的包，以及实际客户端到平台的 RGB-D 全链路；真实硬件验收单列。
- `backend/tests/lance_catalog/`：旧读取、固定版本、幂等、对账恢复保持可用；新增新包提交与混合读取测试。
- `backend/tests/workflow/`：原生导入、普通 ingest、outbox 与 Temporal 重放/失败恢复。
- `backend/tests/publishing/test_lerobot_materialized_export.py`：媒体实体、固定标注、统计、深度和来源；扩展单条主包复用与多 Episode 集合。
- 深度专项：uint16/float 来源的 scale 换算、官方量化/反量化一致性、HEVC 码值无损往返、mm/m 输出、HC 无效掩码、与 RGB 不同频率、源帧缺失/重复、零/负值/NaN/Inf、上下界饱和、单位未知、未空间注册、单独 MP4 下载与离线读取；固定小型样本全帧核对，长真实样本记录抽检范围。记录源物理深度与输出的最大/分位误差和裁剪比例，不以“与源数组完全相等”验收量化结果。
- `backend/tests/publishing/test_mcap_lerobot_export.py`：真实 MCAP 到标准文件链路；新流程禁止 Lance 写入。
- `backend/tests/system/test_continuous_episode_vertical.py`：真实多相机媒体 + Temporal 垂直链路，补新包提交和下载。
- `backend/tests/annotation/`、`backend/tests/dataset_registry/`：修订/审核绑定、租户权限、来源投影、独立人工数据审核。
- `frontend/src/pages/p06-dataset-detail/`、`p08-data-annotation/`、`p21-data-export/`、`p23-recording-segmentation/` 的相关测试及真实 API 浏览器流程。

建议在隔离环境执行以下仓库检查；新模块路径由实现补入定向测试：

```bash
# Python 命令需在已经安装锁定依赖的环境中执行。
python -m pytest -q -c backend/pyproject.toml \
  backend/tests/lance_catalog backend/tests/workflow \
  backend/tests/publishing backend/tests/annotation \
  backend/tests/dataset_registry backend/tests/continuous_recordings \
  backend/tests/robot_ingest

python -m pytest -q -c backend/pyproject.toml \
  backend/tests/system/test_continuous_episode_vertical.py

pnpm --dir frontend typecheck
pnpm --dir frontend exec vitest run --reporter=dot
pnpm --dir frontend gen:api --check
VITE_MOCK_MODE=off VITE_RELEASE_ENV=production pnpm --dir frontend build
git diff --check
```

集成测试需要真实 PostgreSQL/MinIO/Temporal 和其规定配置；缺依赖产生的 skip 必须单列。相关检查通过后只在新改动或未解决问题需要时重复测试。

官方 reader 验证使用独立 Python 3.12 / LeRobot 0.6.1 验证环境作为初始兼容基线，实际运行记录完整锁定版本。生产服务不因此引入训练依赖。只验证 dataset/reader/DataLoader，不要求运行现有兼容脚本里的 ACT 训练环节。用复制出来的 RGB-D 标准根目录，禁用平台服务和网络回源读取；官方 reader 读取 RGB 与 HEVC 深度，另以离线示例读取并应用 HC 有效掩码。RGB 子集和重组集合独立验证。报告必须区分“文件完整”“reader 可读”“模型已使用深度”；G1 不以 DataLoader 通过宣称第三项完成。

## 9. 执行报告最低格式

```text
目标：G1 / G2 / G3
基线与结果：源提交、分支/工作树、结果提交
跨仓库接入：R1–R5 状态、平台/manager/camera/uploader 版本和安装路径、实际修改文件、兼容与回退组合
运行环境：隔离/8088、镜像/依赖版本、是否启用 Mock
需求编号：实现 / 软件验证 / 真实数据验证 / 真人真机验收状态
证据：命令、样本身份和哈希、结果、日志/截图/reader 报告
数据库与文件：新增/修改/迁移数量，未完成批次和原因
回退：已演练步骤、兼容范围、恢复结果
未覆盖：具体需求与缺失条件，不能只写“后续优化”
```

可复制的执行目标见 [chayi-goal-prompts.md](chayi-goal-prompts.md)。
