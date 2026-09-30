# 历史 MCAP 相机消息兼容

2026-09-30，G1 隔离验证，源对象和数据库未写入。
实际来源与缺少的深度单位证据见 [历史调查](historical-native.md)。

`mcap_images.py` 现在接受明确的四种 ROS2 schema 名：
`sensor_msgs/Image`、`sensor_msgs/msg/Image`、`sensor_msgs/CompressedImage`、
`sensor_msgs/msg/CompressedImage`。仍由官方 `mcap_ros2` 动态 CDR decoder
按消息携带的 schema 解码，保留消息哈希、源 header/log/publish 时间和像素布局。
没有根据相机名、dtype 或外参平移单位推断深度比例。

CompressedImage 的深度只接受明确的 `16UC1; png` 或 `mono16; png` 布局，
且需已有版本化 `hc-depth-source/1` 源 profile 与编码声明一致。
PNG 的 IHDR 必须证明 16 位单通道，解码保留量化前 uint16 像素；非有限/零值等
有效性仍由后续源 profile 处理。`compressedDepth` transport、模糊布局、float
PNG、JPEG 深度或不同的 profile 编码拒绝，不以普通图像 decoder 替代专用
transport decoder。

[实际原件检查](historical-mcap-camera-contract.json) 全量读取并校验 MCAP CRC。
文件为 15584050 字节，SHA-256 为
`d170410ea8a2b63d8fc945219cedda0c62117f337e7fc73b05c8ad0e0e25b37a`。
111 帧 RGB 成功解码，源 header 时间与 MCAP log 时间分别保留。
131 帧 `sensor_msgs/CompressedImage` 深度到达正确的 PNG 分支，因没有源单位
profile 全部明确拒绝；不生成假定毫米的完整 RGB-D 包，也不静默改为 RGB 完整包。

32 项帧契约测试通过，包括两类 schema 名的 RGB/整数/浮点、字节序/行填充、
零和饱和值、NaN/Inf、显式比例 0.00025、单位缺失及 transport/layout 冲突。
源像素到投影往返相等，这些测试输入是合成的，不能替代真实深度量化/掩码验收。
[34 项最终测试及回归](historical-mcap-camera-tests.xml) 全部通过，无 skip/xfail；
包含隔离 PostgreSQL/MinIO/Temporal 的有单位/缺单位两条 MCAP 上传工作流、
提交后响应丢失恢复、租户/冻结资产拒绝和禁止新 Lance 写入。服务回归输入为
合成样本，实际历史文件未进行完整深度迁移。最初 decorator 放错导致 collection 失败，
以及调用不存在测试路径导致未执行的 XML 均保留在私有目录；不算通过项。

本阶段只补齐历史消息解码兼容。历史深度的实际源单位、量化前掩码、原件绑定
的驱动/配置证据及迁移仍待完成，真人/真机及模型深度训练支持未验收。
