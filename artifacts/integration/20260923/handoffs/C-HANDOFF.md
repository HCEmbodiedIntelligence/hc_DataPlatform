# 任务 C 交付记录

日期：2026-09-22。**C 可独立交付的调度、恢复和机器人结果接口已完成并提交；OpenArm 真实 Episode/QC/数据/媒体成功闭环尚未通过，依赖 B 交付与 E 接线。**

| 项目 | 版本 |
| --- | --- |
| 仓库 | `/home/hc_op/workspace/openarm-integration/C/platform` |
| 分支 | `feat/robot-ingest-pipeline-v1` |
| 基线 | `2f6022202728f7c18852c0afd0fbd8df4244f362` |
| 代码、测试和证据交付 SHA | `726ce937af827472fcaead41189d9e64f57b4872` |
| 本记录 | 在上述实现提交之后单独封存；最终交付包含这两个本地提交 |
| 契约 | `openarm-capture/v1` / `g0-20260922.1`，未修改公共契约 |
| g0/release.json SHA256 | `4e074444877cd037dbbb9a3f5045333638e480f107c5e4abec4ee0806a11dae8` |
| valid-openarm 内容 hash | `0cddb74670559656850fd78ebe0451efd701d6e167a8026fe30620cbdba2fa32` |

## 已实现

- 完整的 PRESEGMENTED LEROBOT_V3 / v3.0 机器人 commit 在同一 PostgreSQL 事务内保存 Raw、job、upload、持久处理记录和 outbox。并发 commit 用行锁串行化；S3 条件创建 manifest 冲突时验证不可变身份和资产后幂等继续。
- 新 outbox handler、Temporal workflow 和真实 Worker activities。事件重复投递不重开工作流，Worker 重启可回放历史；活动/结果更新按 generation 拒绝旧尝试。处理失败/完成的持久投影活动持续重试数据库故障。
- 逐 episode 保存结果；upload JSON、Raw 汇总、job 和 raw_source_episodes 同事务更新。显式重试的 request_id 持久去重，只重置 retryable 的失败段，保留成功与质量终态身份/receipt。历史完整兼容且尚无业务 episode 的 Raw 可显式恢复，不重新上传字节。
- 新机器人凭据接口 `GET .../{upload_id}/processing`、`GET .../{upload_id}/processing/diagnostics`、`POST .../{upload_id}:retry-processing`。先校验所属 upload，再取作用域；另一机器人 404，用户 JWT 401；两张新增表有组织/项目/Region RLS。
- 源 episode ID/index 的处理器边界、稳定平台 episode 身份、error_code/retryable/next_action、5 秒处理中轮询与终态 0 秒规则。COMMITTED、PROCESSING、READY/PASS、READY/RISK、READY/REJECT、技术失败分别表达。质量拒绝不会计为合格或触发技术重试。
- 外部处理器异常在 Temporal 日志/持久历史边界转换成不含原异常文本的稳定错误码；签名 URL 不进入结果。

主要实现文件位于 `backend/src/hc_data_platform/robot_ingest/processing_*.py`，迁移为 `backend/migrations/ingest/017_robot_processing.sql`，自动测试为 `backend/tests/robot_ingest/test_processing_*.py`。没有修改共享 runtime、worker 总装配、路由总表、OpenAPI 或迁移清单。

## 自动测试与实际链路

**最终 44 项通过**：C 套件 42 项，原 PostgreSQL 回归 2 项；ruff 和 git diff --check 通过。存在一个基线 Starlette TestClient 弃用提示。

| 检查 | 结果与证据 |
| --- | --- |
| 要求的 C Compose config --quiet | 通过 |
| 要求的原始 build migration | 已执行，Docker Hub 固定 Python 镜像元数据下载超时，未成功 |
| C 专用缓存依赖镜像构建 | 通过；仍经 G0 C Compose 入口，使用 C checkout；[覆盖文件](compose.cached.json) / [Dockerfile](Dockerfile.cached) |
| 要求的 test-platform.sh C | 29 passed，最终 42 项套件包含同样 29 项回归 |
| C 新增测试 | 13 passed：真实基础设施/身份/历史恢复 3 项，receipt 替身状态机 3 项，状态/脱敏单元测试 7 项 |
| 原 PostgreSQL 上传/归属回归 | 2 passed，在 C PostgreSQL 独立 `hc_c_regression` 数据库；[JUnit](evidence/postgres-regression.xml) |
| 最终 C 套件 | 42 passed；[JUnit](evidence/c-tests.xml)、[结果摘要](evidence/c-tests.log) |
| 环境与未执行项 | [机器可读汇总](evidence/summary.json) |

真实链路为：FastAPI 机器人创建上传/授权分片 → HTTP PUT MinIO → complete 真实 SHA256/CRC64 验证 → commit PostgreSQL Raw/job/outbox → Temporal 持久 workflow → 真实 Worker 读取 MinIO `meta/info.json` → **ROBOT_PROCESSOR_UNAVAILABLE 的失败回写** → 机器人终态轮询/显式重试。没有调用 `apply_processing_result` 伪造成功。

本轮可核查 ID（[完整 JSON](evidence/real-infrastructure.json)）：

```text
upload:   riu-46cfe99059f252699786d27db046ee7b
raw:      raw-2810cacb8002502096eeb28c7139b54d
workflow: robot-processing-0e5573f39ed95cec967b81442cccc244
retry:    robot-processing-50bfb65c14cd57dca65960039cea0115
数据库:   Raw=1，job=1，episode=0
结果:     COMMITTED / FAILED / QC=PENDING；terminal=true；poll_after_seconds=0
诊断:     ROBOT_PROCESSOR_UNAVAILABLE；retryable=true；next_action=retry_processing
```

故障证据包含：事务提交前异常后 Raw/job/outbox 不留假提交；成功响应忽略后 GET/重复 commit 对账；outbox start 成功但不确认、租约到期后重投；Worker 离线时提交；Worker 在调度活动后停止并由新实例回放继续；正常 serve_outbox 自动消费重试；旧 generation 拒绝回写；四个同步并发 commit 只产生一个 Raw/job/event；投影写入后注入异常时整事务回滚；历史恢复；不同机器人权限与非超级用户 RLS。

PASS/RISK/REJECT、多 episode 一段失败后只处理失败段的测试使用明确命名的 `ReceiptDouble`，只证明 C 状态机/事务/去重，不是 LeRobot 解析、QC 或媒体发布验收。分别见 `evidence/orchestration-double-*.json`，均写明 `openarm_success_verified=false`。

复现 C 测试：

```bash
bash /home/hc_op/workspace/openarm-integration/C/platform/artifacts/g0/test-processing.sh --cached
```

所有测试容器操作经过 G0 C Compose 入口；独占 `openarm-g0-c` project、端口、PostgreSQL/MinIO 卷和 synthetic 任务。API 用实际 FastAPI 路由的 TestClient，尚非远程产品服务器。原离线平台与现有生产数据库未修改。C 测试容器/数据保留以便检查，停止办法见 E 文档。

## B、D、E 交接

- **B：** [接口和具体依赖差异](B-INTERFACE.md)。检查时 B HEAD 仍为基线，已有未提交 `committed.py` 等开发。C 未使用其未提交代码。B WIP 的 normalized manifest、平台 episode 身份和 `prepare`→真实 ingest 发布回执与 C 边界尚需适配；当前 ProcessorUnavailable 不是 B adapter。双方正式接口尚未联调确认。
- **D：** [完整 API 示例、错误码与轮询/幂等重试规则](D-API.md)。接口模型符合 G0 proposed 字段/枚举，保留旧 GET upload 响应模型。发现失败尚无 episode 时用附加 diagnostics，不制造源映射。
- **E：** [迁移顺序、runtime/router/worker/outbox 精确挂接点与恢复说明](E-HOOKS.md)。必须先迁移再部署 commit 代码；并将新 activities 纳入现有维护模式/writer-permit/生命周期守卫。默认产品装配尚未接线。

## 未执行与限制

1. B 真实通用/OpenArm 解析、源映射验证、QC、业务 Episode、数据与媒体发布及结果回写的成功闭环；包括 A 实际导出、缺视频/错维度等格式负样本。本轮基础设施测试把 G0 字节路由到 C 隔离任务/机器人，不能代替 B 的采集上下文与权威身份一致性检查。
2. B 发布完成但 C receipt 尚未保存时的真实重试幂等；C 的稳定 episode_id/attempt_id 已提供，B 的持久幂等实现仍待验收。
3. E 公共接线、维护模式联动、正式迁移清单/发布、远程机器人/IP/gateway/视频播放，以及 U5 真采集。没有启动或部署新产品 API/前端。
4. 原在线 Dockerfile 全新构建。缓存依赖镜像 ID 和基础设施镜像 ID 已固定在 summary.json；缓存构建不冒充原在线构建成功。
5. 手动终止 Temporal workflow、超大包/10,000 episode 压测、持续故障的运维恢复未验收。当前实现按 Raw 顺序处理 episode。

因此本次结论是 **C 独立部分可交付，G5 完整闭环仍受 B/E 依赖阻塞**；不应把 44 个自动测试或 receipt 替身的 READY 计为 OpenArm 数据成功入库。
