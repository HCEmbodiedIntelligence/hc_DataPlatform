# 发布模块迁移

`0001_publishing.sql` 定义了 BE-11 仅追加的 PostgreSQL 约定：

- `dataset_versions` 按项目、数据集和版本标识符存储一份不可变清单。
- `publication_assets` 固化确定性的 `annotations.lance` 覆盖层和训练清单。
- `export_attempts` 是唯一可变的生命周期记录，并且始终指向对应尝试的暂存位置。
- `published_exports` 仅在原生重新加载校验通过后插入，此后不可修改。

数据库触发器会拒绝更新或删除已发布版本、发布资产和已提升的导出记录。所有项目范围内的
表都会启用 RLS，以配合 BE-02 限定作用域的工作单元会话上下文。
