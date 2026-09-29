# G1：版本化标注 JSON 阶段证据

2026-09-29；工作树 `hc_DataPlatform-chayi-work`，分支 `feat/chayi-g1`。
这是 W3/ANN-02 的软件实现阶段，G1 尚未完成，17 项 P0 仍保留 Pending。
没有执行 G2/G3，没有修改 8088 业务数据、删除旧存储或启动机器人运动。

## 已实现

- 新修订（包括任务初始修订）与 `annotation.revision_files` 任务、outbox 事件在同一 PostgreSQL 事务提交。回滚不会留下文件任务。迁移 0015/0016 没有回写历史修订，也没有给历史记录伪造新状态。
- 独立 worker 固定读取指定 revision/hash 和完整数据引用。状态为 PENDING、GENERATING、READY、FAILED；旧修订尚未请求生成时，查询返回 NOT_REQUESTED。普通 GET 不创建任务、文件、修订或审核记录；下载只追加访问审计。
- 每代任务通过数据库行锁、租约和 token 防止并发覆盖；中断后的过期租约可恢复。同代已写完整对象优先验哈希复用。显式重试需要已观察的 generation，并发请求只有一个推进下一代；失败重试不创建新修订。
- JSON 格式 `hc-annotation/1` 包含组织/项目/区域、独立 Episode `DataVersionRef` 或实际历史 Lance 引用、修订号/哈希、完整 Tags（父子/交叉区间/属性/对象/关系）、完整 Schema 定义及哈希、累积操作/有效排除区间、半开 step 区间约定和来源事实。时间归一为 UTC；没有混入可变任务状态或生成时间。
- SHA-256 和字节数首次确定后不能更换；生成文件、整对象读取校验完成后才置 READY。对象使用按组织/任务/修订/格式/代数隔离的不可变路径。重建损坏对象会写下一代路径，保留原对象，且必须得到同样的内容哈希。
- 当前实现单文件上限 64 MiB，超过上限会生成失败而不会截断标签。任务分段/带标注完整包/大批量交付仍待后续阶段；本阶段不是这些能力的替代。
- 默认下载精确的当前已审核修订；未审核修订必须显式选择并授权 `allow_unreviewed=true`。校验精确 review/submission/revision 关联，响应头和下载名标记 approved/unreviewed。后续审核不会改写修订 JSON。
- 状态需标注读取权限；生成/重试需 `annotation.save`；下载需 `export.download`。组织/项目/区域、数据库显式谓词和 RLS 都继续生效。已撤销权限不能借 READY 或缓存绕过。另修复原标注服务仅按项目解析权限而误拒绝组织级权限的问题。
- P08 增加“标注 JSON”抽屉，独立显示保存与文件状态，选择修订、刷新、失败重试、下载。未保存修改明确排除；未审核下载需勾选。前端保持下载字节原样并核对 SHA-256/大小，关闭面板或切换作用域取消未完成下载。
- API/worker 正式组合已接入；正式 OpenAPI、运行时契约、生成客户端同步。SDK 深度显示仍复用官方 `rs.colorizer`，此阶段没有改写配色算法或主深度视频。

## 已执行验证

1. 后端扩大回归 **144 passed，0 skipped/xfail**：annotation、episode_store、workflow、publishing service。涵盖真实 PostgreSQL/MinIO 中的事务回滚、文件生成失败、四路并发重试/消费、写完对象后进程中断、租约恢复、旧 worker fencing、固定审核下载、对象损坏拒绝/显式修复、SQL 内容身份保护、组织与动作授权；原有 Tag/旧 Lance 历史逻辑通过。
2. 前端 **200 passed，0 skipped**：全部 P08、共享 HTTP、P06 包媒体/数值读取。新增文件状态与重试、显式未审核下载、默认已审核选择、权限禁用、校验失败、下载字节保持和读取期间租户切换取消检查。
3. 独立运行中的服务链路 **1 passed**：真实 session → 隔离网关 → API → outbox → 运行中的 worker → PostgreSQL/MinIO → 授权下载。测试进程没有直接执行 snapshot handler。修订 0、修订 1、修订 1 第二代共 3 个事件完成，两代 JSON SHA-256 一致；撤销权限后下载和重试均为 403。
4. `tsc --noEmit`、修改范围 Ruff、`git diff --check` 和生成客户端 `--check` 通过。运行时 OpenAPI SHA-256：`b167d926e12d30ba7534d356dfea5883c5da5696620ede52bc97a01a06e0a3f7`。
5. 仅隔离环境应用 0015/0016：**138/138 current**，missing/unknown/checksum_drift 全空。隔离 API/worker 已重启且健康，其他隔离服务保持运行。

真实服务测试仍使用 4 帧、16×16 的合成 RGB-D 软件测试包。它证明持久化与授权链路，不是实际传感器数据、三入口完整业务链路或真人验收。
既有 Starlette/httpx 弃用提示、React 测试 flushSync 提示保留，没有关闭检查以换取通过。

## 复现命令

以下命令全部使用显式隔离 Compose helper。故障注入测试需控制自己的 outbox，建议暂时停止隔离 worker，测试完成后恢复；不得对现网 worker 执行这些操作。

```bash
bash deploy/chayi/compose.sh stop worker
bash deploy/chayi/compose.sh run --rm --no-deps -e HC_ANNOTATION_TEST_POSTGRES_DSN=postgresql://hc:hc@postgres:5432/hc_data test tests/annotation tests/episode_store tests/workflow/test_workflow.py tests/publishing/test_publishing_service.py --fail-on-skipped --fail-on-xfailed -q --junitxml=/app/test-annotation-files-g1.xml
bash deploy/chayi/compose.sh start worker
bash deploy/chayi/compose.sh run --rm --no-deps -e HC_G1_LIVE_SNAPSHOT_WORKER=1 test tests/e2e_runtime/test_annotation_file_worker.py --fail-on-skipped --fail-on-xfailed -q --junitxml=/app/test-annotation-files-worker-g1.xml
bash deploy/chayi/compose.sh run --rm --no-deps frontend-test vitest run src/pages/p08-data-annotation src/shared/api/http-client.test.ts src/pages/p06-dataset-detail/package-media-source.test.ts src/pages/p06-dataset-detail/package-window-source.test.ts --reporter=junit --outputFile=test-results/annotation-files-regression.xml
bash deploy/chayi/compose.sh run --rm --no-deps frontend-test tsc --noEmit
```

## 证据与边界

- [后端报告](annotation-files-tests.xml)、[前端报告](annotation-files-frontend-tests.xml)、[运行中 worker 报告](annotation-files-worker-tests.xml)、[真实服务结果](annotation-files-worker.json)。结果不包含口令或访问 token。
- 历史文件任务需 W7 盘点及显式生成，不能把 NOT_REQUESTED 伪装为 READY。
- 后续仍需三入口新写分派、subtask/派生 Episode、三类独立审核与统一详情、完整带标注与批量交付、真实样本、1440/1024 浏览器/真人流程以及迁移回退演练。未指定模型的深度训练兼容性不在本阶段结论中。

## G2 部署和回退准备（未执行）

1. 将 0015/0016 作为向前的 expand 迁移，与支持新事件的 worker、API 和前端配套发布；现阶段不要启用尚未完成的三入口新写切换。
2. 管理部署为 worker 配置明确组织/项目/区域 scope allowlist，并验证对象存储条件写、读权限、存储容量及失败监控。隔离开发环境使用 local scope discovery；不得据此扩大生产授权。
3. 旧 API 回退后继续写的修订仍由数据库触发文件任务，因此必须保留支持 `annotation.revision_file.requested.v1` 的 worker，或暂停这些任务消费并保留事件，待恢复后处理。未知事件不能标为已完成。不要回滚删除表、修订、事件或对象；本阶段不提供破坏性的 down migration。
4. 下载失败时只重建文件，不修改修订/审核/主数据，也不调整 Lance 版本或 READY 授权。完整 G2 指南仍需与 W7 切换/回退工具及全部验收证据合并后交付。
