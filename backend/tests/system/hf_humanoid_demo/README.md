# Hugging Face Unitree G1 多模态数据 → Worker → Lance 实跑说明

## 本次实跑结论

最终三模态 run 是 `hf-g1-video-20260819-02`：

- project: `be22-hf-g1-video-20260819-02-p1`
- region: `be22-hf-g1-video-20260819-02-cn`
- rollout: `hf-g1-video-20260819-02-hf-g1-episode-0`
- Temporal run: `53b0fbe4-f917-417b-b656-a7e4710730d5`

实际状态：Upload/Manifest commit `COMMITTED`，Outbox 发布 1 次，Temporal
`Completed`，Raw 验证 `RAW_VERIFIED`，QC `PASS`，对齐 `READY`，Lance 逻辑版本 1、
物理版本 1，共 469 行且 469 行全部 `sample_valid=true`。

数据来自
[`cloudwalk-research/psi0-g1-sneaker-94ep-v1`](https://huggingface.co/datasets/cloudwalk-research/psi0-g1-sneaker-94ep-v1)，
固定 revision `54d28d1c3f77aaa23d9c20b0388a110b664222d1`，许可证 Apache-2.0。
本次取完整 episode 0：469 帧、30 Hz、32 维 state、36 维 action，以及一条
640×480 H.264 egocentric MP4 视频流。

源 MP4 被逐帧解码为 469 张 JPEG，并和 state/action 一起写入一个 Raw MCAP：

```text
/humanoid/observation/state   469 messages
/humanoid/action              469 messages
/camera/egocentric/image      469 messages
总计                         1407 messages
```

worker 会实际解码 JSON/JPEG camera envelope，验证 JPEG 哈希、尺寸和可解码性，计算
亮度与帧指纹交给图像 QC；独立检查还重新从源 MP4 解码了全部 469 帧，逐帧与 MinIO
Raw MCAP 内的 JPEG 字节比对，全部一致。源 H.264 MP4 的精确字节也作为 MCAP
attachment 保留，可从 Raw 无损取回。

## 数据在哪里

宿主机原始 HF 文件：

```text
artifacts/hf-humanoid-lance/hf-g1-video-20260819-02/source/episode_000000.parquet
artifacts/hf-humanoid-lance/hf-g1-video-20260819-02/source/episode_000000.mp4
artifacts/hf-humanoid-lance/hf-g1-video-20260819-02/source/info.json
artifacts/hf-humanoid-lance/hf-g1-video-20260819-02/source/HUGGING_FACE_README.md
```

宿主机转换结果：

```text
artifacts/hf-humanoid-lance/hf-g1-video-20260819-02/bundle/unitree-g1-episode-000000.mcap
artifacts/hf-humanoid-lance/hf-g1-video-20260819-02/bundle/rollout_manifest.json
```

源 MP4 是 1,115,689 bytes，SHA-256：
`286eb47d292ef8311ae3c6a14413da233dd187ab5d6ab01e2759ea81d0e29d3c`。

三模态 Raw MCAP（包含原始 MP4 attachment）是 12,163,699 bytes，SHA-256：
`e29ad7d162a43be85bab1db43b983a13333d10617266d11acc3240c7f5bee291`。

MinIO bucket 是 `hc-data-local`。Raw MCAP key：

```text
raw/v1/project=be22-hf-g1-video-20260819-02-p1/date=2026-08-19/robot=unitree-g1/job=hf-g1-video-20260819-02-hf-g1-job/package=hf-g1-video-20260819-02-hf-g1-package/rollout=000001-hf-g1-video-20260819-02-hf-g1-episode-0/sha256=e29ad7d162a43be85bab1db43b983a13333d10617266d11acc3240c7f5bee291/recording.mcap
```

Lance 数据集 URI：

```text
s3://hc-data-local/lance/be22-hf-g1-video-20260819-02-p1/hf-g1-video-20260819-02-dataset/hf-g1-video-20260819-02-schema-v1/30hz/aligned_steps.lance
```

Docker volume 是 `hc-data-platform-dev_minio-data`，MinIO 容器内根路径是
`/data/hc-data-local`。worker 容器内的中间 Arrow fragment 位于
`/tmp/hc-data/alignment/...`，最终持久结果是 MinIO 上的 Lance URI。

## 脚本和证据

- `convert.py`：下载 pinned Parquet/MP4，验证 SHA、ffprobe 视频参数、469 帧及
  state/action 维度，把三个 topic 写进 MCAP，并生成含 camera 声明的 Manifest。
- `run.py`：创建隔离 scope、质量配置和数据集绑定，经真实上传接口提交，等待
  Outbox/Temporal/Worker 完成。
- `inspect.py`：查询 PostgreSQL、回读 MinIO Raw、打开 Lance，并对源 MP4 与 MCAP
  469 个视频帧逐帧比对。
- `deploy/compose/compose.worker-demo.yaml`：只订阅一个精确 demo scope，关闭测试夹具 decoder。
- `artifacts/hf-humanoid-lance/hf-g1-video-20260819-02/run-evidence.json`：主链路结果。
- `artifacts/hf-humanoid-lance/hf-g1-video-20260819-02/inspection-evidence.json`：完整交叉检查结果。

最终回读检查可重跑：

```bash
PYTHONPATH=backend/src:backend backend/.venv/bin/python \
  -m tests.system.hf_humanoid_demo.inspect \
  --run-id hf-g1-video-20260819-02 \
  --output-dir artifacts/hf-humanoid-lance/hf-g1-video-20260819-02
```

## 本次检查发现

1. HF float32 timestamp 有数百纳秒的网格偏差。MCAP log time 现按
   `frame_index / 30 Hz` 生成精确时基，原始 `source_timestamp_s` 保留在消息内，
   state/action/camera 最终为 469/469 同步有效。
2. 图像数据已完整进入 Raw MCAP，worker 图像 QC 也实际解码了 JPEG；但 Lance 的三个
   modality 字段仍是 `mcap://...&sha256=...` 引用，不是直接嵌入 state/action/JPEG 字节。
3. QC 现在检查三路 timing，并对图像检查损坏、黑帧和相邻重复帧；action/state JSON
   数组仍未进入数值语义 QC。
4. 三模态 469 帧 Temporal payload 峰值约 956 KB，超过 SDK 512 KB warning limit。
   更长 episode 应改用对象存储引用或 activity 内分页读取，避免碰到硬限制。
5. Helm worker 仍未暴露 `HC_OUTBOX_SCOPES`；本次通过显式单 scope overlay 验证，
   production values 还不能视为开箱即消费上传事件。

本次修改了 worker 的 JSON/JPEG camera QC 投影，不涉及前端或 API 业务实现；
PostgreSQL、MinIO、Raw MCAP 和 Lance 数据都保留用于继续检查。
