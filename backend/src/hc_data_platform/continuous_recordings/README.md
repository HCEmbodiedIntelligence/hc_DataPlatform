# 连续录制、视频直传与 Episode 切片

该域把 2–3 小时或更长的设备录制视为一个不可变录制，不要求采集端提前拆成 Episode，
也不要求把相机画面逐帧写成 MCAP/JPEG。推荐的 v2 录制由多个独立 OSS 对象组成：

```text
recording
├── videos/front.mp4          RAW_VIDEO + camera_id
├── videos/wrist.mp4          RAW_VIDEO + camera_id
├── sensors/robot.mcap        SENSOR_DATA（不含永久 JPEG）
└── recording/config.json     RECORDING_CONFIG
```

## 数据服务流程

1. `POST .../continuous-recordings/uploads` 提交不可变录制清单。清单同时携带可查询的相机
   codec、fps、time base、clock domain，并用配置文件对象的 SHA-256 保留原始证据。
2. 服务为每个对象创建独立 OSS multipart upload；视频正文不经过 API 进程。
3. 客户端直传 OSS。完成每个资产时服务流式校验 size、CRC64、SHA-256；所有对象合格后才能
   `POST .../uploads/{upload_id}:commit`。
4. 模型可用 `POST .../{recording_id}/slice-proposals` 保存带模型版本和置信度的候选切片；
   人工用 `PUT .../{recording_id}/slice-draft` 复核、修改或直接创建切片。
5. `POST .../{recording_id}/slice-draft:finalize` 冻结 Episode。每个窗口使用相对录制起点的
   半开纳秒区间 `[start_offset_ns, end_offset_ns)`，允许间隙但不允许重叠。
6. 最终 Episode 写入全新的 `recording_episode_processing` 和
   `recording_episode_asset_windows`，初始状态为 `PENDING_QC`，后续进入质检与对齐。
7. `GET .../video-sources` 为人工切片工作台签发整段原始视频 URL；切片前即可直接读取 OSS。
8. `GET .../sensor-window` 使用 MCAP 索引与对象存储 Range GET，按录制相对时间窗读取原始
   `SENSOR_DATA` Topic；它不依赖 Episode、质检、对齐或 Lance，可直接驱动切片工作台的 URDF。
9. `GET .../episodes/{episode_id}/video-sources` 签发原始视频 URL 和 Episode 时间窗；预览不再生成一份
   派生视频。

Episode 的“切割”是不可变 EDL/软时间窗，不覆盖 Raw，也不立即复制大型视频。训练发布确实
需要独立文件时，再从冻结的时间窗物化派生片段。旧的单 `CAPTURE_BUNDLE` +
`upload_session_id` 接口继续只作为兼容入口，新录制默认使用 v2 多对象接口。
