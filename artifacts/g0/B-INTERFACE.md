# B → C 处理接口和当前依赖

2026-09-22。C 已提供可执行端口 `robot_ingest/processing_contract.py`；这是 C 侧接口提案，**尚未有 B 已提交版本确认实现，不能写成双方已联调通过**。

最终检查时 B 的 HEAD 仍为 `2f6022202728f7c18852c0afd0fbd8df4244f362`，工作区已有 `lerobot_imports/committed.py` 等未提交开发。C 未复制、修改或运行 B 的未提交文件。

## C 输入与 B 责任

```python
class CommittedLeRobotProcessor(Protocol):
    def discover(self, source: CommittedSource) -> tuple[SourceEpisode, ...]: ...
    def process(self, source: CommittedSource, episode: SourceEpisode,
                *, episode_id: str, attempt_id: str) -> EpisodeReceipt: ...
```

- `source.upload` 是提交的 DB 事实，含权威组织/项目/Region/dataset/task/robot 身份、原 manifest 和全部 COMPLETED 资产。`source.storage` 为服务端对象存储；机器人本地路径从不用于服务器读文件。
- `source.read(path)` / `read_chunks(path)` 只解析已提交资产的对象 key，校验大小与 SHA256；流必须读至 EOF 并校验完成后才能发布。大文件使用 `read_chunks` 写临时文件，避免整段视频驻留内存。
- B `discover` 验证完成包、元数据、source_mapping/capture-context，返回真实 `source_episode_id` 与 `source_episode_index`；C 不依据 declared_episode_count 制造映射。返回顺序可任意，索引/源 ID 不得重复，数量 1..10,000。
- C 分配稳定平台 `episode_id`（组织 + Raw + 源索引的 UUID5），重试不变。B 必须使用它建立业务 Episode/Rollout、QC、数据和媒体。`attempt_id` 在活动重投/Worker 重启时不变，显式处理重试时变化。
- B 必须对同一平台 episode 身份幂等：发布成功但 C 回写前崩溃，下一次 `process` 应返回已持久化 receipt，而非再建业务数据。这个跨模块承诺尚待真实 B 版本验证。
- receipt 包含 PASS/RISK/REJECT、frame_count/sample_count、qc_report_id、dataset_version/lance_version。PASS 必须有两个版本；RISK/REJECT 允许无训练版本，表示质量处置已经完成。C 将三者都视为处理终态，并分别统计，只有 PASS 计入合格。
- 格式/源文件问题抛 `ProcessingFailure("UPPERCASE_CODE", retryable=False)`；可恢复依赖/存储问题可设 true。非预期异常会脱敏成 `ROBOT_PROCESSING_TECHNICAL_FAILURE`，活动最多自动重试 3 次，再保存逐段失败。长处理需调用 Temporal `activity.heartbeat`，间隔小于 60 秒。

## 与 B 当前未提交入口的具体差异

B WIP 已出现 `normalize_committed_manifest(raw, assets)`、`discover_committed_source(storage, raw)`，并要求任务进入 `LeRobotPipeline.prepare` + `IngestRolloutWorkflow`。发现 episode **不是**处理完成，不能直接生成成功 receipt。

接入仍需 B/E 决定并实现以下适配：

1. B 当前 discovery/pipeline 读取 `raw-upload-manifest/v1`；现有 robot commit 保存的是 `robot-ingest-committed/v1`。不得覆写已提交 Raw 的 manifest_key/原对象。需要新增派生规范化 manifest 的持久定位，或由 B 读取入口识别原机器人 envelope 并复用同一规范化函数。C 已提交的对象应继续可恢复。
2. B 当前 pipeline 生成 `lerobot-{raw[:16]}-ep-*`，C 使用完整组织/Raw/索引生成 `rie-*`。B 应接受 C 的稳定 episode_id，或在 E 确认后统一身份算法；不能先制造两套 episode 再对账。
3. `prepare` 返回工作流输入，并非发布 receipt。需要执行真实 ingest 子流程并读取其 QC/版本/媒体持久结果，再适配到 `EpisodeReceipt`。若选 Temporal 子 workflow 的组合方式，可由 E/B/C 在集成分支替换 C `episode` activity 的调用边界。
4. B 负责源 episode、outcome、伴随资产和通用轴/相机解析；C 不复制 OpenArm/G1 reader。C 的 `ProcessorUnavailable` 只读真实 `meta/info.json` 校验 hash，然后明确失败，没有模拟成功路径。

接线前至少使用 valid-openarm、valid-generic 和缺视频/错维度/质量风险样本，证明真实 Raw→B→QC/数据/媒体→C 结果，并用一次“B 已发布但 C 未写 receipt”的故障证明跨模块幂等。当前这部分未执行。
