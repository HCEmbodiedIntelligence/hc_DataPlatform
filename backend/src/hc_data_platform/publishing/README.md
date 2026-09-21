# 发布模块（BE-11）

`DatasetPublisher` 会冻结一个 BE-08 目录版本和当时准确的 BE-09 批准记录，形成仅追加的
数据集版本。默认预检只纳入状态为 `PASS` + `DERIVED_READY` + `APPROVED` 的 rollout。
`RISK`、`REJECT`、尚未完成的派生、过期的批准记录和被完全排除的 rollout 仍会显示在
预检报告中，但绝不会进入训练数据。

每个纳入的 rollout 都会冻结：

- 基础 Lance 版本和源 MCAP SHA-256；
- 标注任务/修订，以及规范化的半开排除区间；
- 质量配置、对齐配置/频率和转换器版本；
- 导出器实际使用的精确互补区间 `included_step_ranges`。

发布过程会创建确定性的逻辑 `annotations.lance` 和 `training-manifest.json` 资产。
对于相同的冻结输入，它们的规范字节和 SHA-256 值保持稳定。`created_at` 不参与发布内容哈希。
若用变更后的内容复用已发布版本，系统会返回 `DATASET_VERSION_IMMUTABLE`；调用方必须选择
新的版本标识符。

`adapters.py` 使用公开的 BE-08 `LanceCatalogPort`/`StepReaderPort`，以及 BE-09
已批准修订/生效排除契约。它绝不会读取 Lance 的物理行地址或可变草稿。

无额外依赖的内存导出器是确定性的契约替身。`LanceSnapshotExporter` 会创建并以原生方式
重新加载 Lance 数据集；`LeRobotV3Exporter` 会创建可独立读取的 v3 `meta/`、`data/`、
`videos/` 布局。导出时按冻结媒体引用的原始 PTS 提取已选帧，生成各 Episode 的 MP4；
排除片段后视频、动作、时间戳和标签区间同步重排。源媒体校验大小和 SHA-256，解码与
编码按帧进行。标签名称、属性、结构和批准修订保存在 `meta/annotations.json`，逐帧
`hc.tag_ids` / `hc.tag_labels` 可直接关联标签。动作和状态使用标准 `action`、
`observation.state` 浮点特征，视频使用 `observation.images.*`；重新计算全局及
Episode 统计量，保留原始任务描述和源 Step 映射。

两者都使用尝试暂存，并在原子提升操作创建下载授权之前重新加载验证；LeRobot 还会
完整解码视频，核对帧数、频率、区间、标签和统计量。暂存阶段失败的字节永远不可下载，
重试操作具有幂等性。修复版使用 `lerobot-materialized-v2` 路径隔离旧的引用型产物，
不会复用缺少视频、标签或统计量的旧 ZIP。

目前不提供 HDF5、独立 Parquet 或 VLM 导出；MP4 仅在用户请求导出时生成。

导出页按处理阶段区分三种数据：原始数据通过原始文件授权接口下载上传文件；
标注完成数据按发布清单或当前批准记录筛选；数据集数据按处理入库的快照筛选，
不要求标注审核。`export-eligibility` 返回当前版本中可选的 Episode ID，
不会写入发布记录，页面计数与全选均使用这份结果。

创建导出时 `data_stage` 默认为 `annotated`，保持审核要求。
显式选择 `dataset` 时冻结完整已处理 Step，`annotation_revision` 为 `null`，
不生成批准记录或修改业务发布版本；重试沿用首次任务保存的快照和阶段。

在 `backend/` 目录运行隔离门禁：

```bash
ruff format --check src/hc_data_platform/publishing tests/publishing
ruff check src/hc_data_platform/publishing tests/publishing
mypy --explicit-package-bases src/hc_data_platform/publishing tests/publishing
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/publishing
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -m integration tests/publishing
```
