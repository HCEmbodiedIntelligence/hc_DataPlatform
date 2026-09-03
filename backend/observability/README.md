# 可观测性部署与验收资源

- `metrics-contract.yaml` 固定低基数指标和禁用标签；项目、用户、对象、资源、工作流和请求
  标识只能用于受控日志/链路检索，不能成为指标标签。
- `otel-collector-config.yaml` 是中央 OTLP 网关：日志先经固定 JSON 前缀门禁，再通过原生
  OTLP 写入 Loki；指标供 Prometheus 抓取；链路只接收并丢弃，避免未配置后端时泄露正文。
- `otel-agent-config.yaml` 只读取本发布 Frontend Pod 的容器标准输出，使用持久化检查点和
  磁盘队列转发到中央网关。
- `grafana-dashboard.json` 提供低基数运行指标和结构化 Loki 日志面板。
- `prometheus-rules.yaml` 覆盖备份、深校验、节点/版本、迁移、Outbox、Temporal、对象复制、
  审计、HTTP、磁盘、数据库池和告警投递链路。

Helm 在非生产环境可部署 PVC 单体 Loki 做验收；生产环境强制使用外部托管的原生 OTLP
Loki 端点。Collector 固定两副本和 PDB，Frontend 日志代理是 DaemonSet。Grafana 面板以
ConfigMap 交给 sidecar，也可启用受管实例；管理员凭据只能引用既有 Secret。

验证捕获的试运行证据：

```bash
python backend/observability/verify_metrics.py --url http://prometheus-target:9464/metrics
python backend/observability/verify_logs.py --require-context pilot-api.ndjson pilot-worker.ndjson
```

日志捕获结果为空视为失败。日志校验器要求完整 `hc-runtime-log/v1` 固定字段、拒绝未知或
敏感键，以及形似 Bearer/JWT、DSN、PII email 和对象存储签名查询参数的值。

生产响应和端到端验收见 `deploy/runbooks/observability-alerts.md`。
