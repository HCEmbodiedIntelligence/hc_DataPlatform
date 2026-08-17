# 可观测性验收资源

- `metrics-contract.yaml` 是跨模块的语义指标与安全日志契约。
- `otel-collector-config.yaml` 接收 OTLP 链路/指标，并公开 Prometheus 指标。
- `grafana-dashboard.json` 提供按项目、资源和工作流划分的数据流水线视图。
- `prometheus-rules.yaml` 包含可执行的告警，每条告警都链接到一份 BE-12 运维手册。

采集器和仪表盘不会凭空生成领域指标。每个具名业务负责人必须在事务成功或失败的边界发送
其契约指标。缺少发送器属于集成失败，无法通过部署配置掩盖。

BE-12 Helm Chart 通过 `opentelemetry-instrument` 启动 API 和 Worker，经由 OTLP
导出链路、指标和日志，并为两个服务分配不同的名称。这只提供框架级钩子和日志关联能力；
它本身不能满足六项领域指标门禁。

一次性 kind 试运行已通过 `tests/system/otlp_capture.py` 验证该传输链路：两个服务名称
均导出了 Span、日志信封和 HTTP 框架指标。接收端有意丢弃了日志正文和属性。脱敏后的摘要位于
`tests/system/results/pilot-otlp-summary.json`；其中不包含六项领域指标中的任何一项。

验证捕获的试运行证据：

```bash
python backend/observability/verify_metrics.py --url http://prometheus-target:9464/metrics
python backend/observability/verify_logs.py --require-context pilot-api.ndjson pilot-worker.ndjson
```

日志捕获结果为空视为失败。日志校验器会拒绝敏感键、形似 Bearer/JWT 的值，以及对象存储
签名查询参数。
