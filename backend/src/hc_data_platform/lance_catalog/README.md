# Lance 目录（BE-08）

该模块负责确定性编译数据集模式快照、为每个项目/数据集/模式/频率维护一个共享的
`aligned_steps.lance`、管理不可变逻辑版本与 rollout 血缘，并提供稳定的
`(rollout_id, step_index)` 读取。它绝不执行对齐或修改步骤，也绝不公开 Lance 的物理行地址。

`LanceCatalogService` 协调三个端口：

- `LanceStoragePort`：负责不可变尝试暂存、清单验证、追加、事务回执和逻辑读取。
  `LanceAdapter` 是真实的 pylance 实现。
- `CatalogRepositoryPort`：负责模式、逻辑版本、血缘、幂等性和待协调记录。
  `PostgresCatalogAdapter` 是生产环境中的 DB-API 实现。
- `DatasetWriterLockPort`：在暂存验证、Lance 追加和 PostgreSQL 建立索引期间，
  确保每个项目/数据集只有一个写入者。

在更新 PostgreSQL 之前，Lance 事务会嵌入完整的 `StorageCommitReceipt`。重试或
`reconcile()` 会扫描这些回执，为缺失的版本建立索引，而不会再次追加数据行。
重试时尝试 ID 和暂存 URI 可以变化；数据集内必需的幂等标识仍为
`rollout_id + source_sha256 + converter_version`。

`InMemoryLanceCatalog` 仍是可执行的参考替身。更小的 `InMemoryCatalogRepository`
和 `InMemoryDatasetWriterLock` 用于在没有 PostgreSQL 的情况下测试真实适配器。

在 `backend/` 目录运行检查：

```bash
.venv/bin/ruff check src/hc_data_platform/lance_catalog tests/lance_catalog
.venv/bin/mypy src/hc_data_platform/lance_catalog
.venv/bin/pytest tests/lance_catalog
.venv/bin/pytest -m integration tests/lance_catalog
```
