# Raw 派生版本固定与恢复

`feat/chayi-g1` 的连续录制桥接现在先读取同租户、项目、地域的已登记录制，
固定完整 command 和资产对象位置，再选择匹配的派生 receipt。支持读取已知
`openarm-raw-platform/1` 至 `/4`；运行版本仍为 `/4`。未完成数据库登记但已
成功写入存储的旧 receipt 也可恢复，不因升级再次编码。

每次恢复核验原始封存哈希、录制身份、原文件清单、资产大小／SHA／CRC、
完成状态及精确对象位置，并读取验证实际字节。相同内容哈希不授权跨租户对象。
已登记的 receipt 暂时不可见时拒绝重新生成，不改不可变录制身份、不覆盖旧数据。
同时修复派生资产 CRC64 内存类型为整数、JSON 表示为字符串的契约。

## 验证证据

在 `hc-chayi-g1` 的真实 PostgreSQL、MinIO 及 RobotIngestService 执行 multipart
上传、提交和 RecordingOutboxHandler 登记。原始输入是明确标识的合成录制副本；
测试使用当前编码器写 `/3` 标签以验证版本选择，**未运行历史 `/3` 二进制**。

- 先登记 `/3`，再生成 `/4` 孤立派生；三个并发重试保留 `/3` command 和对象位置，
  禁止调用 prepare 后仍成功，所有原字节哈希保持一致。
- 只用故障注入隐藏旧 receipt；拒绝改用 `/4` 或重新编码。未删除存储对象。
- 拒绝损坏的封存哈希、机器人身份、外国租户对象位置和遗漏原文件的 receipt，
  也拒绝外国租户的登记事件。
- 连同 receipt、Raw 准备和容量回归：**12 passed**，59.28 秒。
  一个已有 Starlette/httpx 弃用警告。变更代码 Ruff 通过。
- 恢复隔离 worker 后，实际 outbox 已发布且登记 job 为 SUCCEEDED，录制仍为
  AWAITING_SLICE。**尚未产生独立 Episode 包**，不能把登记当作三入口验收完成。

```bash
bash deploy/chayi/compose.sh run --rm test \
  tests/robot_ingest/test_raw_derivation_services.py \
  tests/robot_ingest/test_raw_recording_receipt.py \
  tests/continuous_recordings/test_openarm_raw.py \
  tests/robot_ingest/test_recording_capacity.py -q --tb=short
```

实际服务用例要求先停**隔离** worker 以免后台登记抢先执行测试故障窗口；结束后
恢复 worker。详见 [JUnit](raw-derivation-services.xml) 和
[录制与版本证据](raw-derivation-recovery.json)。该结果是软件恢复证据，不是旧版本
真实资料迁移、相机或真人验收。17 项 P0 整项状态不变。

G2 前还需真实旧派生清单、升级／中断恢复及回退演练。回退保留新旧 receipt 和
所有原文件；不能用缺少此固定恢复逻辑的旧桥接处理已登记的跨版本录制。
本阶段未执行 G2/G3、未修改 8088 业务数据、未启动机器人或相机。
