# Unitree G1 原生录制格式

机器端不强制生成 Parquet。推荐的不可变 Raw 目录是：

```text
recording/
├── videos/
│   ├── head_stereo_left.mp4
│   ├── head_stereo_right.mp4
│   ├── wrist_left.mp4
│   └── wrist_right.mp4
├── sensors/robot.mcap
├── recording/config.json
└── recording-upload.json
```

- MP4 保存相机或硬件编码器的原始输出，打包时只复制字节，不重新编码。
- MCAP 保存带纳秒时间的状态、动作、位姿等连续遥测，适合边采边追加和故障恢复。
- `config.json` 保存 codec、fps、分辨率、time base 和 clock domain。
- `recording-upload.json` 是平台 `continuous-recording-upload/v2` 多对象接口的完整清单，
  含每个对象的 size、SHA-256、CRC64 和分片数。

若机器程序已有 MCAP，可直接作为 `--telemetry` 输入；若暂时输出 JSONL，每行格式为：

```json
{"offset_ns":0,"topic":"/robot/joint_states","data":{"names":["joint-a"],"positions":[0.0]}}
```

打包示例：

```bash
PYTHONPATH=backend/src backend/.venv/bin/python \
  -m hc_data_platform.tools.native_unitree_g1_recording \
  --output-dir artifacts/native-g1-recording-001 \
  --project-id be22-hf-g1-video-20260819-02-p1 \
  --collection-task-id 14d16ba1-d95a-5ee3-aaa7-7b7d78091b52 \
  --robot-id robot-d1a17126-b495-59b8-bf48-0ccce0a6ffe7 \
  --device-id unitree-g1-001 \
  --capture-start 2026-08-31T10:00:00+08:00 \
  --telemetry /data/run-001/telemetry.mcap \
  --camera head_stereo_left=/data/run-001/head-left.mp4 \
  --camera head_stereo_right=/data/run-001/head-right.mp4 \
  --camera wrist_left=/data/run-001/wrist-left.mp4 \
  --camera wrist_right=/data/run-001/wrist-right.mp4
```

通过平台接口直传 OSS 并登记：

```bash
export HC_DATA_ACCESS_TOKEN='填写 Bearer Token'
PYTHONPATH=backend/src backend/.venv/bin/python \
  -m hc_data_platform.tools.native_recording_upload \
  --bundle-dir artifacts/native-g1-recording-001 \
  --project-id be22-hf-g1-video-20260819-02-p1 \
  --organization-id legacy-real-api-e2e \
  --region-code be22-hf-g1-video-20260819-02-cn \
  --api-base-url http://127.0.0.1:8000
```

Parquet 的价值主要在平台侧：列式压缩、只读取指定列、批量统计快、Schema 清晰，并且与
训练数据工具兼容。它不适合当作机器实时录制的强制格式，因为持续追加、异常断电恢复和
多路媒体同步更麻烦。平台可以从已经登记的 Raw 数据生成 Lance 或 Parquet 派生版本。
