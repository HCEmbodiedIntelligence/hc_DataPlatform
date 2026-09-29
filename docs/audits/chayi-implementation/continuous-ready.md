# 连续录制标准包 READY 约束与页面绑定

本阶段给连续录制处理账本增加完整 `data_ref` 和 `collection_version_id`。
READY 必须选择标准包或真实历史 Lance 绑定之一。新包保留 `lance_version=NULL`，
不申请旧数据集版本 reservation；P23 使用真实集合 ID 跳转，历史入口保持兼容。
状态服务重新验证模型，避免 `model_copy` 绕过校验；同时到达的相同状态提交会
核对全部持久化证据后恢复，冲突请求仍拒绝。

隔离迁移 `episode_store/0009`、`0010` 添加数据库约束：

- 完整 data reference 必须等于已提交标准包回执，并属于指定集合及对应录制切片。
- 同作用域、同来源的 PASS QC、TAGGING 任务、详情投影和每路相机预览必须存在。
- READY 公开文档与数据库列、已确认切片窗口必须一致；不得改为伪造 Lance 绑定，
  也不得修改已完成绑定、窗口或 QC／标注证据。
- 租户／项目／地域上下文必须匹配；不能仅凭已知数据版本 ID 写入 READY。

## 已执行证据

1. **1 项实际服务测试通过**：真实 PG／MinIO 完整 RGB-D 包提交、集合、Tag 任务、
   详情投影和官方 RealSense SDK 预览。拒绝缺失 QC／预览、错误集合、任务、QC、
   相机数量、伪造哈希及跨作用域 SQL；三个同步首次 READY 提交取得同一回执，
   响应重试恢复成功，Lance reservation 数为零。
2. **32 项回归通过**：连续录制、Raw 准备、时间网格及工作流基础契约。
3. **7 项 Temporal 回归通过**：测试服务器上的现有流程／重放测试，活动使用各
   用例的测试实现；不是连续录制三入口真实服务贯通证据。
4. **13 项前端测试通过**：P23 新集合／历史版本跳转、切片编辑、模型和媒体适配。
   网关响应为测试替身，尚未进行此阶段的 1440／1024 真实浏览器验收。
5. TypeScript、生成契约检查、Ruff 通过。运行时 OpenAPI SHA-256：
   `f2404c8f082333c76c7fd64937c795bc34b5a10ea3895c7c9924d2bd25a8e507`。
   迁移 **143/143 current**，无 checksum drift；隔离 API／worker 健康。

证据：[实际回执](continuous-ready.json)、[服务 XML](continuous-ready-services.xml)、
[回归 XML](continuous-binding-regression.xml)、[Temporal XML](continuous-binding-temporal.xml)、
[前端 XML](continuous-ready-ui.xml)。初次重复执行发现测试 QC ID 的全局唯一冲突，
已改为独立 ID；前端测试补齐已确认切片数据后通过。已有 Starlette/httpx 及 AntD
弃用提示不属于此阶段功能失败。

```bash
bash deploy/chayi/compose.sh run --rm test \
  tests/episode_store/test_continuous_ready_services.py -q
bash deploy/chayi/compose.sh run --rm test \
  tests/continuous_recordings tests/workflow/test_workflow.py -q
bash deploy/chayi/compose.sh run --rm test tests/workflow/test_temporal_workflows.py -q
bash deploy/chayi/compose.sh run --rm frontend-test vitest run src/pages/p23-recording-segmentation
```

实际服务输入是 4 帧 16×16 合成 RGB-D，录制、切片与 QC 来源事实由测试明确
预置，没有发送采集 outbox。它验证状态发布边界，**不代表上传、人工切片、QC
计算、窗口生成或完整连续录制入口已接通**。连续 resolver／alignment／Temporal
仍需接入新包计划；MCAP 及其余 G1 条件也未完成。17 项 P0 均仍 Pending。

## G2 与回退条件

G2 尚未执行。未来候选必须包含上述扩展迁移、支持 data reference 的 API／worker
和对应前端类型，并先完成连续录制新包派发及其余 G1 验收。当前服务的全局入口
没有因状态模型兼容而自动切到新包，不能把此提交单独作为部署完成。

回退必须保留已提交包、集合、标注和原录制；新 READY 数据存在后，只能使用能
读取该绑定的兼容版本。不能回退数据库字段或把 package READY 转成 Lance。
本阶段未改 8088 业务数据、未删除旧存储、未启动机器人运动或新采集。
