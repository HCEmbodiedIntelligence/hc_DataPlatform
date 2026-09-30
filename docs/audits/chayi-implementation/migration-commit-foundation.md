# W7 批次迁移的提交基础

2026-09-30，G1 隔离库/对象存储，合成故障夹具。此阶段只补提交基础；尚未完成
批次迁移命令、checkpoint、指针回退工具或完整隔离副本演练。

- 内部包提交器新增显式 `activate=False`：保持原有全文件、视频、深度证据、
  hash 和事务验证，提交不可变完整包及收据，但不推进 Episode 当前指针。
  已提交包仍可按精确版本经过正常作用域校验读取。没有放松 READY 授权或
  提交前资产读取条件，没有新增公共 HTTP 开关。
- 文件写入、对象核验、数据库提交任一步中断后，重试核验已有字节并复用
  同一版本。当前指针维持旧值（或原本为空）。默认重试一个已提交版本也不会
  隐式激活它；后续批次发布必须有单独的前置状态核对和事务。
- 集合发布按保留历史的最大版本号分配新版本。指针回到旧快照后，再次写入
  不会重复使用已经存在的版本号；之前所有不可变快照继续可读。
- [提交服务回归](migration-deferred-commit-tests.xml) **16 passed**：含旧指针有/无、
  三个中断点、重复/默认重试、四路并发、损坏对象、跨租户、事务故障等。
- [集合服务回归](migration-collection-allocation-tests.xml) **6 passed**：含原恢复/
  并发/混合 Lance 读取、清单，以及指针模拟回退后继续创建 v3、保留 v2。
  该用例直接调整自己的隔离指针来建立测试前提，**不算 W7 回退工具演练完成**。
- Ruff、格式、diff 检查通过；无新增 SQL/OpenAPI/前端修改，不涉及 8088、
  旧存储回收、机器人运动或 G2/G3 执行。

```bash
bash deploy/chayi/compose.sh run --rm --no-deps test \
  tests/episode_store/test_commit_services.py \
  tests/episode_store/test_collections_services.py \
  --fail-on-skipped --fail-on-xfailed -q
```

接下来在固定清单上完成物理清点/等价核验、持久 checkpoint 与不可变映射，
再原子发布整批集合/Episode/产品指针，并验证带并发前置条件的回退。回退必须
保留读取已存在新 RGB-D 包的服务；不得启用看不到新包或深度的旧二进制。
