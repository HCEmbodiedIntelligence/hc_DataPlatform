MCAP → LeRobot 与官方读取、训练兼容性验证（2026-09-21）
====================================================

本次确认平台支持的 MCAP 样本可以经过真实解码、对齐、视频生成、Lance 入库，
导出 LeRobot v3，并交给当前最新正式版 LeRobot 0.6.1 读取和执行 ACT 训练步骤。
版本依据：[PyPI 正式发布记录](https://pypi.org/project/lerobot/0.6.1/)。
机器可读结果见 [兼容性记录](2026-09-21-lerobot-compatibility.json)。

| 数据 | 帧数 | 相机数 | 动作/状态维数 | 官方读取及训练 |
| --- | ---: | ---: | ---: | --- |
| 仓库 `legal.mcap` | 30 | 1 | 2 / 2 | 通过 |
| 仓库 `multi_camera.mcap` | 30 | 2 | 2 / 2 | 通过 |
| 用户 `dataset.lerobot-v3.fixed.zip` | 920 | 4 | 29 / 29 | 通过 |

MCAP 测试读取 `backend/tests/system/wave2/data/packages/` 中的完整 MCAP 文件。
图像为文件内的 JSON/JPEG 消息，动作及关节为 CDR/ros2msg 数组消息。使用平台实际
MCAP 校验器和解码器、流式对齐器、Arrow 暂存读取器、FFmpeg 编码器、原生 Lance
存储适配器和正式导出器。数据库目录及资产索引使用隔离的内存适配器，未向用户当前
数据集插入测试任务。逐帧比较动作、关节数值，并对解码后图像做带有损压缩容差的颜色比较。
开发环境 FFmpeg 及运行中 Worker 的 FFmpeg 5.1 均执行了 MCAP 测试。
相关导出、媒体、故障恢复和连续录像链路回归共 74 项通过；8 项独立数据库/MinIO
集成测试因未配置对应测试环境跳过。变更文件的 Ruff 和 Mypy 检查通过。

画面对比发现并修复了一个编码问题：原链路只用 `setparams=range=limited`
修改范围标记，在部分 FFmpeg 版本中会使 JPEG 转出视频明显偏暗。现在通过
`scale:out_range=tv` 实际转换范围，并更新媒体生成版本，避免复用旧编码暂存。
例如样本首帧灰度从原来的 48 → 37 改为 48 → 46，剩余差异属于 JPEG/H.264 有损编码。
测试要求逐帧通道均值最大差异不超过 8/255，整体平均绝对差异小于 3/255。

官方 LeRobot 验证使用 Python 3.12、LeRobot 0.6.1、PyTorch 2.11.0 CPU。
PyAV 和默认 TorchCodec 均已读取成功；最终记录使用 TorchCodec。
对三份数据分别执行：

- 标准视频、动作和状态特征解析，以及 `DataLoader` 批量加载。
- 4 帧动作序列采样；末尾越界正确生成 `[false, true, true, true]` 填充掩码。
- 官方 ACT 预处理与导出统计量归一化。
- 小型 ACT 配置下的 3 次前向计算、反向传播、AdamW 更新，检查损失和梯度有限且参数改变。
- 动作预测及官方后处理，检查输出维数和数值有效性。

这验证了数据格式和训练接口兼容性。三个训练步骤不代表模型已收敛，也不评估任务成功率。
MCAP 结论限于上述已适配的消息结构；未指定的实际采集文件仍需检查其 topic、schema、
图像编码与动作/状态含义。LeRobot 版本以正式发布版为准，未验证未发布的 GitHub main。

复测命令（在 `backend/` 下生成 MCAP 导出样本）：

```bash
HC_MCAP_EXPORT_CHECK_OUTPUT=/tmp/hc-mcap-export-check \
  .venv/bin/pytest tests/publishing/test_mcap_lerobot_export.py -q
```

用单独安装了 `lerobot[dataset,training]==0.6.1` 的 Python 3.12 环境复测官方兼容性：

```bash
HF_HUB_OFFLINE=1 python backend/tests/publishing/check_lerobot_compatibility.py \
  /tmp/hc-mcap-export-check/mcap-legal.lerobot-v3.zip \
  /tmp/hc-mcap-export-check/mcap-multi_camera.lerobot-v3.zip \
  --video-backend torchcodec --report compatibility.json
```

读取下载包时先解压，将包含 `meta/`、`data/` 和 `videos/` 的目录传入
`LeRobotDataset(repo_id="local/my-dataset", root=解压目录)`。
标签的名称、属性和区间保存在 `meta/annotations.json`，逐帧关联信息在 `hc.tag_ids`
及 `hc.tag_labels` 中；ACT 的常规动作预测损失不会自动把这些扩展标签用作额外监督。
