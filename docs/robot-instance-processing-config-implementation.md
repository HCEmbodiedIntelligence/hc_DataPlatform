# 机器人实例采集与解析配置实施记录

实施日期：2026-10-20。采用[主方案 A–G](robot-instance-processing-config-proposal.md)，包含完整纯关节模式；接入与任务录制背景仅用于兼容接口。以下 18 项「通过」主要指第一阶段隔离环境验收。后续用户明确要求处理 014、确认 19 个控制关节并要求简化界面，已在业务平台完成配置发布和单条同源关联；最新实际状态、分步操作与截图见 [X1 / 014 处理与配置指南](robot-014-processing-and-configuration-guide.md)。

## 工作区、依赖和保护边界

- 平台起点：`main / 068ede3be43768fb5683ae74b08aee4f611062d0`；机器人端起点：`main / 5d23f0562ee1636d0f7418e0bf80df61e8384fb5`。
- 开始前检查了祖先目录及相关实现目录，未发现适用的 `AGENTS.md`。已读主方案全文及两份背景文档。
- 初始平台 86 个、机器人端 21 个未提交文件已逐文件保存 SHA256、patch 和原文件归档。初始全部未提交依赖均带入隔离 worktree，而不是只取 HEAD。
- 原工作区正被业务容器热加载，因此所有实现与运行验证使用隔离目录：`/home/hc_op/workspace/.worktrees/robot-processing-config-20261020/{hc_DataPlatform,HC-teleop-robotic}`，分支均为 `implementation/robot-processing-config-20261020`。
- 其他终端在实施期间继续修改了凭据相关代码、接口生成物、文档，以及机器人凭据目录检查和 HTTP 错误分类。已记录凭据、连接地址及交付时机器人页面布局三次后续依赖快照并三方合并到隔离工作区；未覆盖或回退原工作区。完整依赖快照、合并清单、任务增量补丁在 `/home/hc_op/workspace/reports/robot-processing-config-20261020`。
- 第一阶段没有部署业务服务或写入真实历史数据。第二阶段按用户后续授权，将平台任务增量和分步 UI 更新到运行目录，安装 024–026、重启 API/worker，并只执行 014 的同源关联和用户确认的 X1 19 关节默认配置发布。未提交或合并 git 分支，未为测试重复上传 X1，未改写已有 Episode、质检或数据版本。详细数据边界见新指南。

## 初查与实施结果

初查已有组织机器人、接入流程、任务录制归属、MCAP 持久队列与历史处理快照、工作台关节流读取。纯关节仅有局部解析修改，后续资产合同、预览、切片、READY 约束和导出尚未贯通；重试会重新选默认配置，关节集合有取交集行为；缺实例配置版本及迁移工具。

本轮完成：

1. 组织 + 机器人 + 具名方案配置服务，型号版本/采集软件模板，草稿修订，实际样本验证，不可变发布版本，默认选择和修改审计。草稿用修订号检查冲突，发布通过事务锁保证版本与默认选择一致；模板和发布历史由数据库禁止 UPDATE/DELETE。
2. 实例详情、新建实例与机器人接入复用 `ProcessingConfigEditor`。模板不会从 URDF 猜 topic、单位或动作来源；单个匹配模板可带入并由用户确认保存。无样本或离线仅保存草稿，不伪造在线验证。
3. 上传记录改为读取本批次快照、版本、历史处理和具体错误；提供原始通道、版本差异、显式重试与实例跳转。首页问题、任务采集记录、上传记录、数据集入口共用同一组件，返回时用原始数据 ID 重新打开确切批次。
4. 明确状态/动作角色、完整训练关节布局、每条消息维度、相机类型/必需性和深度单位依据。未配置角色列候选；必需 RGB 不存在或零消息报数据缺失，不把填入不存在的 topic 当作修复。首版发布不支持派生动作，默认关闭。
5. `JOINT_ONLY` 使用 `recording-config/v2`、`continuous-recording-upload/v3`、`mcap-native/2`；零相机仅在显式合同下合法。声明 `STATE_LOG_TIME`，保留原始时间、缺失值和有效性证据，不压缩间断、不生成占位视频。资产登记、预览、传感器窗口、时间切片、QC、对齐、Episode 包、READY 数据库约束及导出均已贯通。
6. 纯关节工作台展示关节曲线和时间切片，缺失样本使曲线断开；图像检查为 `skip / NOT_APPLICABLE_NO_IMAGES`、不参与判定，页面明确显示“不适用（本方案不含图像）”。任务可以声明必须有 RGB，平台预检、处理与资产登记都执行适用性约束。
7. 机器人开始录制时固定机器人、任务、配置摘要和引用。上传首次接收冻结快照；运行中和历史作业不随默认配置变化。旧客户端没有版本字段时按精确旧 scope 兼容，并保留内部快照；旧响应不强加新字段。未知版本仍保留原始上传，处理明确失败。
8. 机器人端从平台读取配置摘要，并针对录制订阅的实际健康状态检查必需 topic、类型、录制开关、消息数及新鲜度。保存解析配置不会启动驱动、更改 ROS 发布或运动控制。
9. 新迁移工具默认只读，需精确清单摘要与候选 ID 才写入。相同配置合并来源，不同配置保留全部非默认草稿；不按更新时间覆盖。显式同源关联保留成功 Episode、阻止重复准备/入库并去重采集统计；不删 Raw，不改 QC。

主要文件：

| 范围 | 文件 |
| --- | --- |
| 配置领域及迁移 CLI | `backend/src/hc_data_platform/robot_processing/{models,service,router,migration}.py` |
| 数据库 | `backend/migrations/ingest/024_robot_processing_configs.sql`、`025_joint_only_recordings.sql`、`026_mcap_stored_binding.sql` |
| 解析、合同、切片与处理 | `continuous_recordings/{mcap_native,asset_models,asset_repository,registration,service,processing,package}.py` |
| 固定版本、重试和兼容 | `robot_ingest/{models,service,connections,processing_dispatch}.py`、`lerobot_imports/{configuration,mcap_processing,processing,router}.py` |
| 质检与统计 | `quality/{models,evidence}.py`、`collection_tasks/captures.py`、`dashboard/{postgres,recording_projection}.py` |
| 配置与一致入口 | `frontend/src/features/robots/ProcessingConfigEditor.tsx`、`TaskDataRequirements.tsx`、`features/ingest/processing/`、`app/shell/PlatformShell.tsx` |
| 无视频工作台与 QC | `features/viewer/{EpisodeWorkbenchCore,SynchronizedEpisodeWorkbench}.tsx`、`pages/p23-recording-segmentation/`、`features/quality/` |
| 机器人端 | `middleware/core/{platform_connection,processing_preflight,task_recording,app}.py`、`static/platform_panel.js`、`upload_client/humanoid_upload/{artifacts.py,schemas/}` |

## 18 项验收

证据位于 `docs/validation/robot-processing-config-20261020/`，完整原始运行记录位于工作区报告目录。真实浏览器为 Firefox/WebDriver，对隔离 API 与真实 PostgreSQL/MinIO 操作；没有以 DOM 中存在按钮替代操作验证。

| # | 结果 | 证据与边界 |
| --- | --- | --- |
| 1 | 通过 | 浏览器新建实例，匹配型号模板自动带入并保存草稿；`13-new-instance-template-draft.png`、`browser-stage4.json`。模板保留明确版本 ID。 |
| 2 | 通过 | 接入详情与实例使用同一 robot/scheme v2，见 `12-onboarding-shared-config.png`；接入 PostgreSQL 测试覆盖已有设备选择、幂等重试与部分失败，不重复创建身份。 |
| 3 | 通过 | 离线实例草稿未验证时发布禁用；样本验证不会使用设备在线标志代替内容证据。无模板保留待填字段。见 stage4、配置发布拒绝测试。 |
| 4 | 通过 | 浏览器项目 B 读取同一 v2；真实非 owner 数据库角色验证组织 RLS；同项目 ID 的其他组织任务不会串入首页。任务数据要求仍按组织/项目/任务检查。 |
| 5 | 通过 | `test_candidates_never_assign_roles_or_intersect_joints`：缺状态/动作映射逐项指出并列候选，新增相似 topic 不自动选角色。 |
| 6 | 通过 | 同测试与实际解析校验按完整声明布局；缺少关节报 `MCAP_JOINT_LAYOUT_MISMATCH`，不取交集。 |
| 7 | 通过 | 不存在/注册但零消息的 RGB 均报 `MCAP_REQUIRED_RGB_MISSING`，保留 Raw，无 READY Episode；浏览器 `02-missing-rgb.png`、`05-original-channels.png`。 |
| 8 | 通过 | `test_multiple_rgb_candidates_and_depth_units` 验证多 RGB 无主路的候选错误；所有错误入口跳转实例并保留原包，stage5 四入口往返验证。 |
| 9 | 通过 | RGB、RGB-D 分别从机器人/网页两种入口走准备、授权预览、切片、QC、入库和全部导出；`test_bare_mcap_to_slice_package_and_all_exports`；不明深度单位拒绝。 |
| 10 | 通过 | 同一真实隔离样本经过浏览器切片提交及后台真实处理服务，0 视频、QC PASS、9 帧 Episode、LeRobot/MCAP/原始格式导出 READY。见 `browser-pipeline.json`、截图 09–11、19–20；缺失时间证据与曲线断开另有测试。 |
| 11 | 通过 | 任务视觉要求在预检和资产登记/处理拦截纯关节；配置测试及格式集成测试覆盖。浏览器勾选保存、重载及恢复测试任务，`21-task-visual-requirement.png`、stage6。 |
| 12 | 通过 | 上传创建后发布 v2，旧上传/处理仍是 v1，新上传为 v2；机器人端开始录制后改默认，已录制任务的 manifest 仍为 v1；并发发布只有一个默认。 |
| 13 | 通过 | 浏览器选择 v2 重试，保留 v1 缺图像错误及原始快照并新增 generation；见截图 06–07、stage2。仅使用隔离样本，真实历史包未执行。 |
| 14 | 通过 | 真实数据库测试同机器人 3 个旧 scope、2 个不同候选，保留冲突与来源；重复应用返回 ALREADY_APPLIED；摘要/候选不匹配拒绝，没有新增 outbox。真实数据仅 dry-run。 |
| 15 | 通过 | 上传权限不能写实例默认配置，接口返回 403；新接口匿名 401、跨组织拒绝；批次通道/快照/可重试版本使用 upload.read 和原包所属 scope。 |
| 16 | 通过 | Firefox 从数据集、任务、上传、首页实际点击到同 robot+scheme+sample，再返回确切 Raw 抽屉；`browser-stage5.json`、四张 `18-*-return.png`。 |
| 17 | 通过 | 数据库测试显式同源关联重复执行幂等，成功 Episode 行完全不变，无新增处理 outbox；两次上传只计一次采集/一次 READY；真实 X1 后续已执行用户授权的单条关联；成功结果仍 READY/PASS/114 帧且更新时间不变，任务显示 2 次上传、1 次采集、1 条 READY。见新指南及 `after-verification.json`。 |
| 18 | 通过 | 旧 RGB/RGB-D、LeRobot、采集包、无版本客户端和原始导出回归；原始 MCAP 字节/时间/载荷保持可追溯；未知新版本保留 Raw。硬件和离线安装环境边界见下节。 |

## 验证环境、结果及可复现范围

隔离服务名为 `hc-robot-config-{pg,minio,api,front}`，网络为 `hc-robot-config-test`。端口分别为 5546、19546、8006、5180；仅绑定 loopback。测试组织与机器人均为随机/显式 synthetic 数据。浏览器真实保存与提交切片，由真实处理服务完成质检入库。另起独立 Temporal、worker、内部 HTTP 入口及专用临时库 `hc_config_worker`，完成真实 outbox→Temporal→worker→HTTP 登记、权限撤销、旧流程重试与工作流重放。未连接业务 Temporal，未验证生产调度部署。

- 后端最终回归：266 项通过。按最后执行结果汇总见 `backend-reconciled-final.xml` 与 `verification-summary.json`。初跑的 8 项环境失败及各次补验原始 XML 全部保留；缺 G0 清单、临时目录执行权限、Temporal namespace/地址、worker 与引导管理员问题均在隔离环境补齐后复测。
- 前端：最终 178 项通过（包括共享接入依赖及缺失曲线断开验证）；类型检查、54 项架构检查、15 项 API 生成检查及生产构建通过。
- 机器人端：Python 114 项通过，3 项缺少硬件交接目录而跳过；机器人页面 Node 集成 1 项通过，覆盖新默认 v2 摘要与录制中固定 v1 的同时显示；实际 multipart 中断恢复、原上传客户端、平台任务绑定和配置固定均覆盖。
- 旧客户端 G0 二进制包使用平台仓库已有固定 fixture；缺少的外部 G0 HTTP 示例由当前平台模型生成 synthetic 测试示例并明确标记，不冒称原始冻结交接数据。
- 全局安全门禁有 3 项既有失败，在原工作区基线复现同样失败：旧 annotation 文件路由未配置服务时先返回 503、旧矩阵覆盖缺口、既有 gap 列表断言。证据 `security-baseline.xml`；本轮新增配置/原通道/任务要求接口的鉴权和隔离专项通过。不能把整个仓库安全门禁写成通过。
- 尚未验证：真实机器人 ROS/相机/运动硬件联调、生产 Temporal 调度、外部硬件交接包、独立离线安装隔离测试。`test_isolation.py` 依赖额外的离线 wheel/bwrap，`test_web_hook.py` 要求独立安装的上传 CLI；两者未作为已通过项目。未驱动真实机器人。

运行命令与环境、最终增量检查见随附 `validation/robot-processing-config-20261020/README.md`。完整带依赖 worktree 可审阅；任务增量补丁以 `dependency-delivery` 最终依赖快照为基准，不应用整个工作区 diff 覆盖其他终端。

## 数据库迁移和历史清单

### 结构迁移（隔离验收后，014 阶段已安装到业务库）

| 文件 | 作用 | 历史数据写入 |
| --- | --- | --- |
| 024 | 配置方案、不可变版本/审计/模板、任务数据要求、同源关系及 RLS；处理表增加配置引用和通道盘点 | 无旧配置/Raw/Episode/QC 的 DML |
| 025 | 保留原 READY 证据约束，仅对明确 v2 JOINT_ONLY 合同允许 0 相机 | 不改历史结果 |
| 026 | 已存储且未绑定的 MCAP 可一次性固定任务/机器人；原始身份与后续归属不可改 | 不主动绑定任何旧包 |

现有迁移 021–023 属于带入的接入/凭据依赖，不能遗漏。发布时先查看迁移 status 和待执行清单，仅在确认目标库、备份及待执行清单后运行仓库 forward-only migrator；不要手动跳过 manifest 校验或修改既有 migration checksum。

```bash
# 在配置了目标 HC_POSTGRES_DSN 的后端运行环境内；014 阶段已执行并核验 165/165 current。
python -m hc_data_platform.core.migrations status
python -m hc_data_platform.core.migrations upgrade-expand
python -m hc_data_platform.core.migrations status
```

### 真实旧配置 dry-run

`historical-dry-run.json` 与最终只读复查完全一致，清单摘要：

`6f1bc864ea4a80ce8461e9fe04071d3124957b7855edac53b933e493fcdc2b6d`

目前 1 个旧配置候选，0 个跨 scope 配置冲突，1 个用户确认同源关系候选；历史两次失败处理快照均保留。

| 项目 | 值 / 待确认差异 |
| --- | --- |
| 组织/项目/Region | `01 / 01 / global` |
| 机器人 | `robot_fa73724c028e571795e06eba838738fa` |
| 旧状态、动作 | `/observation/joint_states`、`/action/joint_states` |
| 旧关节布局/单位 | 均为空，必须按实际数据确认，不能从型号猜测 |
| 旧主相机 | null；不能推断业务已批准纯关节，更不能补一个不存在的 topic |
| 采集类型/时间来源 | 旧配置未声明；迁移为待验证草稿，不自动批准 JOINT_ONLY |
| 历史候选 ID | `afc9ead66ee08d8cbb1c5330649319b7c228db6ad2a1eb3053e462315ff1e3ff` |
| 同源候选 ID | `2bc92210da4455ba16759b897bfe054f8cef1565bf60f316c9bb13f78627d71b` |

迁移工具用旧配置内容哈希分组，保留全部来源 scope、更新时间和原配置。更新时间只作为来源证据，不用于选胜出者。模板迁移不发布默认、不覆盖旧 profile，不改历史快照，也不派发处理。

```bash
# 默认只读；目标环境必须先支持新增表（但 dry-run 本身仅查询旧表）。
python -m hc_data_platform.robot_processing.migration --output /review/robot-config-inventory.json

# 仅在用户明确确认新鲜清单后创建 approval.json；两个数组都为空等于不应用。
# {"inventory_sha256":"<刚审核的摘要>","candidate_ids":[],"source_link_ids":[]}
python -m hc_data_platform.robot_processing.migration \
  --approval /review/approval.json --actor '<执行人>' --output /review/robot-config-applied.json
```

CLI 独立使用显式 `HC_POSTGRES_DSN` 指定的管理员数据库角色，要求该角色能够盘点全部目标 scope；不复用 HTTP 请求连接，不自动提升应用账号权限。CLI 每次重新盘点。摘要或候选 ID 不匹配直接拒绝；重复应用已确认候选不会生成重复草稿、版本、同源记录或处理任务。`source_link_ids` 只接受本次只读盘点里经相同组织/项目/Region/任务/机器人/dataset 及成功 Episode 核验的候选；当前内置核验用户指定的 X1 配对，其他录制不得仅按相似文件名扩大匹配。存在运行中、待执行或已准备完成作业时拒绝创建同源关联，需另行审查。

### X1 真实数据保护与恢复边界

- 失败原包 `riu-445ff5ed97e8529db900a3af16cd0ff5` / `teleop_20261019_154301.mcap` 没有图像消息，配置无法恢复未录入的图像。
- 成功 LeRobot Episode `rie-54102ae0b172579eafa375a28550c2b1` 最终只读核查为 **READY / PASS / 114 帧**，更新时间仍为 `2026-10-19 08:44:05.263076+00`；成功 Raw 同样 READY。见 `x1-protected-final.json`。
- 第一阶段仅 dry-run。014 阶段已安装结构表并执行这一条同源关联、创建已确认的模板与默认 v1；真实旧配置候选迁移和历史重试仍为 0 项。
- 若任务要求视觉：旧 MCAP 保持缺图像结论，继续保留 LeRobot 成功成果。
- 用户已明确确认接受 19 个控制关节纯关节方案。014 的 MCAP 已作为同次录制的原始证据关联到成功成果，阻止自动重复入库；现有 LeRobot 的 29 维状态/19 维动作保持原样。新默认配置不重写历史成果。
- 如确需另行恢复同源原始 MCAP 的预览/研究用途，应先决定目标产物与计数规则，形成新的明确清单。本次不擅自取消成功成果或重算旧质检。

## 发布及回退

当前平台已在 014 阶段按增量清单发布并安装 024–026，X1 模板、布局、纯关节模式和单条同源清单已按用户确认执行。后续发布待办：将已核验工作区变更按团队流程提交和发布到正式部署环境；机器人端实现仍需安装到运行环境并完成真实硬件联调。其他历史配置迁移及任何真实重处理仍需明确清单。新默认不改变已固定版本的录制或历史处理；没有有效配置的新机器人须补配置或选仅本地录制。

回退按 forward-only 数据保护执行：

1. 关闭新配置发布/写入入口、停止派发新的纯关节处理，保留 Raw、版本、审计与所有处理记录；不删除 024–026 表或强制收紧已被 v2 数据使用的约束。
2. 已采用新合同的队列由兼容版本 worker 完成或暂停；不能把 v2/v3 纯关节作业交给旧处理器。旧格式仍可交给兼容 worker。
3. 配置候选迁移只创建未发布草稿，回退时保留来源并不发布即可；旧 profile 和处理快照没有被替换。不得 UPDATE/DELETE 不可变发布版本或审计来伪装回退。
4. 同源关联若误选，先暂停相应原包的重试，核对应用报告、scope、Raw、canonical Episode、清单摘要和当前作业状态；经过明确确认后只撤销对应 `ingest.recording_source_links` 行并记录反向审计。撤销本身不恢复处理、不改 Episode 或 QC，之后任何重处理仍需另行确认。不要批量清空关系表。
5. 如果需要旧 UI，保持后端和 worker 对新合同的读取兼容，或隔离暂停新合同作业后再切换。禁止直接恢复整库备份覆盖本轮之后产生的业务成果。


## 2026-10-20 新建方案、实例删除和默认方案摘要修复

用户反馈“新建无效、删除无效、技术配置一开始太多”后，已将新建改为带名称/来源的弹窗并立即保存草稿；初始仅显示默认方案摘要，通道与关节等收起到高级设置。删除未使用实例时清理草稿和上传身份/凭证残留、项目与型号绑定，保留审计；有历史数据、已发布配置等时显示实际阻断项数量。发布与删除共用机器人锁，避免并发删除刚发布的配置。

本阶段已同步运行主目录，真实 LAN 页面验证通过；数据库写入、创建/发布、物理删除在隔离环境验证。26 项后端、7 项前端、54 项架构检查通过，主目录类型检查与 Vite 构建通过。014 仍为 2 次上传、1 条录制、1 条成功 Episode、0 次失败，Episode/QC 与旧处理快照不变。本阶段没有新增数据库迁移、业务配置版本、业务数据重处理或真实机器人删除。前述 18 项全链路验收是原阶段结果，本阶段针对这三项修复复验，未把它们冒充重新跑过的 18 项全链路结果。

操作路径、截图、代码清单、删除边界及回退见[本次修复记录](robot-config-usability-fixes-20261020.md)。


## 2026-10-20 11:06 接入详情入口补齐与 223 实际删除

上一轮只在实例目录验证了整机删除，遗漏用户在“数据源 → 机器人接入”详情的上传身份删除入口。数据库确认用户操作仅将 223 的上传身份标为已删除，实例仍在。本轮在接入列表与详情新增真正的机器人删除；原按钮改名“移除上传身份”并说明不会删除实例。详情默认设备概览，上传与连接、采集与解析配置独立分页；新建/选择方案工具条移入配置页的高级设置。确认删除窗口层级同时修正，避免被详情抽屉挡住。

用户指定 223 后，真实浏览器于北京时间 11:06:37 删除该实例（serial 23 / robot_194fb76c7d4d5085a873ef8c39220f23），API 返回不存在、实例/身份/配置/型号绑定各 0 行，接入和实例目录刷新均不再出现，其他机器人及 014 Episode 保持不变。此次真实业务操作为删除 223 一台；无数据库迁移、配置发布、数据重处理或其他机器人删除。25 项前端测试和 54 项架构检查通过，当前主目录类型检查、修改文件 ESLint 和实际/隔离浏览器验证通过。详见[独立记录与截图](robot-223-deletion-and-navigation-20261020.md)。

## 2026-10-20 删除阻断明细、任务归属与处理入口

接入目录和实例目录现在使用统一的删除前检查。关联清单显示文件、任务编号及名称、项目/区域、配置版本等真实记录，支持分页筛选和具体对象跳转；“处理方法”说明保留成果时停用/退役、任务及全部成果不再需要时在任务页单独确认删除，以及已发布版本不可清除等边界。任务链接通过稳定 ID 精确定位，不再依赖当前列表窗口。真实浏览器发现实例生命周期弹窗被详情遮挡后，已修复相关弹窗层级并实际打开、取消验证。

20 项后端/真实数据库专项、50 项前端测试、类型检查、代码检查、API 生成一致性、构建及隔离/当前业务 Firefox 验证通过。全仓安全门禁仍有与改前基线一致的 3 项既有失败，未标记为全仓通过。本阶段没有重跑原 18 项全链路验收。

本轮无数据库迁移、历史迁移、重处理或业务删除。当前 X1 实际为 3 条 Raw、3 条上传及 1 个发布版本，数据均归属任务 014；新增的 10 月 20 日 MCAP 在本轮验证前已存在。指定成功 Episode 仍为 READY/PASS/114 帧，前后机器人、版本、任务、Raw、上传、成功 Episode、2 条历史处理快照和同源关系逐行一致。处理步骤、截图、代码清单、验证与回退见[删除关联明细实施记录](robot-deletion-details-20261020.md)。
