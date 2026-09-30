# W7 只读迁移清单阶段

2026-09-30。仅 G1 隔离服务；这是引用清点工具和软件证据，不是迁移完成报告。
批次转换、物理核对、checkpoint 续跑及指针回退仍待实现/演练。未执行 G2/G3。

## 已交付

- `backend/tools/episode_migration.py inventory`：显式指定组织、项目、区域、数据集
  和输出路径；数据库只从 `HC_EPISODE_MIGRATION_DSN` 读取，不回退到应用默认连接。
  工具供持有数据库权限的运维使用，没有新增跳过应用授权的 HTTP 接口。
- `episode_store/migration_inventory.py` 在同一个只读、可重复读事务内清点
  产品版本/流/来源/审核、旧 Lance/schema/lineage、媒体、原件引用、连续录制
  切片/资产、标注 revision/submission/review/操作、旧发布/导出、新包和当前指针。
  实际查询 PostgreSQL 的事务设置确认 `repeatable read`、`read_only=on`。
- 保存完整行哈希；对含运行令牌的媒体/上传资产表只输出选定业务字段，完整
  行仍参与哈希。旧标注/提交/审核行与内容哈希不改写、不自动转移审核结论。
- 按持久化 stream/lineage 创建显式 `LegacyEpisodeRef` 候选，匹配现有历史
  reader 的身份算法；目标引用为空，状态 `AWAITING_VERIFICATION`。缺少或
  不一致的绑定标为 `REFERENCE_BLOCKED`，不会从名称猜测 rollout。
- 清单以内容哈希固定，文件权限 0600，临时文件 fsync 后原子创建，不覆盖已有
  不同清单。同一内容的并发/重复调用复用文件；篡改哈希被拒绝。单表超过
  `--max-rows`（默认 10000，上限 100000）时失败，不输出截断的“完整清单”。
- 连续录制通过处理账本关联切片和原件。产品 Episode ID 与 recorder slice ID
  可以不同，不能直接比较这两个 ID；实际三入口清点发现并修正了该遗漏。

## 已执行及实际结果

- [最终服务测试](migration-inventory-tests.xml)：5 passed，无 skip/xfail。
  实际 PostgreSQL/MinIO、历史 Lance/新 Parquet 混读夹具、固定标注/审核、三路
  并发文件保存、篡改/覆盖拒绝、行数限制，以及组织/项目/区域隔离。
  运行中保留既有 Starlette/httpx 弃用提示。
- 扩展 QC 清点时，误把 QC 表当作 data_version 绑定，出现 2 failed/3 passed；
  [原始失败证据](migration-inventory-before-qc-fix.xml)保留（仅去行尾空白）。按
  实际 rollout 绑定修复后，最终上述五个用例全部通过。没有跳过 QC 表。
- 实际 CLI 对旧 Lance/新包混合数据集，以及此前 MCAP、连续录制、原生 LeRobot、
  独立 uploader 四份隔离输出运行成功。再加固定审核服务测试，共六份
  [清单哈希、作用域、计数及候选摘要](migration-inventory-results.json)。
  所有输入仍来自合成软件夹具，没有新增真实录制/真人证据。
- 连续录制清点到 1 条 processing、1 条 recording、2 个切片修订、2 个切片、
  36 个窗口引用、36 个录制资产及 15 个机器人上传资产。不同表可引用相同来源，
  不能把这些计数相加当作独立原件数量。
- 原生样例保留 2 个标注修订、1 次提交、1 个审核；MCAP 保留 Raw 及单个
  rollout 原件引用；机器人上传样例保留 15 个资产引用。旧 Lance 候选和新包
  分别使用真实旧版本号、正式 DataVersionRef，没有给新包伪造 Lance 版本。
- Ruff、格式和 diff 检查通过。没有新增 SQL 迁移，没有修改存储内容或可见指针。

## 复现

```bash
# DSN 由操作者显式提供；不要将密码写进命令记录或报告。
PYTHONPATH=backend/src python backend/tools/episode_migration.py inventory \
  --organization ORGANIZATION --project PROJECT --region REGION \
  --dataset DATASET --output /secure/path/inventory.json

# G1 服务测试只使用隔离连接。
bash deploy/chayi/compose.sh run --rm --no-deps test \
  tests/episode_store/test_migration_inventory_services.py \
  tests/episode_store/test_collections_services.py::test_actual_legacy_lance_and_new_parquet_in_one_collection \
  --fail-on-skipped --fail-on-xfailed -q
```

完整清单路径记录在摘要中，位于执行工作树 `backend/.g1-artifacts/migration-inventory`。
清单不是数据库备份，也不是可回退运行状态；历史混读夹具的 Lance 位于测试临时
目录，不能把其清单直接当作已准备好长期迁移的物理副本。

## 必须继续的 W7 工作

当前 dry-run 明确保持 `physical_objects_verified=false`、
`raw_manifests_expanded=false`、数值/时间轴/深度/审核等价验证为 false。
原件 manifest 的全部文件需继续展开并逐个校验；缺单位、profile、有效性证据
时保留缺项，不能凭数据库行数推断深度完整。

随后实现固定清单上的批次转换、持久 checkpoint/幂等恢复、不可变迁移映射、
提交前等价核验、带前置状态比较的指针发布/回退，并在隔离副本完整演练。
还需处理回退后的版本号分配、混合历史读取和新 RGB-D 包的兼容读能力。G2
部署/备份/任务排空/跨端矩阵及真实归属另行交付，当前不执行现网切换。
