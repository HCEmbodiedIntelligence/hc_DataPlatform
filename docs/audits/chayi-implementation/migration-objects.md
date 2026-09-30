# W7 原件/媒体物理清单展开

2026-09-30，隔离 `hc-chayi-g1`。新增 `episode_migration.py verify-objects`，只读
核对 MCAP commit manifest、原生上传 manifest、机器人封存 manifest、连续录制
资产、已有对齐媒体、已提交 Episode 全部文件及集合 manifest。源 manifest
校验归属、文件清单哈希/数量/总大小后展开；bootstrap manifest 记录实际哈希，
不错误地把 Raw 文件集哈希当成 manifest 文件哈希。

显式输入数据库和对象存储端点；CLI 要求数据库当前清单与输入快照完全一致，
变化时先重新清点。对象逐块读入哈希，同一 key 的多次引用去重且必须同大小/
哈希；限制对象数、总声明字节和 manifest 大小。缺失、大小/哈希变化、同 key
冲突、无法展开的 manifest、READY 媒体缺主对象引用均不能报告成功。结果不可
覆盖保存；存在问题时保留结果并退出 2。不会创建假 READY、修改对象或可见指针。

本轮也修正 inventory 嵌套 `asset_document` 中残留的 multipart upload handle：
行哈希仍覆盖完整原始行，审阅文档省略该操作句柄。旧脱敏摘要与源证据保持原样，
新录制/机器人清单哈希相应变化，不能覆盖旧清单文件。

已执行：

```bash
bash deploy/chayi/compose.sh run --rm --no-deps test -q \
  tests/episode_store/test_migration_objects_services.py \
  tests/episode_store/test_migration_inventory_services.py \
  --fail-on-skipped --fail-on-xfailed --tb=short \
  --junitxml=/app/.g1-artifacts/migration-objects-tests.xml
```

[18 项测试](migration-objects-tests.xml)通过，无 skip/xfail。14 项物理核验测试使用
实际 MinIO，包括同大小内容篡改、大小变化、未创建对象、错误归属、错误文件集
哈希、三种资源上限、三维 scope、无主对象的 READY 媒体；4 项既有清点服务
回归使用实际 PostgreSQL/MinIO。保留一个已有 Starlette/httpx 弃用提示。

[实际 CLI 结果](migration-object-cli-results.json)覆盖已有四份隔离服务输出；
命令通过 `.g1-artifacts/migration-object-cli.py` 驱动，无业务写入。

| 已有隔离输入 | 去重对象 | 字节 | 展开的 Raw 原件 |
| --- | ---: | ---: | ---: |
| MCAP | 27 | 439374 | 1 |
| 原生 LeRobot | 33 | 170406 | 12 |
| 连续录制 | 71 | 1318995 | 15 |
| 独立 uploader | 36 | 203436 | 15 |

共 167 个对象、2132211 字节；四个命令均退出 0、无物理对象问题。完整对象清单
位于结果 JSON 的 artifact 路径，含源路径/哈希和重复引用；仓库只提交摘要。
这些均为原有合成输入的真实服务输出，不能算真实历史数据迁移。

操作接口（连接参数由受限环境提供，不写入报告）：

```bash
# 必填环境：HC_EPISODE_MIGRATION_DSN、HC_EPISODE_MIGRATION_OBJECT_ENDPOINT、
# HC_EPISODE_MIGRATION_OBJECT_BUCKET、HC_EPISODE_MIGRATION_OBJECT_ACCESS_KEY、
# HC_EPISODE_MIGRATION_OBJECT_SECRET_KEY；可选 *_OBJECT_REGION。
PYTHONPATH=backend/src python backend/tools/episode_migration.py verify-objects \
  --organization "$MIG_ORG" --project "$MIG_PROJECT" --region "$MIG_REGION" \
  --dataset "$MIG_DATASET" --inventory "$MIG_INVENTORY" --output "$MIG_OBJECT_REPORT"
```

状态为 `EXPANDED_OBJECTS_VERIFIED` 只表示本次已展开对象的字节正确。仍需旧
Lance 副本/逻辑核验、旧导出及标注附件展开、深度/时间/标定语义、固定审核
等价性和批次发布/回退；结果显式保留这些检查。对齐媒体输入适配和原始 MKV/
数组转换也仍继续。不将此命令作为完整迁移通过的唯一判据。
