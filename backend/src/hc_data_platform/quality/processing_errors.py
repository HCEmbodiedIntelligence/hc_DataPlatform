"""Processing configuration, structure and corruption are distinct actionable failures."""

PROCESSING_ERRORS = {
    "CAPTURE_PLAN_REQUIRED": "任务采集方案待配置",
    "CAPTURE_VERSION_MISMATCH": "采集方案版本不匹配",
    "CAPTURE_FORMAT_UNSUPPORTED": "采集方案不支持此格式",
    "CAPTURE_STORAGE_INCOMPATIBLE": "此存储格式无法保留采集方案的缺失与有效性信息",
    "CAPTURE_STRUCTURE_MISMATCH": "实际数据与采集方案结构不一致",
    "CAPTURE_DATASET_INCOMPATIBLE": "任务数据结构与已有数据集不兼容",
    "CAPTURE_DATASET_REVIEW_REQUIRED": "已有数据集需要核对历史字段语义",
    "CAPTURE_SOURCE_INVALID": "原始文件内容损坏或无法解码",
    "CAPTURE_MISSING_REPRESENTATION_UNSUPPORTED": "当前格式无法表达此缺失数据",
    "ANNOTATION_SCHEMA_BINDING_MISSING": "标注待配置",
}


def configuration_error(code: str) -> bool:
    return code in PROCESSING_ERRORS or code.startswith("CAPTURE_")
