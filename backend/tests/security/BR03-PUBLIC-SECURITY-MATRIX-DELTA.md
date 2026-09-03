# BR03 公网安全矩阵增量

审计日期：2026-08-18。本文只记录 PD-BR-10～11 的增量，不覆盖
`BE23-PUBLIC-SECURITY-MATRIX.md` 历史快照，也不更新共享
`ACCEPTANCE-MATRIX.md`。

| 门禁 | BR03 当前证据 | 增量状态 | owner / 依赖 / 下一步 |
| --- | --- | --- | --- |
| G-HTTP500 | core 27 PASS；公网错误定向 6 PASS，覆盖 HTTPException 500、ProblemException 503、unexpected exception、非标准 599、validation、header、日志和 request id | PASS（BR03 范围） | BE24 将 health/Problem Details runtime 变化合入正式 fragment 和生成合同后重跑 drift。 |
| G-ARTIFACT | 历史 JUnit 保留 76 testcase/1 failure；原地脱敏审计记录前后 SHA-256、类别计数、工具版本且无明文副本；最终扫描 51 文件、0 finding | PASS（当前 artifact 集） | BE24 必须沿用 gate 后置 sanitizer + scanner，不得用新跑结果删除历史失败结构或审计侧车。 |
| 生产 docs/OpenAPI | production/test 默认关闭；test 只有显式开关才开放；staging/production 配置试图启用会 fail closed；内部 `app.openapi()` 仍生成 85 paths | PASS（代码/配置） | 真实生产管理入口未实现；如未来需要开放，由 platform security 另建认证入口，不能打开公网开关。 |
| 公网 `/metrics` | Compose 公网 listener 精确 404；Helm Ingress 精确映射到无端点 deny Service；内部 ClusterIP metrics Service 指向 API；NetworkPolicy 只允许选定 monitoring namespace/pod | PASS（渲染配置） | platform/deploy 在目标 CNI 和 Ingress 上做真实公网负测及 Prometheus 正测；BE24 在共享 Compose 中复核内部路径。 |
| metrics/readiness 最小披露 | exposition 丢弃 principal/project/region/resource/workflow 等 scope/high-cardinality label 样本并保留低基数运行时指标；readiness 仅返回 ready 或安全 Problem Details | PASS（进程级测试） | observability owner 后续如需领域聚合指标，应建立低基数聚合 emitter，不能恢复 scope label。 |
| G-RATE | BR03 未增加未确认阈值；生产 no-op/网关分布式限流缺口仍在 | FAIL | security/platform；依赖 OPEN-02 数值和部署拓扑；实现后做 burst、slow-client、per-principal 真实门禁。 |
| G-SESSION | BR03 未写死 TTL/并发 session；原缺口保持 | FAIL | security/product；确认 TTL、并发上限和全局撤销后新增 migration/实现，由 BE24 串行合 manifest。 |
| G-AUTHZ / G-SCOPE | 通用成对测试仍不能证明每一条 route 和 Repository | PARTIAL | 各领域 owner 补逐路由同 capability/跨项目同 ID 与 PostgreSQL exact-scope pair。 |
| G-AUDIT | BR03 只保证错误安全日志；领域允许/拒绝/危险动作的统一审计目录未闭合 | PARTIAL | 各领域 owner 定义 allowlist 事件并验证持久化、读取权限和保留策略。 |
| G-IDEMP / G-PAGE | BR03 未改变领域幂等、并发、分页合同 | PARTIAL | 各领域 owner 补 body reuse/concurrent winner/stale revision、稳定 cursor/limit 与跨 scope 负测。 |
| G-CORS | 同源 fail-closed 证据保留；跨域拓扑和 allowlist 未确认 | PARTIAL | deploy/security 在拓扑确认后配置并实测，不从当前同源测试推导跨域 PASS。 |
| 5 TB/day + 30% | 5 个容量真实性测试 PASS；严格 reporter 对 1 个容量 XFAIL 返回退出码 1 | XFAIL / 发布阻断 | capacity/platform；保持 75.23 MB/s 口径，需 production-like 30 分钟全链路证据。 |

只要上述 `FAIL`、`PARTIAL` 或 `XFAIL` 仍存在，公网安全和生产发布整体不得标为
PASS。
