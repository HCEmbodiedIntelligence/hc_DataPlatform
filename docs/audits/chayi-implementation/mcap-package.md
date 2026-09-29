# MCAP RGB-D 标准包阶段证据

日期：2026-09-29。执行分支 `feat/chayi-g1`，仅 `hc-chayi-g1` 隔离服务。
本阶段是 W2 的实施与软件验证，**不是 G1、17 项 P0 或真实相机验收完成**。

新派发 MCAP 已经经过真实上传服务、MinIO multipart HTTP、Raw 提交、Temporal、
校验/QC/对齐、独立 Episode 包、RealSense 预览及 Tag 任务进入成功状态。
运行时使用 Episode schema/collection；没有新 Lance 版本或借用的 Lance 编号。
已经持久化的旧工作流输入保留原分支，新输入显式使用 `lerobot_v3`。

## 数据和授权契约

- `mcap-episode-package/1` 冻结原 MCAP 哈希、上传身份、完整原 manifest、QC、
  schema、对齐 Arrow 哈希、相机映射、源 profile 及编码依赖。worker 在读取资产前
  重新核对已提交上传及组织/项目/区域；公开读取仍要求已提交完整包。
- 支持 JSON/JPEG RGB、带源 profile 的 16 位 PNG 深度，以及实际 ROS2 CDR
  `sensor_msgs/msg/Image` 的 RGB/BGR、16UC1/mono16、32FC1。检查大小端、行填充、
  字节数和尺寸边界。ROS `CompressedImage` 支持 RGB JPEG/PNG；
  `compressedDepth` 尚不支持，明确拒绝，不能把它当作普通 PNG 解码。
- 深度必须在消息 `source_profile` 或 channel metadata `hc.depth_source` 声明
  `hc-depth-source/1`；两处同时存在须一致。类型/名称不提供单位，未声明单位则
  处理失败并保留原件。源 profile 的具体字段见 `publishing/depth_source.py`。
- 每个相机按 manifest 显式识别，支持 `/head/color` 等非旧命名的 topic。
  RGB/深度分别成 MP4；深度沿用默认对数量化、0.01–10 m、shift 3.5 m、4095 码值、
  gray12le、x265 lossless=1。实际 codec profile 为 **Rext**，不伪写 Main 12。
- 先记录零值/声明的无效值/NaN/Inf 掩码，再量化；饱和与无效不同。量化误差和
  视频码值无损分别报告。临时对齐数据处理完可清理，不增加源精度主副本。
- 对齐使用 MCAP log_time；publish_time、ROS header stamp 和原 sequence 分别保留。
  原 sequence 全零也合法，另用稳定的逐 topic 帧序号识别重复帧。
  缺帧或时间阈值外的候选没有伪造源像素，候选时间和选择原因保留在 HC 引用。
- 源标定关联尚未验证时写 `UNKNOWN`。此阶段没有真实标定精度/相机同步验收。
- 深度显示调用官方 `pyrealsense2.colorizer` 2.58.4.10922，Dynamic/Jet；没有自写调色板。
  预览 receipt 包含 SDK/profile。H264 RGB 直接复用为浏览器预览；仅两路深度预览编码。
  原生/连续入口已有 H265 视频复用契约不因本 MCAP 解码路径改变。

新 MCAP 工作流 ID 以组织、项目、区域、rollout 完整元组生成 v2 哈希，避免跨组织
同名冲突。已登记 v1 ID 在上传确认重试时保留。派发前检查持久化 trigger/event；
终态重投递读取实际 job 结果，不重启工作流。历史 outbox JSON 缺组织时只从精确
查询得到的数据库行归属补全，不能采用客户端 payload 的组织声明。

## 实际验证

1. [真实服务测试](mcap-package-services.xml)：**2 passed**。明确合成的 30 帧 MCAP，
   1 RGB、2 depth（16UC1/32FC1）、state/action；实际源 scale 为 0.00025 m/unit。
   正常输入成功；缺单位时无包、无预览，仍可签名下载原件且 SHA256 一致。
   提交投影响应首次丢失，第二次恢复既有包；SDK 深度预览合计只编码两次。
   校验跨组织/伪造对象拒绝、错误 trigger 拒绝和终态重投递。
   此测试调用真实服务方法与专用 Temporal worker，不等同于浏览器/JWT HTTP 验收。
2. [解码/上传/派发回归](mcap-package-regression.xml)：**56 passed**；包含真实 CDR
   大小端/行填充/非有限值、缺帧选择原因、源帧引用不一致、旧上传确认重试。
3. [工作流和包提交回归](mcap-package-workflow-regression.xml)：**17 passed**，
   包括旧工作流、提交并发/恢复、Temporal history replay。
4. [原生 LeRobot 回归](mcap-package-native-regression.xml)：**1 passed**，
   原生完整 RGB-D 实际服务路径继续通过；另外 3 个条件未在这次回归选择。
5. [常驻 worker 审计](mcap-package-live.json)：另一条已经 Raw 提交、此前未启动
   的合成 MCAP 由恢复后的原样隔离 daemon 实际派发并处理为 SUCCEEDED。
   没有注入 activity 依赖。保留完整 Temporal history、Raw、包与预览哈希。
6. 两个独立包的断网官方 reader/DataLoader 都通过：
   [重试测试输出](mcap-package-offline-reader.json)、
   [常驻 worker 输出](mcap-package-live-offline-reader.json)。各核验 22 文件、30 帧、
   RGB 和 2 路深度；独立应用 HC 掩码分别排除 60/90 个无效像素，核对饱和标记、
   原 sequence、逐 topic 帧序号及三个时钟字段。未导入平台模块。
   对独立反量化的官方 reader 舍入误差最大 0.487777 mm；对合成源未饱和值的
   独立反量化误差最大 0.000737777 m。HEVC 解码码值哈希与编码报告一致。
   这些数值分别衡量 reader 舍入、量化和视频码值，不混称“物理深度全程无损”。

[测试包服务/对象审计](mcap-package.json)确认 live outbox 已确认投递、Raw 不变、
3 READY 预览及零 Lance 版本。失败测试留下的合成上传/任务保留用于排查，没有删除
旧对象或把技术失败改成成功。修复过程中的失败运行不计入上述通过数。

证据目录：`backend/.g1-artifacts/mcap-package/mcap-736cccbb897a465f90f704062f7d82ae`
和 `backend/.g1-artifacts/mcap-package-live/mcap-8d5675bc73764b4f8e74488cadad8335`。
前者版本 `dv_b929b3604ea7afa5401aed22c567d4169d9bb494a56999759378427ccab35614`，
后者版本 `dv_85c1cdc0a11de33edad5582e0a796b5e2c72e8379bcf670928e5ef4e66b84f56`。

## 后续条件和 G2 部署说明

G1 继续进行三类审核/统一详情、subtask 和批量交付、W7 副本迁移及 W8 浏览器等工作。
当前样例是合成输入；真实 MCAP 的源单位/标定契约、长录制资源测试、缺帧完整服务场景、
真实账号和 1440/1024 浏览器及真人条件仍需验证。不得据此称任何未指定模型支持深度训练。

本阶段不增加 SQL migration，隔离 migration 状态 current。G2 尚未执行；完整 G1
候选和其余 P0 软件条件收口后，部署时需同时更新 API/worker，保留原件、现有包及
工作流历史；检查固定的 PyAV/FFmpeg、RealSense SDK 和灰度 HEVC 编码能力。
老队列输入及新 `McapEpisodePlan` 需由能解析两者的 worker 处理，避免混用旧 worker。
回退时先停止新 MCAP 派发并保留队列/输入，继续提供已提交包和 Raw 的读取；不能直接
恢复旧 MCAP 写入默认值而继续接单，否则会再次写 Lance。不要删除数据、伪造版本或
修改已提交输入来完成回退。真实 G2 批次和 G3 均需后续指令。
