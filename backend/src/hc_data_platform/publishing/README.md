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
重新加载 Lance 数据集；`LeRobotV3Exporter` 会创建 v3 的 `meta/`、`data/` 和分块
Parquet 布局，不生成永久 MP4。两者都使用尝试暂存，并在原子提升操作创建下载授权之前进行
原生重新加载验证。暂存阶段失败的字节永远不可下载，重试操作具有幂等性。

第一阶段特意不提供 HDF5、独立 Parquet、VLM 和永久 MP4 导出。

在 `backend/` 目录运行隔离门禁：

```bash
ruff format --check src/hc_data_platform/publishing tests/publishing
ruff check src/hc_data_platform/publishing tests/publishing
mypy src/hc_data_platform/publishing tests/publishing
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/publishing
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -m integration tests/publishing
```
