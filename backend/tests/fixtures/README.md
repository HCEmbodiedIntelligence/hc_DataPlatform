# BE-12 确定性测试夹具

使用以下命令重新生成所有二进制和结构化测试夹具：

```bash
python tests/fixtures/generate_fixtures.py
```

`manifest.json` 记录字节大小、SHA-256、预期状态和稳定错误码。MCAP 夹具是不可变的测试输入；
测试绝不能就地修复或改写它们。质量夹具包含版本化配置，以及质量检测公开契约所使用的规范化观测数据。
