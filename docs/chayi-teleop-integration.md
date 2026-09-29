# teleop_ws 录制接入与跨仓库修改清单

日期：2026-09-28。配套：[总实施方案](chayi-implementation-plan.md)、[可复制 goal](chayi-goal-prompts.md)。这是执行要求与代码核查结果，不是实现或验收报告。

用户要求将 `/home/hc_op/workspace/teleop_ws` 的实际录制链路纳入平台完善范围。**R1–R4 是 G1 的必做项，R5 是 G2 的必做项；不能只用手工上传文件证明机器人接入完成。** 沿用 G1/G2/G3 编号，已复制旧 G1 的终端须补读本文件。

## 1. 接入边界与已核实事实

完整交付链路：

```text
网页 / VR 显式开始、停止和标记
  → humanoid_manager 的 CaptureHub / raw Session
  → 原始 MCAP（深度、state/action、时钟、来源事件）+ 分段 RGB MP4
  → 后台关闭文件、fsync、封存 recording-complete.json
  → 独立 humanoid-upload 持久队列、断点上传
  → 平台 robot_ingest / recording_bridge 登记连续录制
  → 原件查看下载 → 整段或窗口创建 Episode
  → 平台对齐、QC、LeRobot v3 独立标准包
      每相机 RGB MP4 + HEVC 12 位深度 MP4 + 有效掩码/来源映射
  → 审核、Tag、完整下载及批量交付
```

HEVC + 12 位量化在**平台标准化阶段**执行。机器人原始深度继续保存在 MCAP，不把编码标准包放入 ROS 采集回调，不因本次改造在机器人上增加在线对齐或训练帧重采样。平台 `openarm_raw/prepare.py` 已使用 HEVC 深度写入器，须和最终标准包统一 profile、掩码、单位及映射，避免重复量化。

`teleop_ws` 是 ROS 工作区，应对 `src` 下各 Git 仓库分别建隔离工作树、提交和记录版本；不能在工作区根目录假设有统一可用的 Git 仓库，也不编辑 `build/install/log` 作为源码修改。

| 仓库 | 本轮只读核查 HEAD | 角色 |
| --- | --- | --- |
| `hc_DataPlatform` | `4933200c1e09dd1f70ecbaabcfa9c6afa404e771` | 接收、处理、存储、浏览和交付 |
| `teleop_ws/src/humanoid_manager` | `ba16c3ee0ceff9834a89e55f46053ef51cc70016` | 录制、封存、管理页；包含独立 `upload_client` |
| `teleop_ws/src/humanoid_camera` | `0c0282a71ea1df314310086ab63b7fc1df436fcb` | RGB-D ROS 输入与深度/时间元数据 |
| `teleop_ws/src/humanoid_motion_server` | `dce1eae09293a368a0e46df341540133bf302e69` | 已发送动作、停止和失效证据 |
| `teleop_ws/src/hc_teleop_recv` | `506212d3430f7d26d877de479161219cae7f6db3` | VR 录制事件和夹爪动作证据 |

四个机器人源码仓库在本次核查时 `git status --short` 无输出；执行前重新检查，不以这里的版本覆盖后来修改。

核查发现的具体约束：

1. `CaptureHub.start()` 已创建 `raw_only=True` Session，但 VR 路由历史配置值 `vr_target=aligned` 才进入该接口；`vr_target=raw` 走旧 `TopicRecorder`。网页已区分“OpenArm 原始录制”和“旧 ROS 原始诊断录制”，默认仍是旧诊断路径且 `package_after_stop=false`。接入时须显式配置并验证，不能按字符串 raw/aligned 猜行为。
2. `humanoid_camera/scripts/timestamp_adapter.py` 固定写 `depth_scale=.001, invalid_value=0`；manager 的 `ros_images.py` 也有 `.001` 默认值，而图像解码器接受 `16UC1/mono16/32FC1`。这不是现有 RealSense 数据单位错误的证明，但缺少面向不同来源的单位依据与校验；需补齐才能可靠生成新深度包。
3. 封存、上传已支持原始文件哈希、冻结任务/机器人身份、重复通知和恢复；应复用。完成包 v2 使用 `openarm-raw-recording-upload/v1`，经 `robot-ingest/v1` 的 `CAPTURE_BUNDLE` 上传。
4. 原始录制资产清单在上传器和平台原始包契约中限为 256 项，通用 ingest schema 上限为 4096、默认上传策略仍为 256。按当前 60 秒 RGB 分块推算，3 路相机连续 2 小时约有 360 个视频文件，另加 MCAP/配置/完成标记，会超过现有原始包限制。长录制必须处理这个跨端限制，不能只测几秒样例。
5. 上传器对 `AWAITING_SLICE` 和 `SLICED` 都保存为 `awaiting_slice` 并停止后台轮询；网页总标题仍叫“等待平台切片”。上传完成不等于 Episode 包已完成，页面必须准确区分。
6. 平台 `processing_contract.py` 的内部 `EpisodeReceipt` 在 PASS 时强制要求 `dataset_version` 和 `lance_version`。这是改存储必须修改的阻断项。上传器公开 `processing-result` schema 则没有 Lance 字段，并且 `additionalProperties=false`；不能向旧公开响应直接塞新字段。
7. 平台 `continuous_recordings/openarm_raw/ORIGIN.json` 记录从机器人复制过来的对齐实现来源。两端不是自动共享一份库；契约变化须检查两份模型/校验器，并用共同样本验证。平台运行不能依赖导入本机 ROS 工作区。

## 2. 哪些文件需要修改

以下路径缩写：`M=teleop_ws/src/humanoid_manager`，`C=teleop_ws/src/humanoid_camera`，`P=hc_DataPlatform/backend/src/hc_data_platform`。均相对于 `/home/hc_op/workspace`。

“必须修改”表示已发现明确缺口；“验证后按需修改”表示必须纳入验收，但现有实现可能直接复用。每个执行报告须说明实际修改文件和未修改的依据。

| 部分 / 优先级 | 文件 | 具体工作 |
| --- | --- | --- |
| 深度来源契约：必须修改 | `C/scripts/timestamp_adapter.py`；`M/python/humanoid_manager/recording/ros_images.py`、`models.py`、`ros_bridge.py`；必要时对应配置入口 | 将 scale/单位和无效值绑定到已验证的驱动输出或显式来源配置，并记录依据；对合法毫米输出可保留 0.001，但不能把默认值当硬件测量事实。校验 encoding/dtype/shape/字节序和 scale；浮点米来源不能被误乘 0.001。缺依据时保留原件并明确阻断完整深度包，不能猜测 |
| 录制入口：必须修改提示与接入说明；路由按需修改 | `M/python/humanoid_manager/web/teleop_control.py`、`web/static/capture.js`、`recording/hub.py`；`M/docs/openarm-recording-v3.md`、`platform-upload-8088.md`、`capture_interfaces.md` | 修正“对齐采集”等过时反馈，说明历史 `aligned` 值实际选择平台 raw Session；保存接入配置时明确使用哪条录制路径、是否后台封存。旧配置若重命名必须兼容迁移，不静默把旧诊断录制改成另一模式 |
| 长录制资产限制：必须修改 | `M/upload_client/humanoid_upload/recording_bundle.py`、`schemas/openarm-recording-complete.schema.json`；`P/robot_ingest/recording_contract.py`、`models.py` 及策略配置；必要时 `P/continuous_recordings/asset_models.py` | 冻结有界的大包能力和最大录制规模，统一包内资产、额外完成标记和上传总资产计数。默认优先复用通用 ingest 的 4096 总资产上限，但须确认服务请求体、任务策略、客户端和接收端全部支持；保留旧小包兼容，新能力先部署服务端，再启用客户端。超限提前提示或显式分录制，不能丢文件或把部分资产冒充完整包 |
| 采集与封存：验证后按需修改 | `M/python/humanoid_manager/recording/profile.py`、`session.py`、`storage.py`、`raw_bundle.py`、`hub.py`、`job_process.py`；`web/runtime.py`、`capture_api.py` | 核对模态/单位/标定/时间/动作有效性进入原件和配置快照；停止后关闭本次输入、后台封存、下一段独立；封存完成才通知入队。新必需元数据未被保存时在这些边界补齐；不重写旧完成包、已冻结身份或哈希 |
| 上传显示：必须修改；传输主体复用 | `M/upload_client/humanoid_upload/worker.py`；`M/python/humanoid_manager/web/static/platform-upload.js`、`platform-upload.html`、`platform_upload_api.py` | 网页按持久 `recording_status` 区分等待切片/切片已确认，进入平台查看 Episode 处理结果；给出恢复查询入口，必要时增加用户触发刷新。保留终态停止自动轮询，不要求机器人端持续跟踪人工审核；不能把已接收显示为完整数据就绪 |
| 上传恢复与协议：验证后按需修改 | `M/upload_client/humanoid_upload/{recording_bundle,queue,http,worker,cli}.py`、`schemas/`；`M/upload_client/{README.md,INTEGRATION.md,config.example.json}` | 复用独立进程、分片、限速、冻结身份、幂等与重试；扩展大包限制的版本兼容与错误信息。若公开接口必须增加新字段/状态，使用新版本或显式能力协商，保留旧严格 schema 响应；旧队列可重启恢复 |
| 平台机器人接收：必须接通新写入并修改回执 | `P/robot_ingest/processing_contract.py`、`lerobot_processor.py`、`recording_bridge.py`、`processing_api.py`、`recording_contract.py` | PASS/READY 绑定真实已提交的数据版本、存储格式、manifest/content_hash；历史 Lance 回执兼容读取，新包不伪造 Lance 版本。保留 robot/task 授权、原始包登记与处理重试幂等；不误将原件接收/预览就绪当标准包就绪 |
| 平台 RGB-D 转换：必须修改 | `P/continuous_recordings/openarm_raw/prepare.py`、`models.py`、`config.py`、`ORIGIN.json`；`P/publishing/holobrain_depth.py`、`export_video.py`，以及 W1/W2 新包写入器 | 接收机器人真实原始深度，量化前生成有效掩码，记录缺帧/裁剪/单位、RGB-D 时间映射及标定；生成独立 RGB/深度 MP4，完整校验后发布。更新适配差异与来源记录，不伪称修改后的代码仍等于原始拷贝 |
| 相机驱动、VR 接收和运动控制：默认复用，仅发现数据证据缺口才改 | `C/launch/multi_camera.launch.py`、`C/python/humanoid_camera/transport.py`；`teleop_ws/src/hc_teleop_recv/hc_teleop_recv/{actions,node,peripheral_ros}.py`；`teleop_ws/src/humanoid_motion_server/src/humanoid_motion_control_node.cpp` | 验证相机元数据/QoS、开始停止标记、实际发送动作和 stop/invalid/epoch 证据被原样录下并被平台消费；无需因更换平台存储格式重写控制器、IK、伺服参数或 VR 协议 |

真实入口可直接定位：[录制调度](../../teleop_ws/src/humanoid_manager/python/humanoid_manager/recording/hub.py)、[VR 路由](../../teleop_ws/src/humanoid_manager/python/humanoid_manager/web/teleop_control.py)、[原始封存](../../teleop_ws/src/humanoid_manager/python/humanoid_manager/recording/raw_bundle.py)、[上传客户端](../../teleop_ws/src/humanoid_manager/upload_client/humanoid_upload/worker.py)、[相机元数据](../../teleop_ws/src/humanoid_camera/scripts/timestamp_adapter.py)、[平台处理回执](../backend/src/hc_data_platform/robot_ingest/processing_contract.py)。

## 3. G1 的 R1–R4

### R1：冻结跨端契约和配置

在 W0/W1 阶段完成。读取适用 AGENTS、记录各仓库/已安装版本；以实际运行源码为准，不以旧 README 的版本描述替代。需要修改的机器人仓库放入独立 ROS 工作区，例如 `/home/hc_op/workspace/teleop_ws-chayi-work/src`，并使用独立 build/install/log、ROS_DOMAIN_ID、manager 状态目录、录制根目录、上传队列及测试凭据。

冻结一份跨端接口样例及版本矩阵，覆盖：本地机器人与平台机器人/任务映射、session→完成包→upload/raw→recording→episode/data_version、相机名到 feature key、动作字段顺序与单位、时钟与 epoch、原始深度 scale/无效值/标定、manifest 资产上限、回执/错误/重试。组织/项目/地域以平台已授权任务解析，客户端不得任意改归属。

正确配置 OpenArm raw Session 路由和后台封存，自动入队与仅本地保存两种模式分别测试。没有真实平台身份时，使用隔离服务的测试身份继续软件工作；不能拿示例身份绑定正式新录制。

### R2：补机器人采集契约与上传接入

执行上表机器人侧必改项，检查可复用部分；补 schema/契约样例/错误处理和文档，产生各仓库实际提交。相机单位检查必须覆盖合法毫米整数、合法米浮点、无效 scale、零/NaN/Inf、声明与消息不一致，以及未注册到 RGB 像素的深度。对于当前 RealSense 路径按已核实驱动输出限定支持范围，不要求采集回调临时操作 SDK。

区分传感器像素无效、缺帧、操作员标记无效以及动作无效；原始记录完整保存。新增元数据按同一来源契约传到平台；旧来源缺少证据时如实标记，不补造标定/单位。

### R3：平台处理和兼容接通

与 W1/W2/W4/W5 同步实施。通过**真正的独立上传客户端**进入 platform API，不直接插库、复制对象或用普通手工上传代替。覆盖原始 recording 分支和历史原生 LeRobot processing 分支；内部回执去除新存储对 Lance 的依赖，公开接口继续兼容旧客户端。

原始接收、预览可用、切片确认、Episode 处理、完整包校验、自动 QC 和两类人工审核是不同状态。至少在机器人管理页显示录制接收/切片状态与平台入口，在平台页显示后续完整状态。平台标准包失败时原件仍可下载，处理重试复用已上传 Raw，不让机器人重新录制或重新上传成功数据。

### R4：跨仓库验证与部署候选

下列证据为 G1 收口条件：

| 验收 | 通过标准 |
| --- | --- |
| 两种开始/停止入口 | 网页与隔离 VR 事件走同一个 OpenArm raw Session 路径；未开始不录制，快速停止/再开始不串段；诊断路径明确且兼容 |
| 封存和通知恢复 | 写盘失败、未关闭文件、封存中断不产生可上传的假完成包；完成后入队失败、重复通知、进程重启不会丢任务或重复上传 |
| 上传与平台恢复 | 分片/commit 响应丢失、断网、客户端重启、令牌失效、平台/Worker 重启、处理失败后重试；成功原件的身份/哈希保持不变 |
| 身份与权限 | 本地/平台机器人映射和冻结任务一致；错误 robot/task、跨项目访问拒绝；变更配置不改旧队列身份 |
| RGB-D 完整链路 | 录制→封存→独立上传→原件查看→整段和多个窗口→标准包→审核/Tag→下载→官方 reader；核对源深度 scale、有效掩码、量化码值、反量化误差、RGB-D 帧映射 |
| 长录制多资产 | 至少一个超过旧 256 资产限制且在新额度内的多相机包成功；完成标记计数、分片恢复、峰值内存和超限拒绝均核对。加速生成的文件只证明软件容量，实际长录制吞吐另记 |
| 新旧兼容 | 新平台+现有 uploader；新 uploader+兼容旧 API 的旧小包；新大包只有服务端支持时启用；已有 v1/v2 完成包和 SQLite 上传队列可恢复 |
| 运行隔离 | 隔离 ROS/服务/状态目录/队列测试；生产 8088 与日常 install/config 未被 G1 修改。记录所加载的 Python 模块、ROS overlay 和 uploader 路径，避免误用旧安装 |

优先扩展现有测试：manager 的 `test/test_e_raw_recording.py`、`test_e_recording_cycles.py`、`test_e_capture_wiring.py`、`test_raw_camera_recording_transport.py`、`test_e_upload_server.py`、`test_teleop_motion.py` 及 `upload_client/tests/`；camera 的 `test/test_capture_transport.py`、`test_frame_trace.py`、`test_depth_processing.py` 并新增针对 scale/编码声明的有效验证；平台的 `backend/tests/robot_ingest/test_raw_recording_receipt.py`、`test_processing_contract.py`、`backend/tests/continuous_recordings/test_openarm_raw.py`、`test_openarm_frame_grid.py` 和真实服务垂直测试。

机器人控制源码未改也须回归其采集证据接口；无需因此重测全部运动算法。跨端软件测试可以使用明确标注的合成 ROS 输入；既有真实录制用于真实数据兼容验证。两者都不能替代新真机采集、VR 实物按钮及长时间控制时延的实测。缺少硬件窗口时完成其余工作并精确列出未验收项。

## 4. G2 的 R5：联合部署与真实配置

G2 同时部署平台及 G1 实际修改的机器人包/独立 uploader，不能只更新平台。先核对当前采集/封存/上传任务，备份相应代码版本、安装和配置，验证恢复；录制仍在进行时先等待安全空闲，不强制终止正在封存的文件。

顺序为：服务端先提供旧包/旧接口兼容 → 检查旧 uploader 可用 → 安装已验证的 manager/camera 修改与独立 uploader → 保留并恢复原队列 → 验证真实身份和目标任务后启用新能力。实际启动路径使用 `/home/hc_op/workspace/teleop_ws/install` 及其 `uploader`，源码更新后通过构建安装校验，不直接手改 install。

已有 [8088 接入说明](../../teleop_ws/src/humanoid_manager/docs/platform-upload-8088.md) 和 2026-09-28 安装交接曾记录：正式 OpenArm 身份/采集任务待指定、`platform_upload` 尚未启用。这是历史交接信息，G2 先核实是否已配置；已配置且授权有效则复用，不重复索要。仍缺归属时列出具体缺失信息，完成其他部署工作后请求用户提供目标项目/任务；不能随意使用 38088 synthetic 身份或当前其他机器人的身份。

回退保留原始录制、完成包、上传 SQLite 队列、凭据引用、平台对象与数据库；分别列出机器人包/uploader/API/数据库可兼容组合。大包能力一旦产生新队列，回退不能把客户端/服务端切回无法读取该队列的版本。停止新增上传不等于删除已有任务。

本次 `/goal` 不授权启动机器人运动或新采集。G2 可安装兼容软件、恢复已授权上传任务并检查页面/查询；首次真实采集、VR 实物与性能验收需要明确硬件窗口。报告区分软件接入完成、真实身份配置完成、已有真实数据贯通、新真机采集验收，不将它们合成一个“已完成”。

## 5. 交付报告增加的内容

在总方案状态报告中增加 R1–R5；每项记录机器人仓库/平台仓库提交、实际修改文件、保留复用依据、契约和安装版本、运行路径、测试样本和证据。交付一份机器人侧更新/配置/回退步骤，以及平台侧对应步骤。未完成真实配置或真机条件须列出具体影响，不能以平台页面通过代替机器人闭环。
