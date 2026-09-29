# G1 标注数据绑定阶段证据（W1/W3/W5 部分）

日期：2026-09-29。独立工作树 `hc_DataPlatform-chayi-work`，分支 `feat/chayi-g1`，前一阶段 `0f0a54a9`。这不是 G1 完整验收，也没有执行 G2/G3。

## 实现

- 新标注任务、草稿、不可变修订、提交、审核及审批快照保存完整 `DataVersionRef`：组织/项目/区域、Dataset/Episode/rollout 身份、数据版本、内容哈希、Schema、包前缀和帧数。新绑定的 `base_lance_version` 为 NULL；同时提供两个基线或都不提供会被拒绝。
- 旧修订哈希的输入文档保持原样，不增加一个会改变哈希的 `data_ref: null` 字段。旧 Lance 基线继续可读，并恢复对数据库已有 `LEGACY` creation source 的解析。没有回写历史修订或伪造 Lance 版本。
- 新自动任务 ID 使用完整引用和集合版本确定。自动任务/触发器、修订零和审计仍在原子事务内写入，丢失响应及并发重试复用同一个任务。自动标注 provider 请求、review preparation workflow 都传递新引用；这不是任何模型已支持深度训练的声明。
- `0013_episode_data_bindings.sql` 为相关表添加 JSONB 引用，旧列保留并允许新记录为 NULL。数据库校验完整引用等于 COMMITTED receipt，且完整引用存在于指定集合版本。子修订/提交/审核校验同一任务的数据和 Schema，提交还校验修订哈希，审核校验精确 submission/revision。
- `0014_binding_scope_and_identity.sql` 把 Tag Schema 兼容绑定的主键补齐组织维度，并保护任务的固定基线。测试发现旧自动 Schema 查询在超级用户测试连接下可能选到同名项目的另一组织绑定，现已补齐显式组织条件；任务读取、修改和 Schema 读取也增加组织条件，原有 RLS 保留。
- P08 从任务固定引用读取标准包元数据，派生相机/Topic 清单，读取固定版本关节窗口，并复用 P06 的 RGB/官方 RealSense 深度预览授权。对任务、草稿、历史修订、提交、审核之间的引用不一致立即报错；不会退回 Lance head。旧入口和 Tag 编辑/提交/审核命令保留。
- 深度预览说明使用 RealSense Dynamic/Jet 动态着色，颜色不对应固定距离，无效深度为黑色。本阶段没有另写调色算法，也没有改变原始 H.265/gray12le 主视频。
- 历史 Lance 发布适配器明确拒绝借用一个新标准包的审批，即使集合版本数字恰好相同。带标注标准包/混合集合导出仍需 W3/W6 的固定修订实现，不能绕过这一限制。

## 验证及复现

只针对 compose project `hc-chayi-g1`，使用真实隔离 PostgreSQL、MinIO 和 Temporal。新生命周期测试的输入是 4 帧 16×16 合成 RGB-D，验证软件契约，不替代真实采集/上传或真人验收。

```bash
bash /home/hc_op/workspace/hc_DataPlatform-chayi-work/deploy/chayi/compose.sh run --rm --no-deps --entrypoint python test -m hc_data_platform.core.migrations upgrade-expand
bash /home/hc_op/workspace/hc_DataPlatform-chayi-work/deploy/chayi/compose.sh run --rm --no-deps -e HC_ANNOTATION_TEST_POSTGRES_DSN=postgresql://hc:hc@postgres:5432/hc_data test tests/annotation tests/episode_store tests/workflow/test_workflow.py tests/publishing/test_publishing_service.py --fail-on-skipped --fail-on-xfailed -q --junitxml=/app/test-annotation-binding-g1.xml
bash /home/hc_op/workspace/hc_DataPlatform-chayi-work/deploy/chayi/compose.sh run --rm --no-deps -e HC_ANNOTATION_TEST_POSTGRES_DSN=postgresql://hc:hc@postgres:5432/hc_data test tests/annotation tests/episode_store/test_annotation_bindings_services.py --fail-on-skipped --fail-on-xfailed -q --junitxml=/app/test-annotation-binding-final-g1.xml
bash /home/hc_op/workspace/hc_DataPlatform-chayi-work/deploy/chayi/compose.sh run --rm --no-deps frontend-test tsc -b --pretty false
bash /home/hc_op/workspace/hc_DataPlatform-chayi-work/deploy/chayi/compose.sh run --rm --no-deps frontend-test vitest run src/pages/p08-data-annotation src/pages/p06-dataset-detail/package-media-source.test.ts src/pages/p06-dataset-detail/package-window-source.test.ts --reporter=junit --outputFile=test-results/annotation-package-regression.xml
```

后端扩大回归 **139 passed**；最后补齐 `LEGACY` 枚举和旧记录生命周期样本后，相关标注回归 **76 passed**。后端仅有现存 Starlette/httpx 弃用警告。扩大回归包含真实 Temporal writer 重试及 sandbox replay；此阶段未把它扩称为完整三入口工作流验收。

新增真实服务证据包括：

1. 没有兼容 Schema 时记录可重试阻塞；补齐 Schema 后创建任务。
2. 事务已成功但响应丢失，再由 4 个线程重试，返回相同任务/引用；触发器只有一条。
3. 保存包含父子与交叉区间的 Tag，重复保存返回相同修订；4 次并发提交返回同一 submission。
4. 返工审核、恢复历史修订、再次提交与批准，旧修订/提交/审核保持不变，所有阶段绑定相同主包。
5. 伪造内容哈希、包前缀、数据版本或集合版本无法建任务；直接 SQL 改任务引用或写入错误引用的子修订被拒绝。
6. 已知任务 ID 在另一组织/区域/项目不可读；自动任务写入在错误作用域被拒绝。相同项目名称的两个组织可各自绑定 Schema，不串读。
7. 新标准包审批不能授权同数字版本的 Lance 导出；测试组织没有新增 Lance 版本。

前端扩大回归 **153 passed**（13 个文件），TypeScript、Ruff、正式契约与生成客户端检查通过。前端测试保留已有 React flushSync 调用警告；没有以关闭警告代替检查。

P08 单元/组件测试使用请求替身，证明解析、固定绑定、官方预览授权请求、关节数据读取和原编辑交互。它们不是实际浏览器或用户权限链路验收。扩大 UI 测试发现上一阶段源码已有的 4 个失败：布局样本没有声明 joint Topic，断言也仍查找旧标题“关节角变化”，实际标题是“关节位置变化”。使用 `0f0a54a9` 的 Workbench/adapter/joint source/fixture 文件只读覆盖到隔离测试容器复现了同样 4 个失败，随后修正测试样本与断言；产品没有为缺数据伪造曲线。

正式 OpenAPI fragment 与聚合契约同步，TypeScript 从生产组合方式生成的 runtime contract 生成。runtime SHA256：`8101d14f3ff69cbc2045837b64e5b133673277e96ad6ba657b55261255504d78`。导出 runtime 时使用独立的临时 staging 目录，避免测试 UID 1000 和服务 UID 65532 共用私有缓存目录；不改变服务目录权限。服务禁用了 `/openapi.json` HTTP 路由，因此运行时契约由正式 exporter 生成，未声称从该 HTTP 路由读取成功。

归档证据：

| 文件 | 测试数 | SHA256 |
| --- | ---: | --- |
| [annotation-binding-regression-tests.xml](annotation-binding-regression-tests.xml) | 139 | `ab6495de0a8b5d26243f8f43b6eaf988aa00d2002a5c8113ebc53c57f74e5bdb` |
| [annotation-binding-tests.xml](annotation-binding-tests.xml) | 76 | `88aa95b31924c02624c61cbe9097da79cae970f529305f0068e84bcedf143ca5` |
| [annotation-package-frontend-tests.xml](annotation-package-frontend-tests.xml) | 153 | `4febb88c9531cd06c7eb0d25c242314e624bd355ce7d4fb8cbaab912c7d49886` |

测试范围有重叠，不将测试数量相加当作独立验收项。隔离 compose 的 8 个服务运行，7 个带健康检查的服务均 healthy；gateway 未配置 healthcheck。实际 `48000/health/live` 返回 `{"status":"live"}`。

## 部署顺序与回退边界（G2 说明，未执行）

1. 保留旧表、对象和历史修订；先应用 manifest 中的新增迁移 0013/0014，再部署支持两种引用的 API、worker 和前端。迁移没有删除历史数据，没有重算旧修订哈希。
2. 本阶段只在 G1 执行迁移并重启 API/worker；健康检查恢复。迁移状态为 136/136 current，missing/unknown/checksum_drift 均为空。不要在此阶段启用三入口的新写分派：它们的 source plan 生成、父工作流和 continuous READY 仍未完成。
3. 回退必须保留新表字段及已有标准包/标注任务，并保持能读取两种引用的服务。旧二进制要求非空 Lance 字段，不能直接拿它读取新任务；禁止给新任务补假 Lance 数字或删除新任务来“兼容”。完整选择性分派回退/迁移演练仍属于后续 W7 交付。

## 尚未完成

- 三入口父工作流还没有生成新冻结 plan 并传递新自动标注引用；本阶段没有切换真实入口。
- 标注修订 JSON 的事务 outbox、文件状态/重试/下载，subtask 派生包、三类独立审核与完整统一详情仍待后续实现。
- 完整包/带标注包批量集合交付、真实源深度/原生包/连续窗口契约、迁移工具和回退演练、离线真实 RGB-D reader/DataLoader、1440/1024 浏览器及真人条件仍需证据。
- 原始录制、8088 业务数据和旧存储未修改；没有启动机器人运动。17 项 P0 继续按完整范围保持 Pending。
