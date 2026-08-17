# BE-05 MCAP 校验

`McapVerifier` 会对不可变的原始 MCAP 执行一次仅向前扫描。它会校验两处魔数、Header 位置、
记录帧结构、DataEnd 与 Footer 闭合、数据/Summary CRC、Summary 分组及偏移、Statistics、
Chunk/Message/Attachment/Metadata 索引、Chunk 大小/CRC，以及每个 Message 引用。
它不计算传输 CRC64/SHA、不评估传感器质量、不对齐样本，也不会写入输入流。

报告包含确定性的 Schema、Channel 和 Topic 清单。Topic 信息包括消息数量、日志时间范围和
解码器探测结果。每个已声明 Topic 都必须包含一条消息，且 `DecoderProbe` 必须能成功解码
一个样本。缺少必需 Topic 或解码失败会导致原始对象被拒绝。除此之外有效但未知的可选 Topic
只会产生警告级的 `MCAP_UNKNOWN_OPTIONAL_TOPIC`。

## 流式读取与生产适配器

`ReadableObjectStorage.open_reader` 有意只要求实现 `read(size)` 和 `close()`。
因此它既适用于 Python 3.10 文件对象，也适用于云端流式响应体，不依赖 `BinaryIO` 的上下文
管理器行为。`ChunkedObjectStorageReader` 会适配平台生产风格的 `read_chunks` 契约，
同时强制使用请求的存储分块大小。`RegisteredDecoderProbe` 会适配以
`(message_encoding, schema_encoding)` 为键的应用解码器；未配置解码器会被视为校验错误。

有限解析器仍负责字节偏移和完整性规则。测试覆盖由 MCAP 官方 Python Writer 生成的黄金文件。
现有 `data` 扩展依赖中的 `mcap` 官方 SDK 只由 `McapSdkChunkDecompressor` 使用，
用于已注册的 `zstd` 和 `lz4` Chunk 编解码器。未知编解码器会以
`MCAP_UNSUPPORTED_COMPRESSION` 失败，绝不会被跳过并当作有效数据。

内存使用受三个显式配置项限制：

- `read_block_size` 限制每次从对象流读取的大小（默认 1 MiB）；
- `max_record_size` 会在分配无界负载前拒绝过大的外层或嵌套记录（默认 128 MiB）；
- `max_chunk_size` 会在解压前拒绝声明大小过大的解压后 Chunk（默认 256 MiB）。

校验器只保留清单、索引元数据和每个 Topic 的一个消息样本。即使为了统计失败大小，
也不会加载完整对象。生产环境还应将上游 `read_chunks` 大小配置为不超过 Worker 的内存预算。

## 稳定契约

`RawVerificationReportV1` 和 `RawVerifiedV1` 使用 `str, Enum` 而不是 `StrEnum`，
以保持 Python 3.10 兼容性。清单和发现项会在序列化前排序。`content_sha256` 是规范 JSON
（不含该摘要字段自身）的 SHA-256，因此相同输入和调用元数据会生成相同的报告哈希。
Pydantic 模型不可变，SQL 迁移会拒绝更新或删除已存储的报告。

安装 `data` 和 `dev` 扩展依赖后，在 `backend/` 目录运行 BE-05 门禁：

```bash
uv run --extra data --extra dev ruff format --check src/hc_data_platform/verification tests/verification
uv run --extra data --extra dev ruff check src/hc_data_platform/verification tests/verification
uv run --extra data --extra dev mypy src/hc_data_platform/verification tests/verification
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --extra data --extra dev pytest tests/verification
```
