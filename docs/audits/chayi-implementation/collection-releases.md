# 新集合的业务发布 API（阶段交付）

本次阶段只完成服务、HTTP 和生成契约。P06 的集合发布按钮仍待接入，
不把该 API 交付视为 G1/W8 完成。整个 G1 → G2 → G3 目标按用户指令暂停；
主分支合并另获授权。

发布固定真实 collection_version_id、当前已独立审核的完整标注 JSON、
不可变提交/审核、原数据引用与产品流绑定。预检不发布业务版本；提交须带
原预检 content_hash，再核验当前集合/标注。未审核或多任务未明确选择的
Episode 显式排除。发布只增加小型元数据，不写 Lance 或重复主视频/数值。
成功的业务版本保持不可变；工作集合回退后，已发布版本和默认业务指针保留。
同名同内容重试和并发返回同一回执；不同内容冲突。名称与另一版本 ID 相同
时，发布按精确名称匹配，读取优先采用不可变版本 ID。

HTTP 在明确 organization/project/region/dataset 范围内要求
 dataset_version.publish（读取为 dataset_version.read）和 annotation_task.read。
新 migration episode_store/0018_collection_releases.sql 只在 G1 隔离数据库应用。
本次不应用到 8088 数据库；其部署及迁移仍属于暂停中的 G2。

2026-10-08 候选验证：13 项真实 PostgreSQL/MinIO/gateway 测试通过，无跳过，
含完整标注/原流绑定、三处提交中断/重试、并发、审核变化、回退、精确范围、
不可变 SQL、普通会话、撤销授权、401/403 和版本名称碰撞。
原测试错误预期工作集合回退应失败，现改为验证回退后的不可变业务版本。
生成 OpenAPI/TypeScript 客户端，候选 TypeScript 检查通过。
[阶段 XML](main-merge-20261008/collection-release-before-integration.xml)。

输入来自独立测试创建的合成标准包及审核链，仅代表软件验证。
真实新录制 RGB-D、P06 实际浏览器发布、真人条件、完整 W8 和 G2/G3
尚未据此验收。历史不再批量处理，源未采集深度的条目正常保持 RGB-only。
合并后结果与准确主分支提交见 [合并记录](main-merge-20261008/README.md)。
