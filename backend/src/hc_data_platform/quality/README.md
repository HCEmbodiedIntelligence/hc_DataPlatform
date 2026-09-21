# BE-06 质量引擎

该包用于评估解码后的 rollout 观测数据。它不解析 MCAP、不执行数据插值，也不写入 Lance。
调用方通过 `QualityInputV1` 提供时间戳序列，以及轻量的图像、关节、动作、点云、模态偏移
和完整步骤观测数据。

`QualityProfileV1` 不可变，并通过 `(profile_id, profile_version)` 进行版本管理。
时间规则的默认值可以按主题覆盖；图像和点云规则也可以按主题覆盖。频率、间隔时长、连续丢失、
覆盖率、检测器容差以及所有风险/拒绝阈值都会序列化到配置中。最初的扁平配置参数仍作为仅输入的
兼容路径保留，并会规范化为嵌套的 v1 契约。

当前默认引擎版本为 `be06-qc/2`，只将以下六类判定为风险：图像损坏、采样频率不足、
动作数据异常（缺失或维度不一致，以及显式配置的动作变化幅度检查）、连续缺帧、时间戳重复或
倒退、必需数据通道缺失。所有命中均为 `warning` / `RISK`；未命中为 `PASS`。
黑帧、重复画面、时间间隔、覆盖率、关节限位、点云、模态时间偏移和完整步比例不参与当前判定。
新增风险必须显式修订 `policy.py` 中的清单，不能因新增检测代码自动启用。

历史 `be06-qc/1` 保留原有判定以复现不可变报告。新处理使用新版本配置；重判旧数据时追加新报告，
保留旧报告内容与哈希。历史引擎使用三条结论规则：

- 出现任何错误级发现时，结果为 `REJECT`；
- 出现一个或多个警告级发现且没有错误时，结果为 `RISK`；
- 没有任何发现时，结果为 `PASS`。

对已有项目执行规则升级时，先预览，再追加 `--apply` 应用。命令只读取持久化的检测证据，
创建新版本配置与报告；不会修改原始数据、旧报告、入库回执或工作流的历史结果。
曾被风险阻断的原生上传可随后通过原有“重试处理”入口继续处理。

```bash
python -m hc_data_platform.quality.reclassify \
  --organization-id ORG --project-id PROJECT --region-code REGION
```

多个警告绝不会累积升级为错误。每项发现都包含稳定的规则代码、实际应用的阈值、观测值、主题
和受影响的纳秒范围。主题指标包含实际频率、唯一/预期帧数、重复及倒序时间戳、最近秩
P50/P95/P99 间隔、最大间隔、最大连续丢失和覆盖率。

持久化时需要同时注入 `ReportSink` 和 `MetadataSink`。系统先按内容哈希写入完整的规范报告，
再更新可变的 rollout 摘要。任一端口失败都会抛出 `QualityPersistenceError`；系统绝不会
返回或发送虚假的 PASS/RISK/REJECT 完成事件。元数据写入失败后可以安全重试，因为对于相同内容，
`ReportSink.put_immutable` 具有幂等性。工作流只能在 `evaluate` 成功返回后调用
`QualityCompletedV1.from_report`。

在 `backend/` 目录运行模块门禁：

```bash
uv run ruff format --check src/hc_data_platform/quality tests/quality
uv run ruff check src/hc_data_platform/quality tests/quality
uv run mypy src/hc_data_platform/quality tests/quality
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/quality
```

如果 `pytest` 尝试导入宿主机上的 ROS 插件，请使用上面所示的
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`；它只会禁用无关的全局 pytest 入口点。
`QualityPersistenceError(stage="report")` 表示不可变对象存储写入失败，并保证未调用
元数据接收端。`stage="metadata"` 表示报告已经存在，只需重试摘要更新。
