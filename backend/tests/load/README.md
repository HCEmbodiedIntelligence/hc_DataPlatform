# BE-12 容量与负载验证

负载测试包将元数据/控制面验证与真实数据面吞吐量分开。它绝不会根据稀疏文件或内存替身推断
系统能够达到每天 5 TB 的处理能力。

1. 校验逻辑大小为 20 GiB 的夹具，无需实际分配 20 GiB 空间或计算其哈希：

   ```bash
   PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q tests/load/test_capacity_controls.py
   ```

2. 在进程内使用 50 个并发的 20 GiB 清单，测试真实的 FastAPI 请求校验、认证依赖、路由和
   上传服务（不传输正文，也不使用网络）：

   ```bash
   python tests/load/inprocess_http_probe.py --concurrency 50 \
     --rollout-size 21474836480
   ```

3. 针对已部署的试运行 API，并发创建 50 个会话，每个会话的清单描述一个 20 GiB rollout。
   如果试运行环境要求凭据，请通过环境变量提供；令牌绝不会写入结果：

   ```bash
   export BE12_PILOT_TOKEN='<short-lived token>'
   python tests/load/upload_control_plane.py --base-url https://pilot-api.example.com \
     --concurrency 50 --rollout-size 21474836480 --bearer-token-env BE12_PILOT_TOKEN
   ```

4. 测量有界的本地顺序写入/读取/SHA 基线，并保留 JSON 证据：

   ```bash
   python tests/load/capacity_probe.py --size-bytes 536870912 --samples 7 \
     --output tests/load/results/local-capacity.json
   ```

   可以使用 `minio_throughput_probe.py` 单独测量一次性 MinIO 环境；凭据只从具名环境变量读取。
   这仍然只是阶段基线，不能提升为端到端结论。

   ```bash
   python tests/load/minio_throughput_probe.py --endpoint http://127.0.0.1:59300 \
     --bucket be12-capacity --access-key-env BE12_MINIO_ACCESS_KEY \
     --secret-key-env BE12_MINIO_SECRET_KEY --size-bytes 268435456 --samples 7
   ```

5. 最终判定能否达到每天 5 TB，还必须使用与试运行环境完全一致的 OSS/MinIO、PostgreSQL、
   Temporal、Lance、FFmpeg 和 LeRobot 部署。测试至少运行 30 分钟，记录每个阶段的
   P50/P95/P99，验证资源饱和度与错误/重试率，并采用包含 30% 余量的阈值
   75,231,481.48 字节/秒（每天 5 TB，按十进制计算）。

2026-08-14 的一次性 kind 运行成功访问了已部署 API，但由于生产用 `AuthContext` 中间件缺失，
全部 50 个请求都返回 401。详情参见 `CAPACITY-REPORT.md`；认证拒绝延迟不能算作成功的负载测试结果。
