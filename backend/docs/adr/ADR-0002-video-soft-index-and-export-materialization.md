# ADR-0002：视频单份存储、帧级软索引与导出时物化

- 状态：Proposed / 决策方向已确认，尚未实现
- 日期：2026-08-19
- 责任域：Raw 存储、Ingest Worker、QC、Alignment、Lance、Annotation、Preview、Publishing
- 当前范围：Worker 与后端数据边界；不包含前端交互设计

> ROS 2 原生机器人采集采用本 ADR 允许的“编码包写入 MCAP”分支；机器人录制、分片上传、
> OSS、QC 和 Lance 的具体合同见
> [ADR-0003](./ADR-0003-ros2-h264-mcap-oss-qc-and-lance-pipeline.md)。

## 一句话结论

原始压缩视频在 Raw 对象存储中只保存一份；Lance 只保存能稳定定位源视频帧的软索引；
PostgreSQL 保存 Tag、排除、恢复、审核和导出版本；预览按需解码并使用有期限缓存；只有导出时
才物化 JPEG、WebP、新 MP4 或训练数据集。

```text
Raw MP4/H.264（唯一事实源）
          │
          ├── PostgreSQL：资产目录、Tag、EXCLUDE/RESTORE、导出状态
          │
          └── Lance：video_asset_id + frame_id + PTS + 对齐事实
                                      │
                                      ├── 预览：临时解码/临时 HLS 缓存
                                      └── 导出：按冻结版本物化目标格式
```

## 背景与当前实测

2026-08-19 的 Hugging Face G1 单 Episode 验证链路已经跑通：上传完成事件、Outbox、Temporal、
Manifest 校验、Raw MCAP 验证、QC、30 Hz 时序对齐和 Lance 落库均成功。该验证为了证明视频
逐帧 QC 和血缘可用，在同一 Raw MCAP 中同时保存了：

- 原始 H.264 MP4 Attachment：1,115,689 B；
- 469 张 JSON/JPEG Camera 消息：JPEG 字节合计 11,034,410 B；
- 状态、动作消息及 MCAP 容器信息。

因此 Raw MCAP 为 12,163,699 B。原始 MP4 与 Parquet 合计只有 1,198,306 B；清理 Lance
临时 attempt 后，服务器正式 Raw、Manifest 和 Lance 合计约 12,359,182 B，即原始业务数据的
约 10.31 倍。主要空间放大来自永久 JPEG 帧，不是 Lance。

当前 Lance 的 Camera modality 仍是以下形式的字符串：

```text
mcap://<raw-object>#/camera/egocentric/image?log_time=<ns>&sha256=<message-sha256>
```

它能追溯到 Raw MCAP 中的一条 JPEG 消息，但没有独立的 `video_asset_id`、稳定 `frame_id`、
MP4 PTS 或原始视频定位信息。若去掉 JPEG 而不修改该模型，当前 Lance 引用会失效。

## 目标

1. 同一原始视频的权威字节只在 Raw 对象存储中保存一次。
2. QC、对齐、Tag 和删减不要求永久保存逐帧 JPEG。
3. 每一个 Lance 对齐步骤都能稳定、可验证地定位到源视频帧。
4. Tag、软删除和恢复不修改 Raw 视频，也不重写历史 Lance 版本。
5. 相同输入和相同导出配置只生成一份确定性导出产物。
6. 支持可变帧率视频，不以 `frame_index / fps` 代替真实 PTS。
7. 避免把逐帧数据或媒体正文放入 Temporal history。

## 非目标

- 本决策不允许在 H.264 文件内部原地删除单帧。
- 本决策不把 PostgreSQL 变成媒体对象存储。
- 本决策不把 Lance 变成频繁 CRUD 的标注事务数据库。
- 本决策不要求永久保存预览 HLS、缩略图或中间 JPEG。
- 本决策不追溯重写已经提交的 legacy Raw MCAP；旧数据通过兼容读取保留。

## 决策

### 1. Raw 视频只保存一次

推荐的 Raw Package 使用多对象布局：

```text
raw/v2/project=<project>/.../package_sha256=<package-sha256>/
├── recording.mcap
├── cameras/
│   └── egocentric/source.mp4
└── rollout_manifest.json
```

- `recording.mcap` 保存状态、动作、IMU、力传感器和必要的跨时钟同步事实。
- `source.mp4` 保存采集端产生或导入的原始压缩视频，不再复制到 MCAP Attachment。
- MCAP 不再写入 JPEG、PNG 或其他解码后的永久 Camera 帧。
- Manifest 必须分别记录每个对象的路径、角色、媒体类型、大小、SHA-256 和 CRC64。
- 上传重试由内容哈希和现有幂等键复用同一对象，不产生第二份 Raw。
- Raw 对象不可覆盖。只要有 Lance 版本、Annotation revision 或发布版本引用，就不能物理删除。

现有 Manifest 已允许多个文件和 `AUXILIARY` 角色；实施时应增加明确的 `RAW_VIDEO` 角色，
或者在兼容阶段以 `AUXILIARY + video/mp4` 表示，并确保上传、提交和 Worker 校验覆盖清单内的
每个对象，而不是只校验主 MCAP。

如果某一采集端必须只产生一个 MCAP 文件，可选择把 H.264/H.265 编码包作为 Camera Topic
消息写入 MCAP，但仍然只能保留一种压缩视频表示，不能再同时保存 MP4 Attachment 和 JPEG。
对当前需要浏览、Seek、Tag 和导出的场景，独立 MP4 对象更便于使用 HTTP Range、浏览器播放和
FFmpeg 解码，因此作为默认方案。

### 2. PostgreSQL 保存资产身份和可变业务状态

PostgreSQL 只保存视频目录信息，不保存视频正文。建议新增 `media.video_assets`：

```text
video_asset_id          稳定内部 ID
project_id              项目隔离边界
region_code             数据驻留区域
rollout_id              所属 rollout
camera_id               相机身份
raw_object_key          Raw MP4 对象键
source_sha256           原始 MP4 SHA-256
size_bytes              原始字节数
media_type              video/mp4
codec                    h264/h265/av1/...
width, height            图像尺寸
time_base_num/den        容器时间基
start_pts, end_pts       源视频 PTS 范围
duration_ns              时长
frame_count              解码帧数
status                   ACTIVE/SOFT_DELETED/PURGE_PENDING/PURGED
created_at               登记时间
```

`(project_id, source_sha256, camera_id)` 可以作为同一安全域内的去重候选键。不得通过跨项目查询
内容哈希泄露不同租户是否持有同一视频。

现有 Annotation 模块继续作为 Tag 和非破坏性清洗的权威来源：

- Tag 保存在不可变 `annotation_revisions` 中，并固定 `base_lance_version`。
- 单帧 Tag 使用半开步骤区间 `[step_index, step_index + 1)`。
- 连续片段 Tag 使用 `[start_step, end_step)`。
- `AnnotationTag.subject` 可使用
  `object_type=video_frame`、`object_id=<frame_id>` 绑定精确帧。
- 删除/恢复继续使用现有仅追加 `EXCLUDE` / `RESTORE` 操作。
- 当前 `modality_scope=ALL_MODALITIES` 的语义保持不变：排除的是同步步骤，而不是只删除相机画面，
  以避免图像和机器人状态在训练集中错位。若产品需要 Camera-only 排除，应另行决策并升级合同。

因此不另建第二套互相竞争的 Tag 真相表，也不直接更新历史标注记录。

### 3. Lance 保存帧软索引，不保存媒体字节

每个对齐步骤中的 Camera modality 应从当前 `mcap://...JPEG` 字符串升级为
`VideoFrameRefV1`：

```text
VideoFrameRefV1
├── video_asset_id
├── frame_id
├── frame_index
├── pts
├── time_base_num
├── time_base_den
├── source_timestamp_ns
├── nearest_keyframe_pts
└── source_video_sha256
```

其中：

- `pts + time_base` 是源视频内的权威帧时间；
- `source_timestamp_ns` 是映射后的采集/机器人时钟时间，用于多模态对齐；
- `frame_index` 只用于显示和顺序访问，不能作为唯一身份；
- `nearest_keyframe_pts` 用于高效 Seek，可由 FFmpeg/容器索引重新生成；
- `source_video_sha256` 用于解析后验证和血缘审计；
- `video_asset_id` 通过 PostgreSQL 解析为当前 Raw URI，避免在每一行重复长对象键。

稳定帧 ID 的规范输入为：

```text
frame_id = sha256(
  "video-frame/v1\0"
  + source_video_sha256 + "\0"
  + camera_id + "\0"
  + pts + "\0"
  + time_base_num + "/" + time_base_den
)
```

`frame_id` 不包含对象 URI，也不依赖解码出的 JPEG 字节。对象迁移、更换桶或重复上传不会改变
帧身份。重新编码会产生新的视频 SHA 和新的帧身份，并通过派生血缘映射回源帧。

Lance 继续保存现有对齐事实：

- `rollout_id`、`step_index`、`timestamp_ns`；
- 每个 modality 的 `source_timestamps_ns` 和 `time_error_ns`；
- `valid`、`repeated`、`sample_valid`；
- 状态、动作及其他模态的引用或值。

Lance 的不可变逻辑版本只是索引和训练视图快照；所有版本都引用同一份 Raw 视频，不复制视频。

### 4. QC 和 Alignment 流式解码，不落永久帧

Worker 的目标流程为：

```text
上传提交
  → 校验 MCAP、MP4 和 Manifest 哈希
  → FFprobe 读取 codec/time_base/PTS/keyframe/frame_count
  → FFmpeg 顺序解码执行黑帧、重复帧、损坏帧等 QC
  → 丢弃解码像素
  → 生成小型帧索引暂存对象
  → 按 source_timestamp_ns 与其他模态对齐
  → Lance 原子提交 VideoFrameRefV1
```

约束：

- QC 可以在内存中计算亮度、感知哈希和连续帧差，但不得把 JPEG 当作 QC 副产物永久上传。
- 对完整视频执行 QC 时顺序解码一次，禁止为每一帧重复启动 FFmpeg。
- 视频 PTS、关键帧和对齐结果先写入 Arrow/Parquet 暂存对象；Temporal activity 之间只传
  `uri + sha256 + row_count`，不得传 469 帧或大型逐帧 JSON，避免 Workflow history 膨胀。
- 成功提交 Lance 后清理 `_attempts`；失败 attempt 按 TTL 垃圾回收。

### 5. 预览按需解码，缓存不属于正式数据

预览有两条允许路径：

1. 浏览器直接播放签名 MP4 URL，并以 PTS/步骤时间轴叠加 Tag；
2. 服务端根据 `video_asset_id + pts` 解码所需窗口，生成临时 HLS 或单帧 JPEG/WebP。

现有 Preview 模块的 HLS 缓存语义继续保留：缓存键必须包含项目、数据集、rollout、Lance
版本、Annotation revision、相机、窗口和编码配置；默认 TTL 为 24 小时。缓存过期后可直接删除，
不能被发布器当作训练数据来源。

精确逐帧标注不能只依赖浏览器 `currentTime`。提交 Tag 时必须使用后端解析出的
`frame_id + pts + base_lance_version`，避免浏览器 Seek 到相邻关键帧造成标注漂移。

### 6. 删减采用 Edit Decision List，不修改 Raw

当前 Annotation 的 `EXCLUDE` / `RESTORE` 步骤区间就是 Edit Decision List：

```text
EXCLUDE [90, 150)   # 在 30 Hz 数据中排除约 3～5 秒
RESTORE [120, 130)  # 在后续 revision 恢复其中一段
```

- 浏览和训练读取按生效区间过滤。
- 撤销操作通过追加 `RESTORE` 完成，不删除历史操作。
- H.264 帧存在 GOP 依赖，禁止在原 MP4 内原地删帧。
- 如果需要得到物理裁剪视频，Publishing Worker 在导出时创建派生视频。
- 任意帧精确裁剪可能需要重新编码 GOP 边界；只在关键帧边界裁剪时才允许安全 stream copy。

派生视频必须记录 `derived_video_id`、源视频 ID、冻结的 Lance/Annotation 版本、导出配置、
输出 SHA-256，以及派生时间线到源 `frame_id` 的映射。派生视频不能冒充 Raw。

### 7. 导出时物化，并按内容寻址去重

发布/导出必须先冻结：

- Raw 视频 SHA-256；
- Lance 逻辑版本；
- 已批准 Annotation revision；
- 生效的 included/excluded step ranges；
- Tag Schema 版本；
- 导出格式、尺寸、质量、编码器和转换器版本。

确定性导出键：

```text
export_hash = sha256(canonical_json({
  source_video_sha256,
  lance_version,
  annotation_revision_hash,
  included_step_ranges,
  export_profile,
  exporter_version
}))
```

产物放置在：

```text
derived/exports/project=<project>/sha256=<export_hash>/...
```

相同 `export_hash` 的重试或再次请求必须返回已有产物，不重新解码、不生成第二份快照。可以按
导出合同生成：

- 选中帧的 JPEG/WebP/PNG；
- 应用删减规则后的 MP4；
- Lance Snapshot；
- LeRobot 或其他训练布局。

导出产物是可再生数据。没有发布版本或下载保留策略引用时，可按 TTL 删除；Raw、Lance 和批准的
Annotation revision 足以重新生成。

## 数据读取示例

### 标注某一帧

```text
1. 读取 Lance step 123
2. 得到 VideoFrameRefV1(video_asset_id, frame_id, pts)
3. PostgreSQL 将 video_asset_id 解析为 Raw MP4
4. Preview 按 pts 解码或让浏览器播放到对应位置
5. Annotation revision 写入 Tag：
   range=[123,124), subject=(video_frame, frame_id)
```

整个过程不产生永久 JPEG。

### 排除一段数据

```text
1. 用户选择 step [90,150)
2. PostgreSQL 追加 EXCLUDE revision
3. Preview 的 EDITED 模式跳过该区间
4. Publishing 固定该 revision 并计算 included_step_ranges
5. 只有请求 MP4/JPEG 导出时才解码和物化
```

### Raw 视频迁移对象桶

```text
1. 复制并验证相同 source_sha256
2. 在受审计事务中更新 video_asset_id 的物理 locator
3. Lance 和 Annotation 均不变，因为它们引用稳定 ID/frame_id
```

## 为什么不选择其他方案

### 永久保存每帧 JPEG

优点是读取简单，但本次实测导致服务器存储约为原始数据的 10.31 倍，并产生大量小对象或大型
MCAP 消息。该方案不作为正式 Raw 格式。

### PostgreSQL 只索引整个 MP4

单条 MP4 记录无法稳定表达帧级 Tag、可变帧率 PTS、关键帧 Seek 和删减区间。必须同时具备
Lance 稠密帧索引和 PostgreSQL Annotation revision。

### 把 MP4 或 JPEG 二进制写入 Lance

会放大不可变数据集版本、降低媒体 Range/Seek 能力，并让标注 CRUD 与分析存储耦合。Lance
只保存软索引和对齐事实。

### 修改或覆盖原始 MP4

会破坏 SHA-256、帧身份、Tag 血缘、可复现性和审核记录。所有裁剪输出都必须是派生版本。

### 同时保存 MP4 Attachment 和 JPEG Topic

虽然单 MCAP 便于搬运，但它保存了同一视觉内容的两种永久表示，是本次空间放大的直接原因，
不进入目标架构。

## 一致性与删除规则

1. `video_asset_id` 存在且状态可读时，Lance `VideoFrameRefV1` 才能解析。
2. Raw 物理删除前必须证明没有活动 Lance 版本、标注任务、批准 revision 或发布版本引用。
3. 普通“删除帧/片段”只能产生 `EXCLUDE`，不能触发 Raw `DeleteObject`。
4. 项目级硬删除走独立的 `PURGE_PENDING → PURGED` 工作流、保留期和审计记录。
5. 若 Raw 已物理删除，Tag 文本可以保留审计，但画面无法恢复；系统不得把这种状态显示为可预览。
6. 导出和预览只能读取固定的 Lance/Annotation 版本，不能读取可变草稿与“latest”组合。

## 迁移方案

### 阶段 A：增加可解析的视频资产

- 新增 `media.video_assets` 迁移、Repository 和服务端口。
- 扩展 Manifest 的 Raw Video 角色和多对象提交验证。
- 保持旧 JPEG MCAP 读取器可用。

### 阶段 B：Worker 生成软索引

- 新增 MP4/PTS/关键帧探测和流式 QC。
- 新增小型 Frame Index 暂存对象。
- Temporal 只传暂存 locator 和摘要。

### 阶段 C：Lance Schema v2

- 增加 `VideoFrameRefV1` 逻辑类型。
- 新 rollout 写 v2；旧 rollout 仍解析 `mcap://...JPEG`。
- 验证每个有效 Camera step 都能按 ID、PTS 解码到源帧。

### 阶段 D：Preview 与 Publishing 适配

- Preview 从视频引用读取，保留现有 HLS TTL 缓存边界。
- Publishing 按批准 revision 顺序解码，并实现确定性 `export_hash`。
- Tag 继续复用现有 PostgreSQL revision 和步骤区间语义。

### 阶段 E：停止生成永久 JPEG

- 新导入不再写 JSON/JPEG Camera Topic。
- 不再把相同 MP4 作为 MCAP Attachment 复制。
- 对新链路执行容量、Seek、逐帧 Tag、删减、恢复和导出验收。
- 清理成功提交后的 Lance `_attempts` 和过期 Preview/Export 缓存。

历史 Raw 保持不可变；除非另有批准的离线迁移和完整校验，不回写旧 MCAP。

## 验收标准

1. 新 Raw Package 中同一相机只有一份压缩视频字节。
2. Raw MCAP 中不存在永久 JPEG/PNG Camera 消息，也不存在相同视频的 Attachment 副本。
3. 每个有效 Lance Camera step 都包含可解析的 `video_asset_id`、`frame_id` 和真实 PTS。
4. 对可变帧率样本，按 PTS 解码的帧与采集源逐帧匹配。
5. QC 完成黑帧、重复帧、损坏帧和时序检查后，不留下永久解码帧。
6. 单帧 Tag `[n,n+1)` 和片段 Tag 在同一 `base_lance_version` 下可重放。
7. `EXCLUDE`、`RESTORE` 不修改 Raw，导出结果严格遵循生效区间。
8. 相同冻结输入和导出配置得到相同 `export_hash` 并复用同一产物。
9. Preview 缓存到期可删除，删除后不影响 Raw、Tag、Lance 和重新导出。
10. Worker Workflow history 不含媒体正文或稠密逐帧 JSON。
11. 成功 Lance 提交后的 `_attempts` 被及时清理或由 TTL 回收。
12. 以本次 469 帧样本复测时，服务器正式持久化空间应不超过原始 MP4、Parquet及必要索引的
    2 倍；目标估算为约 1.3～1.6 MB，最终数值以实现后的对象清单为准。

## 待确认项

1. Raw Video Manifest 是新增明确的 `RAW_VIDEO` role，还是先复用 `AUXILIARY + video/mp4`。
2. 默认只支持 H.264/MP4，还是同时接受 H.265、AV1 和可变帧率源。
3. 是否需要 Camera-only 排除；当前合同只允许同步步骤级 `ALL_MODALITIES` 排除。
4. 导出产物默认保留期以及哪些发布状态会转为长期保留。
5. 项目内相同 SHA 视频是否允许跨 rollout 物理去重，以及对应引用计数和删除事务。
6. 对要求法务级像素复现的场景，是否只为已批准的关键帧保存少量无损快照；普通训练标注不保存。

在这些项确认前，不影响核心原则：Raw 视频只保存一次、Lance 只做软索引、PostgreSQL 保存
可变业务状态、媒体只在预览缓存或导出阶段物化。
