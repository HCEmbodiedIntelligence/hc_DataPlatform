# BE22 第二拨真实主链 fixture

本目录只用于 `HC_ENVIRONMENT=test`。它不创建生产种子账户，不修改认证或业务实现。

## 已准备内容

- `data/catalog.json`：合法、坏 Manifest、重复包、缺帧、缺 Topic、多相机六类最小包；每个
  payload 和 Manifest 都记录字节大小与 SHA-256。
- `fixture.py`：每次运行的独立 project、foreign project、region、账号名与 Raw bucket prefix；
  artifact 只保留资源 ID、request ID、审计 ID 和清理结果，并脱敏 token、密码、签名 URL。
- `cleanup.py`：只允许 `be22-<run-id>-*` namespace，且必须同时设置 test profile、cleanup
  开关和数据库名确认；PostgreSQL 与对象前缀删除均可重复。
- `orchestrator.py`：逐阶段记录 PASS/FAIL/NOT RUN，失败后后续阶段不会伪装执行，finally
  始终尝试清理。
- `http_adapter.py`：真实网络调用；注册两个空账户、审批 membership/capability、IDOR 负测、
  P20 幂等创建、Manifest 预检、multipart 上传和重复提交。Runner 只从 commit 响应和
  Worker result 读取已持久化 workflow/annotation locator；正式自动链未启动时会停在
  准确阶段并记录 NOT RUN，不使用 test-only DB provisioner 伪造成功。

## 验证 fixture（不需要外部服务）

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 backend/.venv/bin/pytest -q \
  --import-mode=importlib -p pytest_asyncio.plugin \
  backend/tests/system/test_wave2_fixture_contracts.py
```

重新生成二进制后，上述测试必须证明工作树内结果逐字节相同：

```bash
PYTHONPATH=backend/src python3 backend/tests/system/wave2/generate_packages.py
```

## 真实网络运行（当前依赖未闭合，不能记 PASS）

API、PostgreSQL、MinIO 与 Worker 都必须是一次性 test profile。JWT key 必须同时配置给 test API
的 `HC_JWT_SIGNING_KEY`（算法 `HS256`）和 runner 的 `HC_WAVE2_JWT_SIGNING_KEY`；禁止复用生产
密钥。示例仅列变量名，不提供默认账户或密码：

```bash
HC_ENVIRONMENT=test \
HC_WAVE2_CLEANUP_ENABLED=1 \
HC_WAVE2_TEST_DATABASE_ACK='<test database name>' \
HC_WAVE2_JWT_SIGNING_KEY='<ephemeral test key>' \
HC_TEST_POSTGRES_DSN='<test postgres dsn>' \
HC_MINIO_ENDPOINT='<test minio endpoint>' \
HC_MINIO_BUCKET='<isolated test bucket>' \
HC_MINIO_ACCESS_KEY='<test access key>' \
HC_MINIO_SECRET_KEY='<test secret key>' \
PYTHONPATH=backend/src:backend backend/.venv/bin/python \
  -m tests.system.wave2.run_main_chain --run-id manual-01
```

当前 runtime OpenAPI 没有启动 ingest Worker 的公开命令，也没有 annotation task 创建命令；
runner 会在相应阶段写 `NOT RUN` 并非 PASS。完整浏览器主链继续由
`frontend/e2e/real-api/main-chain.spec.ts` 保持 NOT RUN，待 FE 第二拨完成后由 BE24/FE10 执行。
