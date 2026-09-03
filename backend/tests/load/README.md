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

   对共享测试 bucket，必须提供本轮独有的 `--prefix` 并启用 `--cleanup`。清理只删除该
   prefix 中由本次 probe 写入并已清单核验的对象；不会删除 bucket 或任何其他 namespace。
   默认 multipart 并发为 4；若测试更高并发，必须显式传入 `--max-concurrency`，并在结果中
   与默认基线分开记录。

5. 最终判定能否达到每天 5 TB，还必须使用与试运行环境完全一致的 OSS/MinIO、PostgreSQL、
   Temporal、Lance、FFmpeg 和 LeRobot 部署。测试至少运行 30 分钟，记录每个阶段的
   P50/P95/P99，验证资源饱和度与错误/重试率，并采用包含 30% 余量的阈值
   75,231,481.48 字节/秒（每天 5 TB，按十进制计算）。

   发布门禁只接受 `hc-capacity-evidence/v1` 的严格 artifact。它必须同时包含：生产或生产近似
   deployment ID、Mock-off 起止时间与至少 1,800 秒持续时间；对象大小分布、并发数、完成
   rollout/字节数；端到端最低吞吐和 P50/P95/P99；API、PostgreSQL、对象存储、Temporal、QC、
   Lance、预览和导出八阶段延迟/错误/重试；七类资源的 CPU/内存/磁盘/网络 P95；对象存储中断
   与 Worker 重启恢复；零重复副作用和精确清理。端到端最低吞吐必须达到阈值，错误率不得超过
   1%，重试率不得超过 5%，任一资源 P95 不得达到 95%。未知/缺失字段、短时运行、本地环境、
   残留对象或仅写 `PASS` 的 JSON 都会被拒绝。

   在把生产采集结果放入 `tests/load/results/` 前先执行：

   ```bash
   python tests/load/validate_capacity_evidence.py /path/to/candidate.json
   ```

   `test_release_gate_has_measured_end_to_end_capacity_pass` 会用同一个验证器复核所有声称 `PASS`
   的结果；格式不完整的 PASS 是普通测试失败，没有合格结果时继续保持发布阻断 XFAIL。

2026-08-14 的一次性 kind 运行成功访问了已部署 API，但由于生产用 `AuthContext` 中间件缺失，
全部 50 个请求都返回 401。详情参见 `CAPACITY-REPORT.md`；认证拒绝延迟不能算作成功的负载测试结果。
