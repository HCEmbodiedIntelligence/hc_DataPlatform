# Tag 子 Episode 产品登记：阶段证据

在生成层提交 `93034838` 之后，增加 `project_tag_episode`。这是一条内部 worker
登记能力，使用真实 PostgreSQL/MinIO；尚未连接 Tag 派生持久任务、HTTP 命令和
P08 按钮。不是 Tag 转 Episode 全流程验收，也未执行 G2/G3。

## 行为

- 绑定显式父集合版本、父数据版本和固定 Tag 派生计划；校验新包已提交、内容
  哈希、帧数、来源、schema 与编码计划。不会用最新父版本替换指定版本。
- 新建真实独立 Episode 的产品快照和数值/RGB/深度 stream binding；沿用现有
  P05/P06 读取结构，使用真实 collection/data version，不填写 Lance 版本。
  条目 change_type 为 DERIVED；父条目和旧产品快照保留。
- 只使用父产品已记录的采集任务/机器人/原始来源。缺少来源或匹配有歧义时拒绝
  登记；MCAP session、连续录制 upload、原生 LeRobot Raw upload 使用各自已记录
  字段匹配。原始来源收据保留，不增加原录制容量；条目 manifest 记新包实际大小。
- 区间开始使用父选中帧的精确纳秒值，内部结束使用下一父帧时间，末尾使用父
  产品已记录的结束时间。直接绑定已提交的帧区间，不用 ceil(时长×fps) 反算帧数。
- 产品事务共用数据集锁，失败全量回滚，并发/丢失响应后可幂等重试。只允许同
  租户/项目/地域的 service context；新数据包自身授权不放宽。
- 子条目没有父人工审核和审批。已验证现有自动标注服务能对新数据创建独立
  revision 0、空标注和新任务；此调用仍待与派生任务消费者编排。
  子包自动 QC 仍 UNKNOWN，需要接通独立执行，不能继承父 PASS。

## 已运行检查

- [4 项新测试通过](tag-projection-tests.xml)：两个已有 Tag 和 [2,4) 尾段的产品
  登记、事务故障、三路并发重试、旧版本与旧标注保留、新标注任务、Raw 容量不
  重复计数；错误哈希/来源/计划、跨租户/项目/地域和普通请求 context 拒绝。
- 尾段明确验证：区间包含 2 帧，但由于纳秒取整，ceil(duration×30/1e9) 得 3。
  登记保留真实 2 帧，不改时间、不增加重复帧。见 [实际结果](tag-projection-results.json)。
- [4 项回归通过](tag-projection-regression.xml)：既有 RGB-D MCAP/连续录制产品
  登记、旧 Lance 产品累计快照与数值通道。已设置隔离 HC_TEST_POSTGRES_DSN，
  最终使用 fail-on-skipped / fail-on-xfailed，无跳过。
- 变更文件 Ruff/格式及 diff 检查通过。一个既有 Starlette/httpx 弃用警告。
  未新增数据库迁移，隔离 API/worker 运行健康；未操作现网 8088、旧存储或机器人。

复现：

```bash
bash deploy/chayi/compose.sh run --rm test tests/episode_store/test_tag_projection_services.py --fail-on-skipped --fail-on-xfailed -q
bash deploy/chayi/compose.sh run --rm -e HC_TEST_POSTGRES_DSN=postgresql://hc:hc@postgres:5432/hc_data test tests/episode_store/test_product_projection_services.py tests/dataset_registry/test_ingest_projection_postgres.py --fail-on-skipped --fail-on-xfailed -q
```

输入仍为合成软件夹具。HTTP/常驻 worker 的派生任务、取消/重试授权、P08 创建
入口、独立 QC、1440/1024 真浏览器、实际录制与真人条件仍待验证。此阶段不新作
离线模型训练支持声明。G1 剩余功能、W7/W8 与最终可部署候选继续。
