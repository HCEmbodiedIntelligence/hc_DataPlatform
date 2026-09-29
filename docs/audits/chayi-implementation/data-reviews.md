# G1 单条 Episode 人工数据审核阶段（2026-09-29）

本阶段在 `feat/chayi-g1` / `hc-chayi-g1` 增加独立的采集合规审核，属于 W4/W5 的
部分交付。现有数据集版本审核针对整个输出版本，不能代表单条 Episode 的人工决定。
全部 17 项 P0 仍按完整要求验收；本报告不宣称 G1 或 QC-01 已完成。

## 实现行为

- 固定版本下新增 `GET/POST .../episodes/{episode_id}/data-versions/{data_version_id}/data-reviews`。
  记录完整 `DataVersionRef`、审核人、时间、意见、APPROVED/RETURNED、递增序号及前一
  决定。没有记录明确返回 NOT_REVIEWED；另一数据版本从独立的未审核状态开始。
- 人工审核意见可定位到从 0 开始的 `[start_step,end_step)`，或精确 task/revision/
  revision hash/annotation ID。后端和数据库均验证标签属于同一数据版本；不把父
  录制帧号当作 Episode 帧号。当前面板支持区间输入、标签定位的历史展示；标签选择
  交互仍待统一详情/标注入口整合。
- 新命令锁定已提交版本行，核对 content hash 和 expected_sequence。并发不同决定
  只有一个成功，其他请求 409；相同审核人/版本/幂等键的同内容重试返回原记录，即使
  后续已追加另一决定。相同键不同内容拒绝。每条成功新决定与审计在同一事务提交。
- 数据库约束与触发器绑定精确已提交回执、组织/项目/区域、前一记录和连续序号；禁止
  UPDATE/DELETE。不修改包文件、QC 报告、标注修订/提交/审核或 READY 状态。
- 读取要求 dataset_version.read；提交还要求 dataset_version.review；标签定位还要求
  annotation_task.read。每次重试也重新检查权限；返回逐动作 allowed_actions 和缺失
  capability 原因。路径只接受版本身份，不接受任意对象路径。
- P06 原有“数据信息”抽屉接入审核状态、历史、分页、意见和可选帧定位。通道绑定必须
  指向同一数据版本；缺少 package binding 的旧入口不伪造版本。保留原播放器、共享
  时钟和问题清单命令。网络中断后保留同一命令和幂等键，明确刷新后可基于新状态提交。
- 正式 OpenAPI、runtime 契约和生成 TypeScript 客户端已更新；分页/缺失状态和
  401/403/404/409/422 错误沿用现有 ProblemDetails 机制。

## 实际运行的验证

1. [后端 XML](data-reviews-backend-tests.xml)：31 项通过，含 5 项新 PostgreSQL/
   RGB-D 服务测试、1 项真实网关测试及 25 项迁移回归。覆盖丢失响应后四并发重试、
   不同决定并发、历史分页、同键冲突、错误内容哈希/区间/标签、租户隔离、数据库
   UPDATE/DELETE/伪造回执/错误序号拒绝，以及标注审核与人工数据审核独立变化。
   测试证实两个审核操作前后 hc/quality.json 和主包引用不变。
2. [真实 HTTP 证据](data-reviews-http.json)：隔离普通账号真实注册/登录，经 gateway
   → API → PostgreSQL，无认证依赖替换。四个同键 HTTP 并发得到同一记录，返工后
   追加通过并可分页读取历史；另一个数据版本仍未审核。只读账号不能提交，跨组织/
   区域/条目被拒绝，标签定位缺少权限被拒绝，撤销审核 capability 后旧请求重试也
   被拒绝。只有测试账号初始成员/权限授予使用隔离 DB 初始化；测试结束已注销会话。
3. [前端 XML](data-reviews-frontend-tests.xml)：16 项通过，含 13 项新 API/组件测试
   及 3 项原 Viewer 测试。覆盖不一致版本/状态/分页/区间响应、混合版本通道、权限
   门禁、提交内容绑定、网络失败重用命令键、精确帧边界及原播放器/问题清单交互。
   组件测试使用替身，不计作真实浏览器或真人任务验收。
4. TypeScript、所改前端 ESLint、后端 Ruff、正式/runtime 契约生成与客户端 `--check`
   通过。隔离迁移状态为 145/145，missing/checksum drift/unknown 均为空。

验证输入仍为明确标识的 3/4 帧合成软件夹具。此次没有新增真实采集任务、真人审核
或 1440/1024 浏览器证据。自动 QC 旧报告缺少逐规则执行证据，不能据“没有 finding”
推断每条规则通过；逐规则新证据、固定版本聚合 API、三类状态的统一详情仍继续实现。
完整基础包通用缓存、批量 RGB-D 集合、Tag 派生 Episode、W7 迁移/回退演练亦未完成。

## 复现与将来 G2 部署条件（本阶段未执行 G2）

只使用 `deploy/chayi/compose.sh` 的隔离服务。测试命令为：

```bash
bash deploy/chayi/compose.sh run --rm test tests/episode_store/test_data_reviews_services.py tests/episode_store/test_data_reviews_http.py tests/core/test_migrations.py -q --junitxml=.g1-artifacts/data-reviews-backend.xml
bash deploy/chayi/compose.sh run --rm frontend-test vitest run src/pages/p06-dataset-detail/data-reviews.test.ts src/pages/p06-dataset-detail/DataReviewPanel.test.tsx src/pages/p06-dataset-detail/ViewerShell.test.tsx
```

新增 `episode_store/0011_data_reviews.sql` 是 additive expand，已列入 manifest；将来
批准 G2 后应先应用迁移再上线匹配的 API/前端。人工审核角色需现有 dataset_version.read /
dataset_version.review，标签定位另需 annotation_task.read，不提供隐式授权或绕过。
回退应用时停止新审核写入，保留新增表、审计及全部旧数据；旧版本不会读取这些记录，
恢复本版本可继续历史和序号。没有 destructive down migration。

本阶段未修改 8088 业务数据、旧存储或机器人。深度预览继续调用官方 RealSense
`pyrealsense2.colorizer` Dynamic/Jet，详见 [官方预览证据](realsense-preview.md)；本阶段
没有更改 H.265 主视频或深度量化 profile，也没有扩展模型训练兼容性的验收声明。
