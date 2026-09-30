# 当前工作版本与回退读取

2026-09-30，隔离 `hc-chayi-g1`。P06/P21 bootstrap 原先只识别
`version_lance_*`；已接入新 Episode 集合，但不会把内部工作集合自动标成业务发布。
PostgreSQL 根据实际 collection head 及其祖先选择已完成产品登记的版本；如果头部
尚未登记，则沿该分支回退。已回退归档的更新集合不会因时间更新被错误选中。
没有集合产品登记时，使用实际旧 Lance 当前指针，不伪造 Lance 版本。

[23 项测试](working-version-tests.xml)通过，无 skip/xfail：

```bash
bash deploy/chayi/compose.sh run --rm --no-deps test -q \
  tests/episode_store/test_working_version_services.py \
  tests/dataset_registry/test_dataset_detail_api.py \
  tests/dataset_registry/test_dataset_page_api.py \
  tests/dataset_registry/test_dataset_version_detail_api.py \
  --fail-on-skipped --fail-on-xfailed --tb=short \
  --junitxml=/app/.g1-artifacts/working-version-tests.xml
```

包含实际 PostgreSQL/MinIO 的登记延迟、回退、回退后再次发布、旧版本保留读取和
三维租户隔离；一个已有 Starlette/httpx 弃用提示。回退只对自有隔离夹具直接 SQL
修改，用于读取语义测试，不能算正式 W7 批次回退工具验收。

[实际 HTTP 结果](working-version-http-results.json)来自已有 MCAP、原生 LeRobot、
连续录制、独立 uploader 的隔离输出。标准登录会话/授权下，数据集 bootstrap、
业务版本列表、选中工作版本 bootstrap 均 200，返回值与实际集合头一致。版本列表
仍只列业务发布，内部版本通过明确 ID 访问；会话均注销。驱动保留在
`backend/.g1-artifacts/working-version-http.py`。初次测试的 import 路径和驱动中
误用的 `include_internal` 查询参数已修正；后者不是公开 API 参数。

输入均合成，不声称真实样本或浏览器验收。核对中另外发现 P06 手动业务发布仍
依赖 `base_lance_version`；此修复仅解决工作版本默认读取，业务发布适配继续进行。
无现网业务写入，G1/W7/W8 未完成，G2/G3 尚未执行。
