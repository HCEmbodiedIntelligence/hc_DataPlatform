# 差异实施的 `/goal` 指令

先执行 G1；完成后按实际报告接续 G2、G3。三个目标顺序衔接，不要在同一业务目录同时启动它们。

**机器人接入补充：G1 包含 `/home/hc_op/workspace/teleop_ws/src` 的 R1–R4，G2 包含联合部署 R5，详见 [具体修改清单](chayi-teleop-integration.md)。已复制旧 G1 的终端须同步此范围；录制、封存、独立上传和平台处理共同验收。**

**深度最终决策：用户选择 HEVC + 12 位量化，以 MP4 保存。每个 RGB-D 相机的 RGB 和深度各是一条独立 MP4 视频流，通过时间与帧索引同步。主包采用 LeRobot v3，原始录制保留；本版取代先前要求额外生成 16 位 MKV/源精度数组的 G1。已复制旧指令的执行终端须同步此决策。**

## G1：机器人录制接入、P0 多模态数据与隔离验收

```text
/goal 依据 /home/hc_op/workspace/hc_DataPlatform/docs/chayi.md、/home/hc_op/workspace/hc_DataPlatform/docs/chayi-implementation-plan.md 和 /home/hc_op/workspace/hc_DataPlatform/docs/chayi-teleop-integration.md，以上述方案的“深度最终决策”和机器人接入补充为准，完成 G1：W0–W6、W7 迁移工具及隔离演练、W8 软件验收，以及机器人接入 R1–R4。先核对源码与运行环境，在独立分支/工作树和隔离服务实施；上述三份文件及 docs/chayi-goal-prompts.md 可能未跟踪，须复制到执行工作树。

范围同时包括 hc_DataPlatform 和 /home/hc_op/workspace/teleop_ws/src 下的 humanoid_manager（含 upload_client）、humanoid_camera；motion_server/hc_teleop_recv 的录制事件与动作证据接口必须回归，发现明确缺口才修改。机器人各 Git 仓库独立建工作树/提交，使用独立 ROS 工作区、安装目录、ROS 域、manager 状态和上传队列。按 R1 冻结录制/单位/时间/身份/封存/上传/回执契约；明确历史 vr_target=aligned 对应 OpenArm raw Session、raw 对应旧诊断录制，正确配置网页/VR 路由与后台封存并修正过时反馈。按 R2 修正深度 scale/编码来源校验、跨端 256 资产限制及长录制容量，改善等待切片/已切片的显示和恢复查询；保留旧包、旧 API 严格 schema、冻结身份和持久队列兼容。机器人保留原始深度 MCAP 和分段 RGB MP4，平台再生成标准深度，不把重编码/对齐放入采集回调。必须通过真正的独立上传器接通录制→封存→上传→平台登记→整段/切片→Episode→审核/Tag→下载；不能以手工上传替代。修改平台内部 PASS 回执的强制 Lance 依赖，公开旧接口保持兼容。

新主存储为独立 LeRobot v3 Episode 标准包，包含数值、RGB 和已采集深度及 HC 来源/标定/有效性扩展；不写新 Lance，兼容历史读取。每个 RGB-D 相机的 RGB 与深度分别保存为独立 MP4 流。深度使用 HEVC Main 12、gray12le、12 位码值0～4095，默认对数量化、depth_min=0.01m、depth_max=10m、shift=3.5m、x265 lossless=1；按实际源 depth_scale 换算并显式记录单位/profile，参数变化产生新版本。复用 holobrain_depth.py 和 openarm_raw/prepare.py，补量化前无效掩码、缺帧/饱和状态、源帧映射、官方元数据与误差报告。原始录制原封保留，不要求另建16位MKV/源精度数组主副本；量化误差与视频编码无损分别验收。同profile且时间/哈希契约一致的已编码深度优先复用。

完成版本化标注JSON、subtask导出、三类独立审核、统一Episode详情、深度预览及反量化物理值读取、完整标准包就近下载和批量集合交付；RGB子集导出显式展示排除深度，完整下载包含深度视频/profile/掩码。接通MCAP、原生LeRobot、连续录制三条入口，保留Tag编辑/提交/审核。验证提交中断/重试/并发、租户授权、跨端封存上传和Worker故障恢复、旧队列、超过旧256资产限制的包、真实服务链路、离线官方reader/DataLoader直接读取RGB-D、独立应用HC掩码及1440/1024浏览器流程。记录17项P0及R1–R4证据与缺失条件；不把数据可读当作未指定模型已支持深度训练。解决提交前资产读取与READY授权条件的循环，不伪造Lance版本或放松授权。

持续完成平台及机器人侧G1可部署候选和迁移/回退交付，不以文案、Mock或合成小样例当完整验收。不修改8088现网业务数据或日常机器人安装/配置，不删除旧存储，不启动机器人运动或新真机采集。缺真实样本或真人条件时继续独立工作并列出未验收项。最终提供各仓库代码/提交、测试报告、实际改动文件、兼容版本矩阵及G2/R5联合部署说明，不执行G2/G3，也不把整个差异清单标成全部完成。
```

## G2：平台与机器人侧联合部署、历史数据迁移

```text
/goal 依据 /home/hc_op/workspace/hc_DataPlatform/docs/chayi-implementation-plan.md 的“深度最终决策”、/home/hc_op/workspace/hc_DataPlatform/docs/chayi-teleop-integration.md 的R5及G1实际报告，完成G2：将已验收候选部署到本机原平台8088，安装G1实际修改的机器人包和独立uploader，并分批迁移历史标准数据。核对各仓库分支/提交、已安装版本、录制/封存/上传任务、数据库与对象存储，验证备份可恢复；阻断缺陷先修复。使用 deploy/local/compose.sh original 对应的原 Compose project 和现存数据，保留其他工作区改动。

按R5先部署向后兼容服务端，验证现有uploader，再在录制安全空闲时安装已验证的manager/camera修改及独立uploader到teleop_ws日常安装路径，验证实际加载来源；保留配置、原录制、完成包、凭据引用及SQLite上传队列。核实真实OpenArm机器人、项目/任务、录制入口和封存/自动入队配置；已授权配置有效则复用，仍缺真实归属则完成独立工作并列出所需信息，不能套用38088 synthetic身份或改旧包绑定。验证新旧小包/队列恢复、服务端支持后启用大包能力、录制页面和切片/Episode入口；回退覆盖机器人包、uploader、API、数据库兼容组合。

完成兼容数据库迁移、工作流排空或版本兼容、LeRobot v3新写入切换、历史inventory/dry-run、逐批转换核对、checkpoint续跑、指针切换及回退演练。深度主包使用HEVC+12位量化MP4，与RGB分流保存；逐批验证编码码值、反量化误差、profile/单位、无效掩码、时间、标定及标签/旧审核引用。已有HEVC12位深度契约一致则原样复用；历史MKV/数组转换时保留原件并记录误差。证据不足的项如实记录，不补造或报告原始深度无损。验证新旧数据回放、标注、完整标准包下载及模态筛选导出。

保留旧Lance、媒体、原件、Docker数据卷和机器人上传队列，不删除回收，不启动机器人运动或新真机采集；回退后已提交数据和待上传任务仍可读。最终提供各仓库实际版本/运行安装路径、真实配置状态、现网证据、迁移成功/失败/未覆盖数量、回退证据及剩余验收项；新真机采集验收与软件接入分别报告，仅真实通过的项目标记完成。
```

## G3：语义编辑、步骤视图、投影和性能

```text
/goal 在 G1/G2 已有成果上，依据 /home/hc_op/workspace/hc_DataPlatform/docs/chayi-implementation-plan.md 完成 G3 四项 P1：ANN-04 机器人语义表单与校验后外部标注导入，UI-04 步骤/标签双视图与视频/时间轴/审核意见同步定位，UI-05 经过标定与同步验证的投影和异常定位，PERF-01 同源性能测量。复用现有 Tag Schema、编辑和审核闭环，导入生成新修订，不覆盖历史修订；缺标定/同步/深度单位时显示具体原因。统一业务导航并保留旧路由、权限和返回链接。在隔离工作树中实现与验收，记录真实短/长/多相机数据上的处理耗时、峰值内存、空间、对象数、首帧、seek、同步误差和掉帧，冷暖缓存分开，根据数据优化；不预设优于旧方案。保持不涉及训练和通用 2D/3D 编辑器的范围。最终提交代码和逐项证据，明确实际样本和真人/真机未覆盖范围。
```
