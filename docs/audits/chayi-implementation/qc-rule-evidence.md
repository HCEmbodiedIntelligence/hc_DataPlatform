# G1 固定 Episode 的自动 QC 执行证据（2026-09-29）

本阶段在 `feat/chayi-g1`、`hc-chayi-g1` 隔离环境补齐 W4/W5 的逐规则证据。
保留原 PASS/RISK/REJECT 总判定，新增执行证据、固定版本查询和 P06 展示。
这是软件阶段提交；所有 17 项 P0 仍为 Pending，不宣称 QC-01 或整个 G1 已验收。

## 实现与版本边界

- 不改已存在的 `qc-report/v1`、QualityProfile 或策略阈值，也不重写历史报告哈希。
  新 `hc-qc-rule-evidence/1` 在实际评估期间收集观测数量、阈值、原始 detector
  findings、参与总判定的 finding 索引、原因、完整 profile、引擎、源窗口和源哈希。
  流式图像只保留每通道计数和前一指纹，不在收集器中保存所有帧。
- 每条规则按通道独立标记 pass/warn/fail/skip/info。pass 要求确实执行、观测完整、
  参与当前策略且没有 finding。缺输入、缺阈值、不足两帧、历史缺证据各有原因。
  当前 v2 策略排除的检测结果以 info 保留，不擅自改变原有业务判定。
  默认值 `complete_step_ratio=1` 不构成实测证明；未显式测量时返回 skip。
  深度解码确认只能证明解码，不能据此宣称 RGB 黑帧/重复帧已评估。
- MCAP、原生 LeRobot、连续录制在新包路径记录此证据；拒绝处理时也先保存作用域内
  不可变数据库 receipt。新包写入 `hc/quality_rules.json`，与 quality.json、manifest
  的源、rollout、窗口、报告哈希和 profile 一致。writer 和提交校验分别验证绑定。
  老 Lance 路径及历史无 sidecar 的包仍可读；历史未记录的规则显示未验明。
- 新迁移 `episode_store/0012_qc_rule_evidence.sql` 包含项目 RLS、完整组织/项目/区域
  检查和不可变触发器。相同 receipt 重试幂等；禁止 UPDATE/DELETE。证据变动进入
  新包配置哈希，因此产生新数据版本，不覆盖原包。连续录制外层 QC 文档增加证据后
  产生新外层报告 ID；其中原始 `qc-report/v1` 内容和判定保持不变。
- `GET .../episodes/{episode_id}/data-versions/{data_version_id}/quality` 读取已提交的
  固定版本，经原有 dataset_version.read 和完整租户边界授权，返回 no-store。
  状态区分 AVAILABLE / LEGACY_MISSING / REPORT_MISSING；伪造或不一致证据拒绝。
- P06 原“数据信息”抽屉新增自动质检卡片，与人工数据审核分开显示。总判定与逐规则
  统计、缺失原因、观测和阈值分别可见，保留原播放器和问题记录交互。
  正式/runtime OpenAPI 和生成客户端同步更新。

## 实际运行结果

1. [后端回归 XML](qc-rule-backend-tests.xml)：78 项通过。包含旧报告字节/哈希保持、
   raw detector 与策略判定区分、缺输入/部分观测/深度解码、来源替换拒绝、迁移及旧
   连续录制回归。新增跨 PYTHONHASHSEED 进程的 profile 集合排序检查。
2. [入口服务 XML](qc-rule-ingress-tests.xml)：8 项通过，包含连续录制直接处理与
   Temporal 中断/重试、原生成功/缺掩码/profile 不匹配/帧数不匹配、两个 MCAP 场景。
   使用真实隔离 PostgreSQL、MinIO、Temporal；继续验证旧 Lance 写入被禁止、来源
   约束、深度复用和 READY 链路，新增固定包 QC 文件绑定断言。
3. [HTTP 证据](qc-rules-http.json)：上述 78 项中包含普通账号真实注册/登录，经
   gateway → API → PostgreSQL 读取两个固定数据版本。验证新证据、旧报告不伪造
   pass、401/跨组织403/跨区域404及不可变数据库 receipt。初始测试成员和权限用
   隔离 DB 设置，无认证依赖替换；结束注销会话。
4. [前端 XML](qc-rule-frontend-tests.xml)：12 项通过，包含新查询/卡片和原 Viewer。
   这是带替身的组件/协议测试，不计作 1440/1024 真实浏览器或真人验收。
5. TypeScript、ESLint、Prettier、Ruff、runtime 契约及生成客户端检查通过。
   Runtime SHA-256：`fb30fc9dcdf9aad4f6e88caa366d995fa445336c61da9453c83589cc0755e6ce`。
   隔离迁移 146/146，missing/unknown/checksum drift 均为空。

[三入口文件证据](qc-rule-entrances.json) 直接读取上述服务测试输出，核验 QC 文件大小、
哈希、manifest 与报告绑定。当前样本的结果如下；规则按通道展开，行数不等于规则种类数。

| 入口 | 帧数 | 总判定 | pass | info | skip |
| --- | ---: | --- | ---: | ---: | ---: |
| MCAP | 30 | PASS | 28 | 12 | 11 |
| 连续录制 | 6 | PASS | 58 | 20 | 17 |
| 原生 LeRobot | 6 | PASS | 34 | 14 | 7 |

所有输入均为明确标识的软件夹具，未新增真实采集、真人审核或模型训练证据。本阶段
未重跑官方离线 reader；先前对应入口的 reader 证据见各入口报告，不能冒充本次执行。
深度展示继续直接调用官方 RealSense SDK，见 [显示证据](realsense-preview.md)。

初次验证发现并修复：必需主题 frozenset 经 JSON 往返后的顺序可能变化，导致连续
录制内嵌证据与 sidecar 被误判不同。新证据 profile 使用既有规范化顺序，比较双方
已校验的模型，并通过跨进程测试及最终三入口重跑。另有两个测试环境问题：常驻 worker
抢先消费故障夹具、旧原生测试依赖多余数据库环境变量；分别通过停用隔离常驻消费者
和使用显式 G1 DSN 解决。最终 XML 为修复后完整结果，测试结束已恢复常驻 worker。

## 复现与将来 G2 条件

故障测试需要临时停止 `hc-chayi-g1` 的常驻 worker，避免它消费同一测试 outbox。
只操作隔离项目；命令结束后恢复 worker 并检查健康状态。

```bash
bash deploy/chayi/compose.sh run --rm test tests/quality/test_rule_evidence.py tests/quality/test_quality.py tests/quality/test_policy.py tests/quality/test_contracts.py tests/episode_store/test_quality_evidence_services.py tests/core/test_migrations.py tests/continuous_recordings/test_continuous_episode_processing.py -q --junitxml=.g1-artifacts/qc-backend.xml
bash deploy/chayi/compose.sh stop worker
bash deploy/chayi/compose.sh run --rm -e OPENARM_REAL_INFRA=1 test tests/episode_store/test_recording_package_services.py tests/lerobot_imports/test_native_package_services.py tests/episode_store/test_mcap_package_services.py -q --junitxml=.g1-artifacts/qc-ingress.xml
bash deploy/chayi/compose.sh start worker
bash deploy/chayi/compose.sh run --rm --entrypoint python test tools/capture_qc_rule_evidence.py --root /app/.g1-artifacts --output /app/.g1-artifacts/qc-rule-entrances.json
bash deploy/chayi/compose.sh run --rm frontend-test vitest run src/pages/p06-dataset-detail/episode-quality.test.ts src/pages/p06-dataset-detail/EpisodeQualityPanel.test.tsx src/pages/p06-dataset-detail/ViewerShell.test.tsx
```

未来经 G2 授权后，先应用 additive expand 0012，再部署匹配 API/worker/前端。
回退应用前应排空/暂停新版本处理，保留新增表、证据和包；旧程序不展示这些证据，
恢复候选版本可继续读取。不能将旧报告“升级”为已运行的新证据，也不做删除式回退。
历史已经 READY 的包不重新转换；明确重新处理时才生成新版本。

本阶段没有执行 G2/G3、操作 8088 业务数据、删除旧存储或启动机器人。
统一聚合详情、标签区间新 Episode、物理像素交互、完整包通用打包任务、批量 RGB-D、
W7 隔离迁移/回退、真实浏览器和真实数据/真人条件继续，17 项 P0 保持待验收。
