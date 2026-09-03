# Hugging Face DROID 多相机数据导入

`hc_data_platform.tools.hf_droid_to_mcap` 把 Hugging Face 上的
`aractingi/droid_100` 单个 episode 转成平台上传页可直接选择的目录。这个
100 条版本保留了 DROID 的 7 轴真实关节位置；`lerobot/droid_100` 的简化状态是
末端笛卡尔位姿，不适合直接驱动 Franka URDF。

```text
hf-droid-package-000000-<稳定摘要>/
├── recording-config.json
├── recording.mcap
└── rollout_manifest.json
```

数据包包含三路同步相机（腕部、外部 1、外部 2）、状态、关节角、动作以及
Hugging Face 来源/版本信息。7 个 Topic 全部在 Manifest 中声明为必需 Topic。

`recording-config.json` 是录制/数据配置，不是机器人模型配置。它完整保留源
`meta/info.json`，并单独记录本 episode 的源时间戳基准、名义频率、实际帧间隔、
相机编码/尺寸、关节与动作维度。当前数据明确声明 `fps=15`，三路视频也均为
15 Hz；转换器使用 episode 自带时间戳，不假定 30 Hz。源配置的 `robot_type`
为空且未提供 URDF，配置文件会明确写入 `dataset_declares_urdf: false`。

在仓库根目录运行：

```bash
backend/.venv/bin/python -m hc_data_platform.tools.hf_droid_to_mcap \
  --project-id be22-hf-g1-video-20260819-02-p1 \
  --collection-task-id 14d16ba1-d95a-5ee3-aaa7-7b7d78091b52 \
  --episode 0 \
  --revision e86f5657cac0cd48c509543e4c14c6a31352b0cc
```

episode 0 验收完成后，同一个脚本可断点续跑并生成 100 个独立数据包：

```bash
backend/.venv/bin/python -m hc_data_platform.tools.hf_droid_to_mcap \
  --project-id be22-hf-g1-video-20260819-02-p1 \
  --collection-task-id 14d16ba1-d95a-5ee3-aaa7-7b7d78091b52 \
  --all-episodes \
  --episode-count 100 \
  --revision e86f5657cac0cd48c509543e4c14c6a31352b0cc \
  --output-dir artifacts/hf-droid-mcap-100
```

首次执行会把当前数据集 revision 解析成不可变提交 SHA，并缓存所选 episode 的
Parquet 和三路视频；后续执行复用缓存。每条输出对应一个 Manifest/MCAP，选择
总输出目录时上传页可一次发现全部 100 条。脚本在公布输出目录前会完成：

1. Manifest v1 的正式后端模型校验；
2. MCAP framing、索引、CRC、Schema、Topic 和 JSON 样本校验；
3. MCAP 与录制配置的文件大小、SHA-256、CRC64/ECMA 和 Manifest 一致性校验。

DROID 的 Hugging Face 子集和官方采集仓库当前都没有提供可对应到本 episode 的
整机 URDF。因此脚本默认不下载 URDF。只有显式加入
`--include-compatible-robot-model` 时，才会从固定提交拉取 MIT 许可的第三方
Franka Panda + Robotiq 2F-85 模型并生成 primitive 几何预览。该目录的
`MODEL_PROVENANCE.md` 和 `joint-mapping.json` 会明确标记
`THIRD_PARTY_COMPATIBILITY_PREVIEW`；它可用于界面联调，但不能作为 DROID、
Franka 或 Robotiq 的官方真实模型/标定事实。

输出成功后，在“数据上传 → 新建上传”中选择 `artifacts/hf-droid-mcap-100`
（可一次发现多个 episode），或直接选择其下的单个数据包目录。Manifest 的
`task_id` 已固定为目标采集任务，因此 Raw 提交和自动处理完成后会同时进入该采集
任务的数据计数、数据集版本和标注任务。

数据来源：<https://huggingface.co/datasets/aractingi/droid_100>，Apache-2.0；
可选兼容模型来源：<https://github.com/generative-skill-chaining/gsc-code>，MIT；
它不是数据集随附或官方 DROID URDF。
