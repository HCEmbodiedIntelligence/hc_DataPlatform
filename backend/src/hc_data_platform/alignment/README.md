# BE-07 多模态对齐

该引擎会创建精确到整数纳秒的时间线，其中 `start` 包含在范围内，`end` 不包含在范围内。
在 30 Hz 下，60 秒窗口恰好包含 1,800 行；时间戳使用
`start_ns + floor(step_index * 1e9 / frequency_hz)` 计算，因此不会累积浮点周期误差。

默认策略如下：

- 图像和点云：选择最近的样本（距离相同时选择较早的样本）；
- 连续关节值：嵌套数值线性插值；
- 动作：采用符合因果关系的上一条命令；
- 离散状态：采用最近且不晚于当前时刻的状态；
- IMU 和力/力矩：计算对称容差窗口内的平均值。

所有容差均来自 `AlignmentProfileV1`。缺失、不符合因果关系、形状不兼容或超出容差的选择，
其 `value=None` 且 `valid=false`；如果存在候选项，则保留候选源时间戳和实测时间误差。
只有所有必需模态都有效时，`sample_valid` 才为 true。在相邻的对齐行中复用同一个源时，
`repeated=true`，复用图像也包含在内。

`AlignmentEngine.align_to_writer` 通过 `FragmentWriterPort` 每次输出一行。
`ArrowFragmentWriter` 会将确定性的 Arrow IPC 记录批次写入以 SHA-256 命名的
rollout/attempt 目录。提交时关闭 `.part` 文件，并以原子方式将其重命名为
`<content_sha256>.arrow`；中止时只删除当前尝试的部分文件。使用该写入器需要安装后端的
`data` 扩展依赖。引擎绝不会打开或提交共享 Lance 数据集，并且只返回
`AlignedFragmentManifestV1`。

规范行哈希和模式哈希不包含尝试标识及暂存 URI。因此，对相同源、配置和转换器输出进行重试时，
即使尝试路径不同，仍会得到相同的哈希。

在 `backend/` 目录运行模块门禁：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/pytest tests/alignment
.venv/bin/ruff format --check src/hc_data_platform/alignment tests/alignment
.venv/bin/ruff check src/hc_data_platform/alignment tests/alignment
.venv/bin/mypy src/hc_data_platform/alignment tests/alignment
```

如果某次尝试失败，请先检查调用方错误；该尝试不应存在 `.arrow` 文件。遗留的 `.part`
文件表示进程在引擎能够调用 `abort` 前已终止，可使用尝试记录进行协调修复。
