# ADR-0003：ROS 2 编码视频从机器人录制、OSS 入湖到 Lance 软索引

- 状态：Proposed / 方案已形成，待机器人端能力验证后接受
- 日期：2026-08-20
- 责任域：Robot Recorder、ROS 2、rosbag2、MCAP、Ingest、OSS、Temporal、QC、Alignment、Lance、Preview、Publishing
- 关联决策：[ADR-0002：视频单份存储、帧级软索引与导出时物化](./ADR-0002-video-soft-index-and-export-materialization.md)
- 当前范围：机器人到服务端的完整视频数据链路；不包含前端交互设计和具体硬件编码器选型

## 一句话结论

机器人端把相机图像编码为 H.264/H.265 ROS 2 packet Topic，并通过 rosbag2 写入可独立解码的
分片 MCAP；MCAP 是 OSS 中唯一永久 Raw 视频表示。服务端流式读取并顺序解码完成完整性验证、
视频 QC 和时间对齐，Lance 只保存 `video_asset_id + segment_id + frame_id + PTS` 等软索引，
JPEG、MP4 和 HLS 只在预览或导出时临时物化。

```text
机器人
Camera Image
  → H.264/H.265 编码
  → ROS 2 Encoded Packet Topic
  → rosbag2 分片 MCAP
  → 本地待上传队列
          │
          ▼
上传与 Raw 存储
Manifest Preflight
  → OSS Multipart 直传
  → Size/CRC64/SHA-256 校验
  → Raw Package 原子提交
  → PostgreSQL Outbox
          │
          ▼
服务端处理
Temporal
  → Manifest/MCAP/视频流验证
  → 顺序解码与 QC
  → Frame Index 暂存
  → Alignment
  → Lance 原子提交
  → Annotation Task
```

## 背景与问题

当前机器人使用 ROS 2 Camera Topic，并由 rosbag2/MCAP 录制逐帧图像。MCAP 保存的是 Topic
实际发布的序列化消息，不会自动把 `sensor_msgs/msg/Image` 或
`sensor_msgs/msg/CompressedImage` 转换为帧间压缩视频：

- `sensor_msgs/msg/Image` 保存 RGB、BGR、YUV 或 Bayer 等原始像素，容量最大；
- `sensor_msgs/msg/CompressedImage` 通常保存每帧独立 JPEG/PNG，缺少跨帧压缩；
- H.264/H.265 packet Topic 才能利用相邻帧之间的时间冗余。

当前仓库的 469 帧、640×480、约 15.63 秒样本中：

- 原始 H.264 MP4 为 1,115,689 B；
- 逐帧 JPEG 字节合计为 11,034,410 B；
- 同时保存 JPEG Topic 和 MP4 Attachment 的 MCAP 为 12,163,699 B；
- MCAP 约为原始 H.264 的 10.9 倍。

问题的根因不是 MCAP 格式，而是 MCAP 内保存了未压缩图像或逐帧 JPEG，甚至同时保存了第二份
视频表示。Zstd 可以压缩 MCAP Chunk 和通用 ROS 消息，但不能替代 H.264/H.265 的帧间压缩。

## 目标

1. 保留 ROS 2 Topic、时间戳、Schema 和 rosbag2 回放语义。
2. 同一相机视频在正式 Raw 中只保存一种压缩表示。
3. 机器人可以边录制边上传，网络中断后可以断点续传。
4. 单个分片损坏、上传失败或机器人断电不影响整个 rollout。
5. Raw 在 QC 为 `RISK` 或 `REJECT` 时仍然保留并可审计。
6. QC 可以逐帧检测，但不永久保存解码像素或 JPEG。
7. 每个有效 Lance Camera step 都能稳定定位到 Raw MCAP 中的源视频帧。
8. Temporal history 不包含 MCAP 正文、H.264 packet 或稠密逐帧 JSON。
9. Preview 和 Publishing 可以按固定版本重放并生成目标格式。

## 非目标

- 不要求 MCAP Chunk 压缩进一步显著压缩 H.264/H.265 字节。
- 不允许同时永久保存 Image、JPEG、H.264 和 MP4 多种视频表示。
- 不要求浏览器直接播放 Raw MCAP。
- 不在机器人上永久生成逐帧 JPEG 作为训练数据。
- 不把 PostgreSQL 或 Lance 作为媒体正文存储。
- 不追溯重写已提交的 legacy Image/JPEG MCAP。
- 不在本 ADR 中决定所有机器人硬件的具体编码器名称和驱动参数。

## 决策

### 1. ROS 2 Camera Topic 使用帧间编码消息

默认采集链路为：

```text
/camera/<camera_id>/image_raw
  sensor_msgs/msg/Image
          │
          ▼
H.264/H.265 Encoder 或 image_transport Publisher
          │
          ▼
/camera/<camera_id>/image_raw/ffmpeg
  ffmpeg_image_transport_msgs/msg/FFMPEGPacket
```

机器人正式录制只选择一种 Camera 正文 Topic。采用编码 Topic 后，不得同时录制：

```text
/camera/<camera_id>/image_raw
/camera/<camera_id>/image_raw/compressed
/camera/<camera_id>/image_raw/ffmpeg
```

推荐录制集合为：

```text
/camera/<camera_id>/image_raw/ffmpeg
/camera/<camera_id>/camera_info
/tf
/tf_static
/clock                         # 仅在使用仿真时钟时
/joint_states、/action、/imu   # 按 rollout 合同选择
```

如果摄像头或采集卡能够直接输出 H.264/H.265，应直接封装原始编码 packet，不先解码为 ROS Image
再编码。如果只有 RGB/YUV/Bayer 图像，优先使用机器人已有的 GPU、NPU、VPU 或媒体编码单元；
软件编码只作为无硬件能力时的回退路径。

V1 默认使用 H.264，建议从以下合同开始验证：

- 每 1～2 秒一个 IDR/关键帧；
- 每个 MCAP 分片以可独立解码的 codec config 和关键帧开始；
- 编码消息保留原始 ROS `header.stamp`；
- 编码流保留 PTS、必要的 DTS、codec、宽高、像素格式和关键帧事实；
- 第一版可以关闭 B-frame 以简化 packet/frame 时间映射，后续在验证 PTS/DTS 合同后启用；
- 质量使用 CRF/CQ 或目标码率控制，具体值通过相同场景 A/B 样本决定。

若训练或合规要求像素级无损复现，应使用单独批准的 lossless 编码 Profile。普通 H.264/H.265
有损编码一旦成为 Raw 权威表示，就不能恢复编码前完全相同的 Image 字节。

### 2. rosbag2 生成可独立处理的 MCAP 分片

机器人不写一个持续数小时的单体 MCAP。默认按 1～5 分钟切分，具体时长由本地磁盘、网络和
单分片最大重传时间共同决定：

```text
rollout-001/
├── metadata.yaml
├── rollout-001_0.mcap
├── rollout-001_1.mcap
├── rollout-001_2.mcap
└── rollout_manifest.json
```

要求：

- 只上传已经关闭并完成 Summary/Index 的 sealed segment；
- 当前正在写入的 `.mcap` 不得进入上传队列；
- 每个分片具备完整 MCAP 索引，并能独立完成容器验证；
- 视频流跨分片时，新分片必须从可独立解码点开始；
- `metadata.yaml` 作为原始 rosbag2 元数据保留，以支持标准回放和审计；
- rollout 最终结束后才提交 package Manifest；提前上传的分片在 Manifest 提交前不可见为正式 Raw。

MCAP Writer 默认保留 Chunk、Message Index、Summary 和 CRC。推荐起始配置为：

```yaml
noChunking: false
noMessageIndex: false
noSummary: false
noChunkCRC: false
chunkSize: 4194304
compression: "Zstd"
compressionLevel: "Fast"
forceCompression: false
```

`forceCompression=false` 避免对无法进一步缩小的 H.264/H.265 Chunk 强制消耗 CPU。是否使用
`compression=None` 或 `Zstd/Fast` 必须以机器人 CPU、丢帧率、写盘吞吐和最终文件大小实测决定，
不能只比较压缩率。

### 3. 机器人本地使用可恢复状态机

每个分片的本地状态为：

```text
RECORDING
  → SEALED
  → HASHED
  → UPLOADING
  → UPLOADED
  → RAW_COMMITTED
  → LOCAL_DELETABLE
```

- `SEALED` 后计算 size、SHA-256 和 CRC64；
- 上传失败保留相同分片 ID 和幂等键，不生成第二份字节；
- 签名 URL 过期时续期，已完成 Part 根据服务端 ETag 继续上传；
- 服务端确认对象完整性和 Package `RAW_COMMITTED` 前，不删除本地分片；
- `RAW_COMMITTED` 后即使后续 QC 为 `RISK/REJECT`，Raw 已安全进入 OSS，本地可以按策略删除；
- 本地磁盘水位不足时，优先暂停新 rollout 或降低采集策略，禁止删除未提交分片。

### 4. Manifest V2 表达分片、视频流和包身份

现有 Manifest V1 的单 `RAW_MCAP` 和单一顶层 SHA 不能完整表达多分片 Raw。新增
`RolloutManifestV2`，至少包含：

```json
{
  "schema_version": 2,
  "project_id": "project-a",
  "region_code": "cn-hangzhou",
  "rollout_id": "rollout-001",
  "robot_id": "robot-01",
  "start_time": "...",
  "end_time": "...",
  "recorder_version": "robot-recorder/2",
  "ros_distro": "jazzy",
  "video_streams": [
    {
      "camera_id": "front",
      "topic": "/camera/front/image_raw/ffmpeg",
      "message_type": "ffmpeg_image_transport_msgs/msg/FFMPEGPacket",
      "codec": "h264",
      "width": 1920,
      "height": 1080,
      "clock_domain": "robot_monotonic"
    }
  ],
  "files": [
    {
      "path": "rollout-001_0.mcap",
      "role": "RAW_MCAP_SEGMENT",
      "size": 12345678,
      "sha256": "...",
      "crc64": "...",
      "start_time_ns": 1000000000,
      "end_time_ns": 301000000000
    },
    {
      "path": "metadata.yaml",
      "role": "ROSBAG_METADATA",
      "size": 1234,
      "sha256": "...",
      "crc64": "..."
    }
  ]
}
```

包身份定义为：

```text
package_fingerprint = sha256(canonical_json({
  schema_version,
  identifiers,
  time_range,
  recorder_contract,
  video_streams,
  files_sorted_by_path
}))
```

其中 `files` 描述符包含 path、role、size、SHA-256、CRC64 和分片时间范围。Manifest 对象本身及
`package_fingerprint` 字段不参与该哈希，避免自引用。后续 Outbox、Temporal、QC、Alignment、
Lance 和 Publishing 都以 package fingerprint 为源 Package 身份，而不是任选一个 MCAP SHA。

### 5. 上传正文绕过 API 并直接进入 OSS

上传流程为：

```text
1. 机器人提交 Manifest Preflight
2. API 校验项目、区域、rollout、Topic、分片清单和容量上限
3. 为每个 sealed object 创建或恢复 Multipart Upload
4. 机器人通过有期限签名 URL 直接 PUT OSS
5. 机器人提交已排序的 part_number/etag
6. 服务端完成 Multipart 并验证 size、CRC64、SHA-256
7. 全部对象完成后条件式创建 rollout_manifest.json
8. PostgreSQL 在同一事务中写 RAW_COMMITTED 和 Outbox Event
```

API 进程不代理 MCAP 正文。Raw 对象不可覆盖，OSS Bucket 应开启版本控制、服务端加密和适用的
对象锁/保留策略，API 身份不具有普通 Raw `DeleteObject` 权限。

推荐对象布局：

```text
raw/v2/
└── project=<project>/
    └── region=<region>/
        └── date=<date>/
            └── robot=<robot>/
                └── rollout=<rollout>/
                    └── package_sha256=<package-fingerprint>/
                        ├── rosbag2/
                        │   ├── metadata.yaml
                        │   ├── rollout-001_0.mcap
                        │   ├── rollout-001_1.mcap
                        │   └── rollout-001_2.mcap
                        └── rollout_manifest.json
```

Manifest 是 Package 的提交标记。只有清单中所有对象均存在且摘要匹配时，Package 才能进入
`RAW_COMMITTED`。

### 6. Outbox 和 Temporal 编排正式处理

Raw 提交事务写入 `rollout.raw_package_committed.v2` Outbox Event。Dispatcher 使用稳定 Workflow
ID 启动 Temporal：

```text
ingest-rollout:v2:<project_id>:<rollout_id>:<package_fingerprint>
```

服务端状态机为：

```text
RAW_COMMITTED
  → MANIFEST_VALIDATING
  → RAW_VERIFYING
  → RAW_VERIFIED
  → QC_RUNNING
      ├── QC_REJECTED  # Raw 保留，不进入正式 Lance
      ├── QC_RISK      # 隔离，等待人工决策
      └── QC_PASSED
            → ALIGNING
            → LANCE_COMMITTING
            → LANCE_COMMITTED
            → ANNOTATION_READY
```

Activity 之间只传不可变 locator 和摘要，例如：

```text
package_fingerprint
manifest_uri + manifest_sha256
frame_index_uri + sha256 + row_count + schema_hash
alignment_fragment_uri + sha256 + row_count + schema_hash
```

禁止把 MCAP、H.264 packet、解码帧或逐帧 JSON 放入 Workflow input、result 或 heartbeat。

### 7. Verification 先验证 Raw 和编码流

Raw Verification 包含：

1. Manifest Schema、标识、时间范围和文件清单；
2. OSS 对象 size、CRC64 和 SHA-256；
3. MCAP magic、footer、summary、chunk CRC、schema、channel、message index；
4. Manifest Topic 与 MCAP 实际 Topic 一致；
5. Camera Topic 的 ROS message type、codec、宽高和像素格式符合合同；
6. 每个分片存在 codec config 和独立可解码起点；
7. ROS `header.stamp`、MCAP log time、packet PTS/DTS 的基本单调性和范围；
8. 分片序号、时间范围和相邻分片边界无未声明重叠或断层。

Raw Verification 失败时：

- 不删除或覆盖 OSS Raw；
- 不进入 QC、Alignment 或 Lance；
- 持久化稳定错误码和不可变验证报告；
- 修复只能通过重新上传正确的新 Package，不得原地修改已提交对象。

### 8. QC 顺序解码一次，不永久保存图片

Worker 按 Camera、Segment 和 PTS 顺序读取编码 packet，并为完整视频流启动一次持续解码会话；
禁止每帧重新启动 FFmpeg。检测内容至少包含：

- packet/帧是否损坏或无法解码；
- ROS 时间戳、PTS、帧间隔、倒序、重复和连续缺失；
- 实际帧率、分辨率和时长；
- 黑帧、过暗/过亮、冻结/重复帧；
- 可选的模糊度、色偏和曝光突变；
- 分片边界是否可以连续恢复展示顺序。

Worker 可以在内存中计算亮度、感知哈希和帧差，但每帧检测完成后立即释放像素。QC 产物为：

```text
reports/qc/
  project=<project>/
  rollout=<rollout>/
  package=<package-fingerprint>/
  profile=<profile-id>-<profile-version>/
  sha256=<report-hash>.json
```

PostgreSQL 只保存 QC 状态、Profile、报告 URI、摘要和审核信息。

决策语义保持现有合同：

- `PASS`：允许进入 Alignment 和正式 Lance；
- `RISK`：隔离并等待人工决策，不直接进入正式 Lance；
- `REJECT`：不进入 Lance，但 Raw 和报告继续保留。

### 9. 解码过程中生成小型 Frame Index

不能假设一个编码 packet 必然对应一个展示帧。Frame Index 必须以解码器实际输出的展示帧为准，
并记录其与 ROS 时间和源 packet 的关系：

```text
video_asset_id
video_segment_id
camera_id
topic
frame_id
frame_index
presentation_ordinal
pts
time_base_num
time_base_den
capture_timestamp_ns
mcap_log_time_ns
nearest_keyframe_pts
packet_sequence_start/end
valid
```

稳定帧 ID 定义为：

```text
frame_id = sha256(
  "ros2-video-frame/v1\0"
  + package_fingerprint + "\0"
  + camera_id + "\0"
  + segment_sha256 + "\0"
  + pts + "\0"
  + pts_occurrence_index
)
```

`pts_occurrence_index` 用于消除异常或合法重复 PTS 的歧义。没有有效 PTS 且无法通过已批准 Clock
Mapping 恢复的帧，在 V1 中标记无效，不使用 `frame_index / fps` 伪造源时间。

Frame Index 写入 Arrow/Parquet 暂存对象：

```text
staging/frame-index/
  project=<project>/
  rollout=<rollout>/
  attempt=<attempt-id>/
  sha256=<content-hash>.arrow
```

成功提交 Lance 后删除 attempt；Worker 崩溃遗留对象按 TTL 回收。

### 10. PostgreSQL 保存媒体目录，不保存视频正文

为 MCAP 编码流建立逻辑资产和分片目录：

```text
media.video_assets
  video_asset_id
  project_id
  region_code
  rollout_id
  camera_id
  source_kind = ROS2_MCAP_ENCODED_STREAM
  topic
  message_type
  codec
  width / height
  package_fingerprint
  status

media.video_segments
  video_segment_id
  video_asset_id
  segment_index
  raw_object_key
  source_sha256
  size_bytes
  start/end_capture_timestamp_ns
  start/end_pts
  frame_count
  status
```

逻辑资产与物理分片分开，以支持一个 Camera rollout 对应多个 MCAP 对象、对象桶迁移和独立校验。
Raw location 迁移必须验证相同 SHA-256，并通过受审计的 location 切换完成。

### 11. Alignment 使用采集时间，不使用写盘到达时间

视频对齐的权威输入是原始 Image 继承到编码消息中的 ROS `header.stamp`，而不是 MCAP Writer
收到消息的时间。MCAP log time 作为传输和录制诊断事实保留。

```text
encoded packet/frame PTS
  → Clock Mapping
capture_timestamp_ns
  → Alignment Profile
Lance step timestamp_ns
```

Clock Mapping 的版本或内容哈希必须随 Frame Index/Alignment Manifest 持久化。30 Hz 对齐继续使用
整数纳秒时间线、最近图像选择、容差、`time_error_ns`、`valid` 和 `repeated` 语义。

### 12. Lance 只保存软索引和对齐事实

每个 Camera modality 使用：

```text
VideoFrameRefV1
├── video_asset_id
├── video_segment_id
├── frame_id
├── frame_index
├── pts
└── nearest_keyframe_pts
```

源采集时间不在 `VideoFrameRefV1` 内重复保存；继续使用 Lance 现有外层
`source_timestamps_ns` 作为 Alignment 权威来源。每行还保留：

```text
rollout_id
step_index
timestamp_ns
source_timestamps_ns
time_error_ns
valid
repeated
sample_valid
```

Lance 中不得保存：

- `sensor_msgs/Image.data`；
- JPEG/PNG 字节；
- H.264/H.265 packet；
- MP4、MCAP 或 HLS 正文。

提交过程保持现有原子边界：

```text
Alignment Arrow staging
  → Schema/Manifest 校验
  → Dataset Writer Lock
  → Lance 事务追加
  → StorageCommitReceipt
  → PostgreSQL 登记逻辑版本
  → 创建 Annotation Task
  → 清理 attempt
```

### 13. Preview 从 Raw MCAP 临时生成播放产物

浏览器不直接消费 Raw MCAP。Preview 根据冻结的 Lance/Annotation 版本解析所需 Frame Ref：

```text
video_asset_id + video_segment_id + PTS
  → PostgreSQL 解析 MCAP Raw locator
  → 从前置关键帧提取编码 packet
  → 无转码 remux，必要时转码
  → 临时 MP4/HLS/JPEG/WebP
```

Preview Cache 默认 24 小时 TTL，缓存键至少包含：

```text
project_id
dataset_id
rollout_id
lance_version
annotation_revision_hash
camera_id
window
encoding_profile
previewer_version
```

Preview 产物不是 Raw，也不能被 Publishing 当作训练数据来源。

### 14. Publishing 导出时物化目标格式

发布和导出必须冻结：

- package fingerprint 和所有源 MCAP segment SHA-256；
- Lance 逻辑版本；
- 已批准 Annotation revision hash；
- included/excluded step ranges；
- Tag Schema；
- 导出 Profile、FFmpeg/编码器和 Exporter 版本。

`export_spec_hash` 用于请求幂等，生成后另存实际 `artifact_sha256`：

```text
export_spec_hash = sha256(canonical_json(frozen_input_and_profile))
```

可导出：

- 选中帧 JPEG/WebP/PNG；
- 应用 EDL 后的 MP4；
- Lance Snapshot；
- LeRobot 或其他训练数据布局。

同一项目内对 `export_spec_hash` 建立唯一约束，通过
`PENDING → MATERIALIZING → READY/FAILED` 防止并发 Worker 重复解码。

## 存储分层与生命周期

```text
OSS
├── raw/                    # 唯一事实源，禁止覆盖
├── reports/qc/             # 小型不可变 QC 报告
├── staging/frame-index/    # 临时 Frame Index
├── staging/alignment/      # 临时 Arrow Fragment
├── derived/lance/          # Lance 数据和逻辑版本
├── cache/preview/          # 临时 HLS/MP4/JPEG
├── derived/exports/        # 可再生导出产物
└── _attempts/              # 失败尝试和未提交中间对象
```

| 存储类别 | 默认策略 |
|---|---|
| Raw MCAP/Manifest | 不可覆盖；近期热存储，随后按访问需求转低频或归档；引用存在时禁止物理删除 |
| QC Report | 长期保留；按内容哈希去重 |
| Lance | 保留活动逻辑版本及其血缘；版本删除走独立决策 |
| Frame Index/Alignment staging | 成功后立即清理，失败 attempt 由 TTL 回收 |
| Preview Cache | 默认 24 小时 TTL |
| Export | 默认 TTL；被发布版本或保留策略引用后长期保留 |
| Multipart/未提交对象 | 短 TTL 清理；不得被读取为正式 Raw |

视频容量使用编码码率估算：

```text
每小时逻辑存储 GB ≈ 视频码率 Mbps × 0.45 × 相机数
```

例如 4 Mbps、4 个相机约为 7.2 GB/小时、172.8 GB/天。若 OSS 有多副本或跨区域复制，物理占用
和费用应在此逻辑容量上按存储策略另行计算。内容哈希只能去重完全相同的字节；真实机器人 rollout
通常内容不同，容量治理应优先依靠视频编码，而不是依赖跨 rollout 去重。

## 监控与告警

### 机器人侧

- 编码输入 FPS、输出 FPS、编码延迟和 dropped frames；
- 实际码率、关键帧间隔和编码错误；
- 当前 MCAP 写盘吞吐和 seal 延迟；
- 本地磁盘剩余量、未提交分片数量和最长积压时间；
- 上传吞吐、重试次数、签名续期和最后成功提交时间；
- ROS header time 与系统/机器人时钟偏差。

建议至少在以下情况告警：

- 编码输出 FPS 连续低于合同；
- 本地磁盘不足以容纳两个最大上传窗口；
- 上传积压超过允许离线时长；
- 时间戳倒退或 Camera Topic 中断；
- 分片无法从首个关键帧独立解码。

### 服务端

- `RAW_COMMITTED → RAW_VERIFIED` 延迟；
- MCAP Verification 失败率和稳定错误码；
- 解码速度相对实时倍数、Worker CPU/GPU 和内存；
- QC `PASS/RISK/REJECT` 分布；
- Frame Index row count 与解码 frame count 差异；
- Alignment valid ratio、重复率和 P95/P99 time error；
- Lance commit 延迟、冲突、reconcile 数量；
- staging、attempt、Preview Cache 和 Export 的未清理字节数。

## 失败恢复与一致性规则

1. 机器人只上传 sealed MCAP，不上传正在写的文件。
2. 相同分片重试使用相同内容哈希和幂等键。
3. Package Manifest 只在所有对象完整后条件式创建。
4. Outbox Event 与 `RAW_COMMITTED` 状态在同一 PostgreSQL 事务中写入。
5. Temporal Activity 必须可重试；结果以内容哈希或稳定业务键幂等。
6. QC 失败不删除 Raw，也不创建伪造的 PASS 结果。
7. Alignment attempt 失败不能进入 Lance 正式版本。
8. Lance 已提交但 PostgreSQL 未登记时，通过 StorageCommitReceipt `reconcile()`，不得再次追加行。
9. Preview/Export 失败不能修改 Raw、Lance 或 Annotation。
10. Raw 硬删除前必须证明没有活动 Lance 版本、Annotation、发布版本或法定保留引用。

## 对现有代码的影响

当前实现需要升级以下边界：

1. `ManifestFileV1.role` 从 `RAW_MCAP/AUXILIARY` 升级为能表达
   `RAW_MCAP_SEGMENT/ROSBAG_METADATA` 的 V2 合同。
2. `RawObjectCommittedV1` 的单 `object_key/source_sha256` 升级为 Package locator 和
   package fingerprint。
3. 当前 `raw/v1/.../recording.mcap` 单对象键升级为 `raw/v2/.../package_sha256=.../rosbag2/*`。
4. Verification 从单 MCAP 扩展为 Package、多个 segment 和视频流边界验证。
5. 当前 Worker 的 JSON/Base64 JPEG Camera 解码器扩展或替换为 FFMPEGPacket/H.264 解码器。
6. `AlignedFragmentManifestV1.source_sha256` 升级为 Package 身份，避免只绑定一个 segment。
7. Lance Schema 增加结构化 `VideoFrameRefV1`，legacy `mcap://...JPEG` 字符串继续只读兼容。
8. Preview 增加从 MCAP 编码 packet 按关键帧窗口 remux/转码的适配器。
9. Publishing 冻结所有 segment SHA，而不是仅冻结 `source_mcap_sha256`。

## 迁移计划

### 阶段 A：机器人端编码试点

- 选择一种代表性机器人硬件和一个 Camera；
- 对相同场景分别录制 Image、JPEG 和 H.264 MCAP；
- 比较文件大小、CPU/GPU、温度、丢帧、时延和解码画质；
- 验证 H.264 MCAP 可由 rosbag2 回放并恢复 Image Topic；
- 冻结 H.264 V1 Profile 和分片时长。

### 阶段 B：Manifest 与多对象上传 V2

- 实现 `RolloutManifestV2`、Package fingerprint 和新角色；
- 支持多个 MCAP segment 与 metadata.yaml 的断点续传；
- Raw Package 原子提交并发布 v2 Outbox Event；
- 保持 V1 单 MCAP 上传兼容。

### 阶段 C：Verification、QC 与 Frame Index

- 增加 FFMPEGPacket Schema 和 codec contract 验证；
- 实现跨 segment 的持续顺序解码；
- 增加 packet/frame/ROS 时间映射和 Frame Index 暂存；
- QC 不留下永久解码帧。

### 阶段 D：Alignment 与 Lance Schema v2

- Alignment 使用 `capture_timestamp_ns`；
- Lance Camera modality 写入 `VideoFrameRefV1`；
- 验证每个有效 step 都可从 Raw MCAP 重放到目标 frame；
- 提交成功后清理 staging attempt。

### 阶段 E：Preview、Publishing 与停止旧录制

- Preview 支持从 MCAP H.264 packet 临时生成 HLS/MP4/JPEG；
- Publishing 实现确定性 Export；
- 新机器人 rollout 停止录制 Image/JPEG Camera 正文；
- legacy JPEG MCAP 继续兼容读取但不再生成。

## 验收标准

1. 新 rollout 的每个 Camera 只存在一种永久编码视频正文。
2. MCAP 中不存在同一 Camera 的 Image、JPEG 和 H.264 重复 Topic。
3. 每个 sealed segment 都能独立通过 MCAP 验证并从关键帧开始解码。
4. 机器人网络中断后可恢复上传，不产生第二份 Raw。
5. OSS Package 只有在全部分片和 Manifest 摘要匹配后进入 `RAW_COMMITTED`。
6. Raw Verification 覆盖所有 segment、Topic、Schema、CRC 和 codec contract。
7. QC 完成损坏、黑帧、重复、时序和帧率检测后不留下永久 JPEG/RGB。
8. `PASS` rollout 进入 Alignment；`RISK/REJECT` Raw 保留但不进入正式 Lance。
9. 每个有效 Lance Camera step 都包含可解析的 asset、segment、frame ID 和真实 PTS。
10. 从 Lance Frame Ref 解码出的画面与顺序解码中相同 PTS/ordinal 的画面一致。
11. Temporal history 不含 MCAP 正文、视频 packet 或逐帧稠密 JSON。
12. 相同 Alignment 输入和 Profile 重试得到相同内容哈希，不重复追加 Lance 行。
13. Preview Cache 到期删除后不影响 Raw、QC、Lance、Annotation 和重新导出。
14. 相同导出规格复用同一 `export_spec_hash`，实际产物另有 `artifact_sha256`。
15. 代表性样本的视频持久化容量相对逐帧 JPEG MCAP 至少降低 80%，且不超过批准的丢帧和画质阈值。

## 待确认项

1. 首批机器人 ROS 2 发行版、CPU/GPU/NPU 和可用硬件编码器。
2. H.264 V1 的 CRF/CQ、目标码率、GOP、B-frame 和像素格式。
3. 默认 MCAP 分片为 60 秒、180 秒还是 300 秒。
4. 普通训练视频是否允许有损编码，以及哪些 Camera/Profile 要求 lossless。
5. `RISK` 是否允许人工批准后进入正式 Lance，或只能重新采集。
6. Raw 从热存储转低频/归档的时间以及恢复时延要求。
7. 多相机硬件同步、PTP/NTP/机器人单调时钟的 Clock Mapping 合同。
8. 是否需要保留标准 rosbag2 `metadata.yaml` 原始字节，还是允许从 Manifest 确定性重建。

在这些参数确认前，不影响核心架构：ROS 2 编码 packet 是唯一视频正文，MCAP 是唯一 Raw 容器，
OSS 保存不可变 Raw，Worker 流式解码检测，Lance 只保存软索引，媒体仅在 Preview/Publishing 阶段
物化。
