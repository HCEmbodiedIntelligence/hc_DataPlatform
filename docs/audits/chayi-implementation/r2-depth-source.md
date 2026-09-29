# R2 深度来源、独立网格与标准化门禁：阶段证据

这是 G1 部分实现，17 项 P0 仍待完整验收。本轮没有实机采集、运动、8088 数据操作或 G2/G3 部署。

## 实现

机器人侧增加 `hc-depth-source/1`：编码、单位、实际米/码尺度、无效值、非有限值策略、RGB 注册状态及版本化来源依据。
不能从 uint16/float32 单独推断单位。mm=0.001、m=1；其它真实尺度使用 source_unit。
原始录制保留未知/冲突帧及数值原字节，保存 UNKNOWN/INVALID；元数据 NaN/Inf 保留位模式和字段路径。
历史读取仍兼容，旧 raw 不会被悄悄赋予已验证单位。

本机安装的 RealSense ROS 驱动为 4.57.7。核对
[同版本官方源码](https://github.com/realsenseai/realsense-ros/blob/4.57.7/realsense2_camera/src/base_realsense_node.cpp#L252-L287)
的 fix_depth_scale 及发布调用链后，camera 适配器对该版本 Z16 输出声明毫米。
未知驱动版本/格式保留原图但不附默认尺度。该依据是驱动输出规则，不是实机精度验收。
原始深度 topic 保持自己的视野，未声明已注册 RGB。

平台 `openarm-raw-platform/3` 在编码前逐帧核对 source profile、编码、尺度、dtype/shape、哈希及注册状态。
未知来源或相矛盾的声明阻止完整标准化；原始上传文件保留。先扫描实际深度网格，再创建编码器；RGB 和深度可有不同尺寸。
同一流网格改变会明确失败；来源 profile 可随帧版本化，最多 64 个版本，进入来源记录和深度报告。
量化后仍分别保存 gray12le HEVC 和 HC 掩码、源帧映射、量化误差与编码无损证明。
完整包校验将每帧源 profile 哈希、dtype、尺度绑定到报告中的版本；已有合法历史证据保持兼容。

## 验证

- 机器人来源/原始录制：23 passed，`r2-depth-source.xml`。实际写入 MCAP、MP4、seal，数据为合成消息，未启动 ROS 节点。
- 相机适配器/launch 参数解析：23 passed，`r2-camera-contract.xml`。含已确认和未知驱动来源的原像素转发。
- 平台原始录制处理：6 passed，`r2-openarm-raw.xml`。毫米 uint16 和米 float32、独立半分辨率深度、三相机、21 帧、原始文件哈希不变；旧未知来源明确拒绝。
- 深度/完整包/收据：28 passed，`r2-package-depth.xml`。含四类源 profile/尺度/dtype 篡改拒绝及原有量化和编码回归。
- 变更平台模块 Ruff、格式和 git diff --check 通过。

平台测试明确重建新的合成 fixture 并重新计算资产摘要，fixture 专用单位声明不应用到任何真实旧录制。
独立 camera colcon 构建成功，安装在 `teleop_ws-chayi-work/install/ros`。最初四项 launch 测试因未加载隔离安装失败，修正测试环境后全部通过。
首次构建有未使用 PYTHON_EXECUTABLE 的 CMake 警告，脚本已移除该参数。

## 边界与交付

纯契约模块在 manager 与平台之间逐字复制，SHA-256 为
`72e4c4dce3fa9d47cc0ed40b78c09eece37647474b0ad5f94fea5a60129eeab2`；来源提交在 ORIGIN.json 中记录。
版本 3 使用新的派生对象前缀，不覆盖版本 2 或原件；历史 READY 资源读取不变。
旧 raw 缺少单位依据时不能依靠本次更新自动补齐，仍需要可信来源信息；现有 Holobrain 真样本的尺度/帧映射缺口仍未消除。

R2 尚不等于整条 R3 上传/切片/新包链路完成，也不代表真实硬件验收。
官方 reader、HC 掩码及完整包的此前证据见对应报告；本轮未声称任何模型已支持深度训练。
G2 必须配套安装 camera/manager/平台版本，保留原件与旧派生对象；回退应用时保留这些新对象，不删除旧存储。
后续仍需完整链路、真实数据、标注/审核/批量交付、浏览器及迁移演练等 G1 工作。
