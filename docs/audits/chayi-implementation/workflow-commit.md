# 新 Episode 包的工作流提交子链路

本阶段继续 G1 W2；没有将三条入口或 G1 整体标成完成。代码位于
`feat/chayi-g1`，服务和测试只使用 `hc-chayi-g1`。

## 实现

`workflow.commit_aligned_bundle` 现在接受内部冻结的 `EpisodeBundlePlan`。
计划绑定完整 Episode 身份、原件 SHA-256、schema、对齐策略和 Arrow SHA-256、
媒体回执哈希、特征映射、深度 profile/掩码/来源证据清单及元数据。配置变化
改变包幂等键。活动执行前再次校验计划和实际请求，拒绝跨作用域、变更配置、
缺少模态以及相互混用的旧/新提交输入。

新分支读取逻辑 Arrow 步数据，写入独立包、生成官方 RealSense 预览、发布
真实集合版本并投影产品条目；不依赖预留的 Lance 当前头，不调用 Lance
暂存或提交。新包输入的 `expected_dataset_version` 必须为空，集合序号在发布事务
中分配。原有准备媒体回执的数字字段仅用于核对那个回执，不用作新包引用或
伪造 Lance 版本。
数据集已有历史 Lance 条目时，集合发布仍可读取历史快照建立混合引用；
这属于历史读取，不会写旧存储。

`DatasetWriterWorkflow` 按输入结构选择新包提交，使用 `episode_commit`
阶段；旧输入仍发出原来的 `workflow.commit_fragment` 命令。选择不依赖环境
开关、当前时间或外部查询。新结果返回真实包/集合引用及 viewer target。
API/worker 装配中的 Arrow adapter 共享同一个逻辑解码器；新方法不访问
Lance schema 仓库。

## 授权、恢复与并发

- **保留旧授权门槛。** 新包成功后不会设置旧媒体回执的
  `dataset_committed_at`。因此旧媒体接口仍返回 425；新包预览只通过完整
  Episode 数据版本引用授权。没有把准备媒体的数字选择器当作已发布集合的
  成员关系，也没有放宽 `PostgresExportAssets` 的 READY 条件。
- **独立恢复。** 已提交包的冻结计划可在完整清单及文件哈希验证后恢复预览、
  集合和产品投影。重试不要求仍然存在暂存 Arrow 或准备媒体，不重写主包，
  不重新量化/编码深度。一般写入接口仍按实际新行校验内容冲突；恢复接口
  只用于已绑定原始对齐字节和媒体回执的冻结计划。
- **消除已复现的媒体提交死锁。** 初版回归在并发更新多个相机的媒体租约时
  触发 PostgreSQL deadlock。`begin_dataset_commit` 和
  `mark_dataset_committed` 现在先按 UUID 排序锁定整组 READY 行再更新，
  不依赖 `UPDATE ... ANY(...)` 的行锁顺序。没有改动作用域或状态约束。
- **工作流导入。** 导入 alignment 模型时不再立即加载 NumPy 对齐引擎；
  `AlignmentEngine` 保持原导出接口并延迟加载。模型/回执和纯特征命名被
  拆出，工作流解码不再动态导入视频处理实现。没有关闭工作流沙箱。

## 已运行验证及边界

`test_workflow_commit_services.py` 四项集成测试通过，并在
`-W error::UserWarning` 下通过，包含：

1. 真实 Arrow 暂存、PG/MinIO、准备 RGB-D 媒体、提交活动、完整包和产品投影。
   丢失成功响应之后，实际删除**该测试刚创建的暂存 Arrow**，禁止访问准备
   媒体和主包写入器，四个并发重试仍返回同一版本；原件/视频未删除。
2. 从尚未提交开始强制四个并发活动同时进入提交路径，其中两份相机回执
   顺序相反。只有一个包版本、一个集合版本，每个 RGB/深度显示副本仅编码
   一次。主 RGB H.265 和灰度深度 HEVC 的哈希与输入相同。
3. 真实隔离 Temporal 服务、独立测试队列、生产 writer workflow 和提交活动。
   注入已持久化产品投影之后的响应丢失，服务执行 attempt 1、2 后成功；
   历史只调度 `workflow.commit_aligned_bundle`，新版沙箱回放通过。
4. 计划篡改、跨组织、混合新旧输入、预留伪造旧版本字段均拒绝；旧活动输入
   仍能解码。新包提交后旧媒体授权保持 425。数据库中该项目没有 Lance
   版本记录，新包对象中没有 `.lance` 文件。

这些输入是 4 帧 16×16 的合成 RGB-D 素材。来源事实直接准备到隔离数据库，
没有经过上传或 MCAP 解析；因此这是**提交子链路**和 writer 工作流的真实
服务测试，不是三入口的完整验收、真实传感器录制或浏览器验收。

历史兼容另以提交 `400ed913` 的原工作流源码生成真实 Temporal 历史，再由
新版 writer 回放。旧工作流源码 SHA-256：
`50a611d9c123f51074f9291a7646341ec1ae038607a5d106773fe149fff977bd`。
旧活动使用内存历史 catalog 测试适配器，没有创建物理 Lance 文件；这份证据
只证明旧 writer 命令序列兼容，不替代真实旧存储迁移或所有父工作流回放。
见 `legacy-writer-replay.json` 及对应历史文件。

最终扩展回归 **85 passed**：`tests/episode_store tests/alignment
tests/aligned_media tests/publishing/test_lerobot_materialized_export.py
tests/workflow/test_workflow.py --fail-on-skipped --fail-on-xfailed -q`，见
`episode-workflow-regression-tests.xml`。四项新增测试单独记录于
`episode-workflow-tests.xml`，已包含在 85 项内。仅有一个已有
Starlette/httpx 弃用警告；变更模块 Ruff 和 `git diff --check` 通过。
隔离 API/worker 及依赖服务健康，8088 现网服务没有本阶段的写操作。

实际新包工作流历史：`episode-package-writer-history.json`，SHA-256
`438ecfca102b6a3e681257cfa05996c430ac1cd43d091da649b1d80b74022b39`。
旧工作流历史：`legacy-writer-history.json`，SHA-256
`5bcf58bb5189dd3cb1284905e592db6ba3cb52a40a3175c7070d459c7b6b1514`。

## 复现

从执行工作树根目录运行：

```bash
bash deploy/chayi/compose.sh run --rm --no-deps test tests/episode_store/test_workflow_commit_services.py --fail-on-skipped --fail-on-xfailed -W error::UserWarning -q
git show 400ed913:backend/src/hc_data_platform/workflow/temporal_workflows.py > backend/.g1-artifacts/legacy-temporal-workflows.py
bash deploy/chayi/compose.sh run --rm --no-deps --entrypoint python test tools/check_legacy_writer_history.py --source /app/.g1-artifacts/legacy-temporal-workflows.py --source-sha256 50a611d9c123f51074f9291a7646341ec1ae038607a5d106773fe149fff977bd --source-commit 400ed913 --output /app/.g1-artifacts
```

测试要求 `HC_TEMPORAL_NAMESPACE=chayi-g1`，并对每次运行使用唯一队列。
`check_legacy_writer_history.py` 仅为加载外部旧源码的证据 worker 使用
unsandboxed runner；新版实际 worker 和回放均使用生产默认沙箱。

## 剩余关键路径与 G2 条件

三条入口的父工作流/计划解析器尚未默认生成和传递新计划；自动标注的数据
绑定、连续录制 READY 状态仍需改为完整数据引用。还需要 native HC 证据传递、
原视频窗口精确物化和 MCAP 深度源单位/有效性处理，然后才能宣称三条入口
不再写 Lance。当前不执行入口开关或 G2 发布，不能用此子链路成功替代它们。

后续 G2 候选须包含 API、workflow/outbox worker 一致版本、全部扩展迁移和
SDK 依赖，并先完成完整入口、标注/审核/交付及浏览器验收；本阶段没有新增
数据库迁移，没有修改现网 8088 数据、旧存储或机器人。其余 W3–W8 工作和
17 项 P0 的完整证据仍按总账保留待完成。
