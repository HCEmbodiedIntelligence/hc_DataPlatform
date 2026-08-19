# 标注模块迁移

请按照文件名的字典序执行 SQL 文件，并在单独的事务中执行每个文件。第一个迁移会创建
`annotation` 模式、Task/Draft/Current/Revision/Operation/Review 持久化约定、持久化的
客户端变更记录、延迟校验的精确修订版本外键，以及项目级行级安全（RLS）策略。

数据库会强制要求修订、操作、审核和变更记录只能追加。`RESTORE` 必须作为新操作插入；
适配器不得更新或删除历史记录。对于非所有者应用角色，BE-02 数据库组合层会设置事务内
局部生效的 `app.project_ids` 和 `app.is_admin` 值。

`0002_tag_schema_revisions.sql` 增加版本化 Tag Schema、固定 Lance/Schema 基线、完整 Tag
快照、可审核提交、提交幂等记录和旧 P11 cleaning 映射。Tag Schema 内容在发布前即按版本
创建，发布后由触发器冻结；没有最大层级数据库常量。旧记录的原作者、时间、审计事件和
源 payload 保存在 revision/mapping 中。迁移只扩展 annotation schema，不创建 cleaning
schema、表或 HTTP 资源。
