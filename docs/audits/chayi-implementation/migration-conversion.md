# W7 固定旧版本转换与断点续跑

2026-09-30，G1 隔离 project `hc-chayi-g1`。本阶段补充转换服务，尚不是完整
迁移批次命令或指针回退演练。输入为合成数值/RGB-D 夹具，经实际历史 Lance、
PostgreSQL 和 MinIO 服务；没有迁移 8088 数据。

实现位于 `episode_store/migration_conversion.py`：从 inventory 中明确的旧
Episode 引用冻结计划，重试时复核数据库中该地域的历史绑定、物理 Lance 版本、
Schema、来源哈希及准备好的媒体契约。计划内容变化产生不同新数据版本。
读取每批最多 1024 行；输出重建旧逻辑值、整数纳秒时间和来源/有效性映射，
全量逻辑哈希须与旧 fragment 一致才提交。数值不使用容差掩盖转换差异。

RGB 与已编码深度直接复制，不重编码/量化；深度沿用现有全帧码值、profile、
掩码、帧映射校验。误差报告标明量化误差继承自固定来源报告，未重新读取原始
传感器数组测量；不能声称新验证了原始深度。缺少任一已声明相机的准备资产、
深度证据或被篡改的媒体拒绝迁移。新包 QC 为 UNKNOWN，不自动继承旧审核。

每个计划有持久化 `plan.json`、`converted.json`、`staged.json`。文件与目录
fsync 后以不可覆盖的原子链接保存；同机并发文件锁和既有数据库提交锁共同约束
重试。转换完成后复用同一输出目录，重新核验文件及来源；残留未完成尝试留存。
提交采用 `activate=False`，不修改 Episode 当前指针或集合指针。转换报告作为
`hc/migration.json` 包含在主包哈希中，保留旧引用与修订行哈希映射。

执行：

```bash
bash deploy/chayi/compose.sh run --rm --no-deps test -q \
  tests/episode_store/test_migration_conversion_services.py \
  --fail-on-skipped --fail-on-xfailed --tb=short \
  --junitxml=/app/.g1-artifacts/migration-conversion-tests.xml
bash deploy/chayi/compose.sh run --rm --no-deps reader \
  /app/tools/read_migrated_episode.py \
  /evidence/migration-conversion-offline-reader.json \
  /app/.g1-artifacts/migration-conversion/0782214e38c8ac75307db30106d5ca41123333db736b39f3351ee11a7eb46073
```

- [15 项服务测试](migration-conversion-tests.xml)：2053 行跨窗口读取；float64
  数值、超过 JavaScript 安全整数范围的值和纳秒时间精确保留；中途单值篡改
  被拒绝。6 个中断位置分别覆盖来源窗口、转换断点、提交预留、对象核验、数据库
  提交和落盘回执；新实例续跑没有重复版本、没有提前激活。另含三线程并发、
  组织/项目/地域拒绝、路径篡改、缺相机、媒体篡改、错误来源及特征声明拒绝。
- 比较迁移前后清单：所有非 episode_store 表行保持相同，旧历史和审核未改写。
- [离线官方 reader 证据](migration-conversion-offline-reader.json)：禁网容器、
  LeRobot 0.6.1、未导入平台代码；读取从已提交对象复制出的完整标准包，验证
  全部文件哈希和独立重建的旧逻辑哈希。4 帧 RGB-D，4 个无效像素独立应用 HC
  掩码；深度输出 mm，官方整数毫米取整与独立反量化最大差 0.4491 mm。
  这不是原始深度量化误差；量化与视频码值无损分别保留在包内报告。
- 实际深度 codec profile 为 `Rext`、`gray12le`；RGB/深度视频原哈希保留。
  官方 reader 提示忽略自定义 HC metadata，已独立核验 HC 数据，不隐藏该提示。
- Ruff/format 和 `git diff --check` 通过。首次夹具因旧库要求完整来源字段而
  13 项 setup error，随后修正 Episode ID 格式又有 2 项 setup error；原始
  XML 保留于 `.g1-artifacts/migration-conversion-before-{fixture,identity}-fix.xml`。
  最终 XML 无 skip/xfail/error/failure。

剩余：历史物理清单展开和准备资产适配、原始 MKV/数组来源转换、批次计划 CLI、
数据库批次 checkpoint、产品投影与集合/Episode 指针原子切换及正式回退工具。
当前原始 Lance 位于测试容器临时目录，不能据此声称可供后续整批演练恢复；整批
演练需持久化独立副本。真实历史数据、旧审核等价关系和规模验收未完成。
G1 继续；完成后按新增授权自动接续 G2、G3。模型深度训练支持 NOT_VALIDATED。
