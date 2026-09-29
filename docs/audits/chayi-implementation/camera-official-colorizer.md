# 相机诊断显示统一采用 RealSense 官方 SDK

平台官方显示已在 `400ed913` 完成（见 `realsense-preview.md`）。本轮审查额外发现机器人 camera 诊断仍有手写色带和百分位灰度，因此同步改为 SDK。
这属于 G1 的补充软件工作，不是实机或全链路验收。

camera 阶段提交：`6918b4c69d99f52bb1d2b467f486d32e042a5234`。
配套 manager 来源契约：`2f93e232fd55dd74f7342d10c7f3b720bdf8a4f3`；平台来源/网格：`b39f3ca3`。

## 行为与证据

默认 Dynamic/Jet/直方图均衡；灰度用 SDK White to Black。全部像素来自 `rs.colorizer`，没有本地调色函数或备用色带。
动态颜色没有固定米刻度。原始 RGB 和深度 PNG 保留；平台灰度 HEVC、量化 profile、掩码和 RGB H.265 原件不变。
固定米范围须有匹配的受支持驱动单位证据，未知单位拒绝该模式；动态模式只作码值排序着色，临时 SDK 单位占位值明确标记为显示用途。

- 主机隔离 Python 3.10、固定 SDK、官方 ROS 消息类型：77 passed、无跳过，`robot-official-preview-regression.xml`。
  包含 46 项 R2 来源/适配器回归及 31 项显示/证据测试。
- 平台 Python 3.12 SDK 镜像：28 passed、3 项 ROS 命令测试不选入，`camera-official-colorizer.xml`。
  首次尝试因缺少 rclpy 跳过的三项随后已在主机完整通过。
- 相机 colcon 重建成功，输出仅位于 `teleop_ws-chayi-work/build|install/ros|log`。
- 原工作区 manager/camera Git 状态仍干净；平台原工作区只含原先未跟踪的四份输入文档。

测试输入为明确合成的数值/消息对象。像素比较对照直接调用未设置项目参数的 SDK 默认 colorizer，另比较其官方固定范围与灰度选项。
CLI 中断/异常保留测试使用回调替身，不充当真实机器人链路验收。

## 固定环境与复现

官方 SDK `2.58.4.10922`，cp310 wheel 为
`pyrealsense2-2.58.4.10922-cp310-cp310-manylinux1_x86_64.whl`，
SHA-256 `809d48b329890594ab158c924537d7ddd6b15acab0aa22d6ba29e974f09ddc9d`。
从 [PyPI 固定发行版](https://pypi.org/project/pyrealsense2/2.58.4.10922/) 取得元数据及官方文件，安装脚本先验证哈希。

```bash
bash deploy/chayi/robot-install-preview-sdk.sh
bash deploy/chayi/robot-build-camera.sh
bash deploy/chayi/robot-python-test.sh \
  test/test_depth_source.py test/test_e_raw_recording.py \
  ../humanoid_camera/test/test_depth_visualization.py \
  ../humanoid_camera/test/test_evidence.py \
  ../humanoid_camera/test/test_pipeline_diagnostics.py \
  ../humanoid_camera/test/test_driver_streams.py \
  ../humanoid_camera/test/test_depth_processing.py -q
```

SDK 仅安装到隔离 `install/preview-python`。测试环境禁用用户 site-packages，并只加载 ROS 发行版及隔离 camera 安装。
最初用户目录残留 SDK 导入产生 Bus error，原目录保持不变；排除它并安装校验后的 wheel 后解决。
之后 software_device 在沙箱内初始化 udev 监听被阻止，在取得工具沙箱外测试许可后完整通过；没有启动实际采集或机器人。
这些环境失败没有被计为通过，也没有通过自定义色带绕开。

G2 需要把固定 SDK 与对应 camera 包一起安装到计划中的运行解释器，执行离线自测后再进入实际验收；此处没有部署日常机器人或 8088。
回退应用时保留原始录制和旧证据，显示修改不要求删除/重写任何主存储。
真实样本、R3 全链路、1440/1024 浏览器、真人审核及迁移演练仍待完成，17 项 P0 不标记全通过。
