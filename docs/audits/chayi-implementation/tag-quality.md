# Tag 派生 Episode 的独立自动质检

2026-09-30，G1 隔离工作树及服务。此阶段补充 W3/W4，不代表 G1、W8 或任何
完整 P0 已验收。输入仍是合成 RGB-D 录制夹具，没有新增真人或实采验收。

## 代码行为

- 新 Tag 创建计划在既有 `encoding` 中固定 `hc-tag-qc/1`：实际帧率、精确帧数、
  质检 profile、父 profile 哈希和检查时间范围。配置参与数据版本哈希；改变 QC
  参数会产生新 data version，同一个固定 Tag 的逻辑 Episode 身份不变。
- 对选定包的 Parquet 数值、`hc.valid.*`、`hc.sample_valid`、模态时间误差和
  实际解码的视频重新执行检查。检查范围明确为 `selected_aligned_episode`；
  原始录制检查作为父来源附件保留。页面显示这个范围，避免把已对齐网格的
  检查当成重新检查原始传感器采样。原包与审核结果均不修改。
- 仅当逻辑 topic 完全一致时沿用父 profile 的动作阈值、具名关节范围；
  对齐后时序使用 schema 的实际帧率及当前区间帧数。缺少关节名字/限位或
  点云观测仍显示未验明。完整 profile 与输入依据写入包，重试不重选当前默认值。
- RGB 实际解码产生亮度/指纹/解码证据；深度实际解码记录完整性，不把深度码值
  作为 RGB 黑帧或重复帧依据。深度显示仍是官方 RealSense Colorizer；此阶段
  不改变着色、量化、掩码、物理值读取或实际 gray12le profile。
- 保存独立 `hc/quality.json`、`hc/quality_rules.json` 及数据库执行收据，绑定子
  rollout、来源哈希、profile、时间范围和报告哈希。已有八类风险判定策略不变：
  排除于总判定的规则保留 info 与原始发现，未测项保留 skip。因此总 PASS
  不表示每条规则都通过，更不表示人工数据审核、标注审核或训练模型已通过。
- 29.97、30000/1001 等实际分数帧率用有理数计算网格。选定区间的帧数是冻结
  输入，不从舍入后的纳秒时长反算；原始采集调用继续采用时长推算。旧整数
  profile、报告和证据的固定哈希回归未变化。
- 动作、关节与时间误差观测暂存 SQLite，动作相邻比较按迭代器执行，偏差分位数
  在磁盘排序；视频逐帧解码、Parquet 分批读取。没有因此宣称真实大规模性能验收，
  输出发现列表的大小仍取决于实际问题数量。
- 原先没有 quality 配置的冻结计划继续生成原 UNKNOWN 报告，原格式及配置哈希
  形状不变。新流程在提交后保存 QC 收据，即使此步中断也复用已提交主包恢复，
  不重新裁剪、编码或量化。

## 实际验证与限制

- 后端累计 **104 个独立用例的最近执行均通过**。保留首次完整回归的
  [102 项 XML](tag-independent-qc-tests.xml)（仅去除堆栈行尾空白）：98 通过，两个新增测试夹具错误、
  两项因未设置旧测试 DSN 而跳过。修正空数值基础输入并显式连接隔离库后的
  [20 项补跑](tag-qc-recovery-tests.xml)全部通过；另有
  [1 项冻结契约测试](tag-qc-contract-tests.xml)通过。按 classname/name
  取最新结果，104 个唯一用例均无失败、错误或跳过。新增契约单测初次使用不完整
  数据引用被验证拒绝，已改为完整 DataVersionRef 后通过，没有放松生产验证。
- 覆盖父/子/末尾区间、RGB 黑帧数量随区间改变、深度外观规则明确 skip、
  动作跳变与无效动作、精确期望帧数、旧计划重放、同 Episode 新 data version、
  假造帧数拒绝、四路并发、质检收据故障及各提交阶段恢复、撤权与跨租户拒绝。
  测试保留既有 Starlette/httpx 弃用提示。
- [实际 HTTP 收据](tag-qc-http-results.json)：普通会话经隔离网关/API/常驻
  worker 创建独立包，详情有新 QC，独立空标注仍未审核，官方深度 PNG 与米值
  可读，完整 ZIP 每个文件哈希一致。此轮 HTTP 测试属于前述 104 项。
- [独立报告](tag-qc-report.json)及[逐项证据](tag-qc-rules.json)来自该下载包。
  其中 17 种 QC 规则代码不等于实施方案的 17 项 P0，不能互相替代。
- [禁网官方 LeRobot 0.6.1 reader/DataLoader](tag-qc-offline-reader.json)直接读取
  上述实际下载的 3 帧 RGB-D 包，独立应用 HC 掩码，并校验新 QC/profile/配置/
  来源/父 Tag 映射及全部主包哈希；未导入平台 Python 包。官方 reader 对未知
  HC 字段的提示保留。主包 `dv_4918988cf9a09836cdc29e2e8df476f89b26be6d968a4d5e7409d73595a70c5f`，
  manifest SHA-256 `e74e29df970ea2a4665a0080120f0d5287f289b92d052414b03f603030ab86a8`。
- OpenAPI 正式聚合/运行时与生成客户端一致；runtime SHA-256
  `f9119df33557d3458d9a7375256e8359c7b01e945b76c24459f243089e550cd1`。
  数值基础实现阶段提交 `f5a771fd`。本阶段不重复宣称此前 1440/1024 浏览器的
  UNKNOWN 截图已验证新 QC 页面；新 QC 的浏览器实测仍需后续补充。
- [9 项现有前端回归](tag-qc-ui-tests.xml)与 TypeScript 检查通过。类型检查发现
  前端原解析器没有保留报告 profile_id，已补齐真实字段后重新通过；检查范围
  说明据该字段展示。Ruff/格式与 diff 检查通过。

## 复现和未来 G2 约束

完整回归中调用真实 HTTP 的测试需要常驻隔离 worker。确定性故障注入补跑时
暂停该 worker，结束立即恢复；本轮已恢复健康。只操作 `hc-chayi-g1`。

```bash
# quality/test_postgres.py 的旧 DSN 必须显式绑定隔离值；不传入现网配置。
bash deploy/chayi/compose.sh stop worker
bash deploy/chayi/compose.sh run --rm --no-deps --entrypoint sh test -c 'export HC_TEST_POSTGRES_DSN="$HC_G1_TEST_POSTGRES_DSN"; exec python -m pytest tests/quality tests/episode_store/test_derivation_quality.py tests/episode_store/test_tag_derivation_services.py tests/episode_store/test_tag_projection_services.py tests/episode_store/test_tag_jobs_services.py --fail-on-skipped --fail-on-xfailed -q'
bash deploy/chayi/compose.sh up -d --no-deps --wait --wait-timeout 30 worker
bash deploy/chayi/compose.sh run --rm --no-deps test tests/episode_store/test_tag_jobs_http.py --fail-on-skipped --fail-on-xfailed -q
```

本阶段没有新增 SQL 迁移，仍依赖已有 Tag 任务/独立 QC 表的 expand 迁移。
未来 G2 必须配套发布生成器、worker、API 合同和页面；回退旧 worker 前先停止
新派生命令，完成或隔离带 `hc-tag-qc/1` 的未终态任务，保留收据、数据包和标注。
旧 worker 不认识新增编码契约；只接受整数帧率的旧 reader/API 也不能作为
分数帧率 QC 包的完整回退读路径，需保留兼容读取服务。这是部署约束补充，
最终 G2 runbook 和演练仍待完成。未执行 G2/G3、未改 8088 业务数据、未启动运动。

全无效/缺失深度区间、真实录制与规模、真人、全三入口浏览器、来源未记录字段、
W7 迁移/回退演练及 W8 完整证据继续处理。
