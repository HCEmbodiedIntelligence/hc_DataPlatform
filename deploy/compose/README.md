# Compose 配置说明

日常开发使用根目录的 `compose.dev.yaml`，无需叠加本目录的测试配置。
不需要源码热更新时，使用根目录的 `compose.single-server.yaml`，配置方式见
[单机部署说明](../../docs/single-server-storage.md)。

本目录集中存放测试、验收、演练配置，以及 Nginx、MinIO CORS 和测试服务等配套资源。
以下路径和命令均从仓库根目录执行。

| 文件 | 用途 | 使用方式 |
| --- | --- | --- |
| `compose.test.yaml` | 隔离的集成测试环境，包含数据库、对象存储、API、Worker 和测试执行器 | 独立使用 |
| `compose.real-api.yaml` | 真实 API 自动验收，设置测试认证和精确任务范围 | 叠加根目录 `compose.dev.yaml` |
| `compose.minio-test.yaml` | 双环境迁移和故障注入演练的 MinIO 配置 | 叠加根目录 `compose.dev.yaml` |
| `compose.auto-annotation-e2e.yaml` | 自动标注验收，提供确定性 HTTP 标注服务 | 叠加开发入口和 `compose.real-api.yaml` |
| `compose.p15-search-e2e.yaml` | 搜索页面验收，调整网关端口并取消 MinIO API 的宿主端口映射 | 叠加 `compose.test.yaml` |
| `compose.worker-demo.yaml` | Worker 演示验证，限定 demo 范围并使用正式解码配置 | 叠加开发入口和 `compose.real-api.yaml` |

## 路径和组合规则

`compose.test.yaml` 是独立入口，构建目录和挂载路径以本目录为基准，通过 `../../` 引用仓库文件。
其余文件是补充配置；Compose 按第一个 `-f` 指定文件的目录解析相对路径，因此必须按下面的顺序组合。

```bash
# 独立测试环境：仅验证配置，不启动服务
docker compose -f deploy/compose/compose.test.yaml config --quiet

# 真实 API 验收
docker compose -f compose.dev.yaml \
  -f deploy/compose/compose.real-api.yaml config --quiet

# 自动标注验收
docker compose -f compose.dev.yaml \
  -f deploy/compose/compose.real-api.yaml \
  -f deploy/compose/compose.auto-annotation-e2e.yaml config --quiet

# 搜索页面验收
docker compose -f deploy/compose/compose.test.yaml \
  -f deploy/compose/compose.p15-search-e2e.yaml config --quiet

# Worker 演示验证
docker compose -f compose.dev.yaml \
  -f deploy/compose/compose.real-api.yaml \
  -f deploy/compose/compose.worker-demo.yaml config --quiet

# 迁移演练
docker compose -f compose.dev.yaml \
  -f deploy/compose/compose.minio-test.yaml config --quiet
```

这些命令只检查配置；完整测试入口在 `scripts/first_wave_gate.py`。
真实 API 验收需设置 `HC_REAL_API_E2E_RUN_ID`；Worker 演示需设置对应的 `HC_HF_*` 范围变量，见
[演示说明](../../backend/tests/system/hf_humanoid_demo/README.md)。
迁移演练通过独立项目名和环境文件隔离，操作说明见[部署文档](../README.md#同机双-compose-演练)。

开发、单机部署和部分测试配置默认使用相同的宿主端口；验收配置需按各自说明设置项目名、端口和数据隔离。
