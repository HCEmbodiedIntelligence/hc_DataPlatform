# 预览模块（BE-10）

`PreviewService` 通过消费端口 `StepReaderPort` 读取逻辑步骤，通过消费端口
`EffectiveExclusionPort` 读取不可变标注修订，再把明确的渲染帧交给
`MediaEncoderPort`。`LanceStepReaderAdapter` 和 `AnnotationExclusionAdapter`
用于衔接真实的 BE-08 与 BE-09 提供方签名，而不修改任何一个提供方。

`FFmpegHlsEncoder` 会将每张图像标准化为选定配置，为对比模式下的排除帧添加醒目的红色边框，
并生成使用 CMAF/fMP4 初始化片段和媒体片段的 H.264 HLS。它通过参数数组调用 FFmpeg，
绝不使用 Shell。输出先在缓存根目录下的临时目录中构建，完成后再以原子方式重命名到目标位置；
编码失败会清理临时目录，不可能发布播放列表或不完整的片段集。

缓存键包含项目、数据集、rollout、Lance 版本、标注修订、相机、视图模式、频率、请求窗口和
完整编码配置。缓存产物默认保留 24 小时；签名 URL 的有效期最多为 15 分钟，并在查询会话时刷新。
无效的图像步骤绝不会被静默丢弃：编码器会收到带有 `invalid_reason` 的醒目占位图，描述信息则返回
其源步骤/播放帧。

预览媒体和缓存记录只是临时运行产物。它们不是数据集版本、永久资产、发布输入或训练源。
运行时镜像必须按照 `backend/docs/dep-requests/BE-10.md` 的要求提供 FFmpeg/FFprobe。

使用 `pytest tests/preview` 运行隔离测试。当 FFmpeg 或 FFprobe 不可用时，
真实媒体集成测试会被跳过。
