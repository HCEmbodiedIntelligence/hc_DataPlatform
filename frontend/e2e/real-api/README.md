# Real API E2E（第二波预留）

本目录只定义最终真实主链的顺序和 artifact 位置，不声明主链已实现。当前 spec 默认报告
`skipped`，验收矩阵将其记为 `NOT RUN`；自定义 reporter 会让命令返回非零，发布门禁不会把该
skip 当 PASS。

启用前必须同时具备：

- runtime OpenAPI 中的直接注册空账户、项目加入/权限申请及审批合同；
- P20 create/close、上传/Manifest/QC、多相机标注、多级 Tag 审核合同；
- 两个隔离测试身份、隔离项目、MinIO 测试桶和可清理的真实数据 fixture；
- `VITE_MOCK_MODE=off`，网络断言确认没有 Service Worker/MSW 响应；
- 每个异步阶段有有界轮询、失败诊断和 trace。

依赖满足且 spec 中的显式 Wave 2 failure 被真实操作替换后，才可设置：

```bash
HC_REAL_API_E2E_ENABLED=1 \
HC_REAL_API_E2E_BASE_URL=http://127.0.0.1:8088 \
pnpm --dir frontend exec playwright test --config playwright.real-api.config.ts
```

JUnit：`artifacts/test-gates/latest/real-api-e2e.xml`；trace/screenshot/video：
`artifacts/test-gates/latest/playwright/`。

BE22 已在 `backend/tests/system/wave2/` 提供确定性数据包、双身份隔离 namespace、逐阶段 API
runner、脱敏 artifact 和幂等清理。该 runner 的 API/Worker 结果不能替代本目录的完整浏览器主链；
本 spec 在页面和正式 provision/launcher 依赖完成前继续保持 `NOT RUN`。
