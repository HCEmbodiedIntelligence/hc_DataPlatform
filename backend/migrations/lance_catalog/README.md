# Lance 目录迁移

请在 database/core 模块提供的事务机制中，按照文件名的字典序执行 SQL 文件。这些语句
可以安全地应用于已经初始化的数据库模式。

生产环境写入器在校验暂存数据、向 Lance 追加数据以及为回执建立索引期间，必须为对应的
项目/数据集持有 `pg_advisory_lock(hashtextextended(lock_name, 0))`。每次向 Lance 追加
数据时，都会将完整回执嵌入事务属性。因此，即使当时 PostgreSQL 不可用、无法写入待处理
记录，对账程序仍然能够完成恢复。
