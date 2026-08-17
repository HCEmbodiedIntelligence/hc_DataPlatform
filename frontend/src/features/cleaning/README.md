# 清洗功能归属

`ManualIssue` 和 `CleaningDraft` 属于人工清洗功能。P09 是 `ManualIssue` 变更的唯一负责人；
P10 只负责读取投影；P11 只能编辑服务端选定的草稿 ID。

由 P07 负责的 `ReviewFinding` 是不可变的审核决定事实，与 `ManualIssue` 严格分离：
二者不共享 ID 类型或序列、状态枚举、DTO/传输模式、查询键领域、权限、变更、审计事件或负责人。
P11 只能读取已授权的 Finding 投影，用于显示和时间定位。

代码和测试必须明确保留两个相互独立的交接流程：

- P09 `ManualIssue` → 由服务端创建或已关联的 `CleaningDraft`。
- P07 `ReviewFinding` → 以原子方式创建的后继 `CleaningDraft`。

绝不能把二者合并成通用的问题转草稿辅助函数。绝不能从该功能导入或调用审核变更操作。
