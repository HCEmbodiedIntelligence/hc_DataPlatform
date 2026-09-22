# D 机器人结果 API

以下路由由 C 提供，需 E 将 `processing_api.router` 挂到 API 后生效。所有请求只用所属机器人凭据；不使用用户 JWT 或平台管理员账号。不需要组织/项目/Region 请求头，归属从 upload 解析。响应 `Cache-Control: no-store`。

```bash
# BASE_URL 使用平台公开地址；TOKEN 从安全凭据存储读取。
curl -H "Authorization: Bearer $TOKEN" \
  "$BASE_URL/api/v1/robot-ingest/uploads/$UPLOAD_ID/processing"
```

成功结构严格匹配 G0 proposed schema；它现在是 C 实现的接口结构，仍待 E 产品装配。

```json
{
  "schema_version": "openarm-processing-result/v1",
  "upload_id": "riu-example",
  "raw_source_id": "raw-example",
  "processing_status": "PARTIALLY_FAILED",
  "quality_status": "PASS",
  "terminal": true,
  "poll_after_seconds": 0,
  "episodes": [
    {"source_episode_id":"source-0","source_episode_index":0,
     "episode_id":"rie-example-0","dataset_id":"dataset_example",
     "status":"READY","quality_status":"PASS","error_code":null,
     "retryable":false,"next_action":"open_episode"},
    {"source_episode_id":"source-1","source_episode_index":1,
     "episode_id":"rie-example-1","dataset_id":"dataset_example",
     "status":"FAILED","quality_status":"PENDING",
     "error_code":"ROBOT_PROCESSING_TECHNICAL_FAILURE",
     "retryable":true,"next_action":"retry_processing"}
  ]
}
```

上述示例用于客户端开发，不是 OpenArm 成功链路回执。真实本轮回执见 `evidence/real-infrastructure.json`：FAILED、episodes=[]、B 依赖未交付。

| upload/processing/QC | D 展示与轮询 |
| --- | --- |
| COMMITTED / PENDING | Raw 已上传，5 秒轮询 |
| COMMITTED / DISCOVERING_EPISODES、PROCESSING | 处理中，5 秒轮询；即使已出现一段失败，剩余段仍在运行就不提前终止 |
| COMMITTED / READY / PASS | 可进入后续业务，停止轮询；不代表人工审核/发布 |
| COMMITTED / READY / RISK | 质量风险，人工处置，停止轮询 |
| COMMITTED / READY / REJECT | 质量拒绝，审核/补采，停止轮询 |
| COMMITTED / READY / PENDING | 结果不完整，交平台排查，不显示 ready |
| COMMITTED / FAILED、PARTIALLY_FAILED | 处理失败，停止轮询；成功 episode 仍可定位 |

只有 `terminal=false` 才按 `poll_after_seconds=5` 继续轮询；所有处理终态返回 0。`retryable=true` 是**允许显式重试处理**，不是让 D 自动无限轮询/重传。非重试失败 next_action=repair_export；质量终态=review_quality；成功=open_episode。任何 episode_id 都不应仅凭非空就认为可用，先看 status/QC。

发现阶段失败可能尚无源 episode；G0 schema 不容许顶层 error_code，因此查询附加诊断：

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "$BASE_URL/api/v1/robot-ingest/uploads/$UPLOAD_ID/processing/diagnostics"
```

```json
{"upload_id":"riu-example","workflow_id":"robot-processing-example","generation":0,
 "error_code":"ROBOT_PROCESSOR_UNAVAILABLE","retryable":true,"next_action":"retry_processing"}
```

机器人显式重试/历史恢复：请求前持久化 `request_id` UUID，同一操作响应丢失仍用原 UUID。先对账，**不再上传字节、不创建第二份 Raw**。

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"request_id":"ae7dd03d-9aa5-463d-b19b-3e1962144bdf"}' \
  "$BASE_URL/api/v1/robot-ingest/uploads/$UPLOAD_ID:retry-processing"
```

200 返回当前 processing 结构；重复 request_id 即使上一轮已结束也不启动新轮。新 UUID 仅在终态且有可重试失败时可用；只重置可重试的失败段，成功/质量终态/不可重试段保持。

| HTTP / code | 处置 |
| --- | --- |
| 401 / ROBOT_CREDENTIAL_* | 更新机器人凭据 |
| 404 / ROBOT_INGEST_UPLOAD_NOT_FOUND | 不存在或非该机器人所有，不能越权读取 |
| 409 / ROBOT_UPLOAD_NOT_COMMITTED | 先完成上传协议 |
| 409 / ROBOT_PROCESSING_INCOMPATIBLE | 当前只支持完整的 PRESEGMENTED LEROBOT_V3 / v3.0 |
| 409 / ROBOT_PROCESSING_NOT_SCHEDULED | 历史 Raw 无 C 调度记录，显式 retry-processing；原 GET upload 仍保留历史事实 |
| 409 / ROBOT_PROCESSING_RETRY_UNAVAILABLE | 当前正在处理、已成功/质量终态，或没有可重试失败 |
| 409 / ROBOT_PROCESSING_LEGACY_REVIEW_REQUIRED | 历史源已有处理事实，交 E 对账，不能自动覆盖 |
| 503 / ROBOT_PROCESSING_NOT_CONFIGURED | E 尚未装配持久结果存储 |

处理错误码还有 ROBOT_SOURCE_ASSET_MISSING、ROBOT_SOURCE_ASSET_CHANGED、ROBOT_SOURCE_EPISODES_INVALID、ROBOT_SOURCE_EPISODES_CHANGED（不可重试，repair_export）。OPENARM_VIDEO_MISSING 等 B 格式错误需由 B 的真实解析器输出，C 没有伪造此能力。
