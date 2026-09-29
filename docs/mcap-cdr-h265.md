# 原始 MCAP 的 CDR 与 H.265 处理

原始 MCAP 可以生成上传清单，再走既有的上传、文件校验、质检、对齐及媒体处理流程。缺少任务或清单是接入配置问题，不代表文件格式不可解析。裸 MCAP 的网页原始归档入口仍只负责归档；需要处理时选择包含 `rollout_manifest.json` 的数据包。

支持 ROS2 `cdr` + `ros2msg` 消息：非相机消息使用 MCAP 官方动态解码器；相机读取 `sensor_msgs/msg/CompressedImage` 的 `format/data`，或 `foxglove_msgs/msg/CompressedVideo` 的 `format/data`。相机支持 JPEG、普通 PNG（包括 `16UC1; png`）、H.265/HEVC。保留相对 topic 名，不强制加 `/`。

H.265 输入要求每条消息包含一个 Annex-B access unit，支持独立参数包、帧间依赖、B 帧和文件结束时延迟输出；每个 channel 独立解码。先按 MCAP 物理消息顺序解码，再用临时 SQLite 按 MCAP `log_time` 排序供对齐使用，不把每个视频包当成独立图片。缺少关键帧、损坏及不能解码的帧会进入图像损坏质检。ROS header 时间戳保留在非相机原始字段中；跨设备时钟及 header/log_time 的关系需要另行校验。

原 MCAP 不重写。JPEG/PNG 原字节保留在短期对齐缓存中；H.265 解码帧暂存为 PNG，RGB 网页媒体输出 H.264。16 位深度 PNG 只有显式声明 `depth_unit: mm` 才生成深度媒体，使用已有 Holobrain `gray12le` HEVC 对数量化约定，携带完整深度元数据。派生深度视频经过量化，原始深度 PNG 保持不变。未声明单位的 16 位图不会静默转换成 RGB。浏览器能否直接播放 HEVC 取决于其解码能力。

以下输入需要额外适配：Protobuf 相机消息、AVCC/HVCC 长度前缀码流、任意网络碎片、ROS `compressedDepth` 的专用传输头。H.265 是视频编码，不代表自动支持所有消息封装。

## 为历史文件生成清单

在安装 backend `data` 依赖的环境运行：

```bash
python -m hc_data_platform.tools.mcap_manifest /path/to/mcap /path/to/packages \
  --project-id PROJECT --task-id TASK --robot-id HISTORICAL_ROBOT \
  --depth-unit mm --link-source
```

`--depth-unit mm` 必须由采集约定确认。`--link-source` 为同一文件系统中的原文件创建硬链接，不复制或重编码；修改任一硬链接都会修改原文件，因此这些包应只读使用。不需要硬链接时，可在网页同时选择生成的清单和对应 MCAP。`--resume` 仅复用生成内容相同的清单。

输出每个文件的 `rollout_manifest.json`、`processing-plan.json`，以及总索引。清单记录 SHA-256、OSS 兼容 CRC-64/XZ、文件长度、原 topic/schema/encoding 和确定性包 ID。可用时用 liblzma 加速 CRC，缺失时使用相同算法的 Python 实现。

上传前须在目标项目登记采集任务、计划内的质检配置，以及指向任务 dataset 的已发布 Tag Schema binding。现有任务解析器据此注册数据结构并触发工作流。生成工具不会虚构这些登记已完成。

默认计划是格式与连续性校验：输出对齐网格为 30 Hz，不把原始 15 Hz 等采样率自动判坏；必需通道超过约 0.5 秒的断帧会进入当前平台的风险报告。静态元数据不按周期传感器考核。当前通用 MCAP 投影未填充关节限位、动作语义等丰富质检输入，格式校验通过不等于训练质量合格。

LeRobot MP4 导入使用整数 PTS 与 time_base 检查边界，避免 ffprobe 六位小数显示舍入导致“少一帧”误报；仍会拒绝实际缺帧。
