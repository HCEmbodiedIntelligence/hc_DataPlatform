"""Safe, actionable messages for a rejected capture scheme draft."""

# These messages are fixed by the server. Payload values and arbitrary field
# names from Pydantic errors must never be copied into logs or responses.
REASONS = {
    "select only one action generation policy": ("ActionPolicyConflict", "数据后处理规则冲突。“首尾缺动作时用关节状态补齐”不能与“首条动作前用关节状态补齐”或“缺少动作话题时生成动作”同时启用。“状态与动作短缺帧插补”可以单独启用，也可以与首尾补齐配合。"),
    "enabled zero and replacement joints must be disjoint": ("JointRuleConflict", "关节置零和动作跟随状态选中了同一关节，请只为该关节保留一种规则。"),
    "short gap interpolation requires mapped state and action on a capture timeline": ("MissingJointMapping", "短缺帧插补需要状态和动作话题，请在原始文件配置里填写这两项。"),
    "state source joints must have matching names and units": ("JointLayoutMismatch", "从状态补动作的关节必须在状态和动作中使用相同的名称和单位。"),
    "hand forward fill requires a mapped scalar hand command": ("MissingHandMapping", "手部补值需要先在原始文件配置中映射至少一侧的手部指令。"),
    "joint limits require a name and finite ordered bounds": ("JointLimitOrder", "请检查质检规则中的关节范围，每个关节的下限必须小于上限。"),
    "gap reject threshold must be at least risk threshold": ("GapThresholdOrder", "在时间与连续性质检规则中，帧间隔的拒绝阈值必须大于或等于风险阈值。"),
    "missing-frame reject threshold must be at least risk threshold": ("MissingFrameThresholdOrder", "在时间与连续性质检规则中，连续缺帧的拒绝阈值必须大于或等于风险阈值。"),
    "offset reject threshold must be at least risk threshold": ("OffsetThresholdOrder", "在多路同步质检规则中，同步偏差的拒绝阈值必须大于或等于风险阈值。"),
    "capture-structure/v3 requires complete float64 17-axis positions in rad": ("PositionLayout", "HC TJ 方案需要完整的 17 轴状态及动作，类型为 float64，单位为 rad。"),
}
SECTIONS = {
    "postprocessing_rules": ("PostprocessingRules", "请检查数据后处理规则的选择和参数。"),
    "quality_policy": ("QualityPolicy", "请检查采集方案质检规则中的范围和风险、拒绝阈值。"),
    "cameras": ("CameraMapping", "请检查相机映射的标识、话题和消息类型。"),
    "numeric_sources": ("NumericMapping", "请检查数值来源的 ROS 话题和字段映射。"),
    "features": ("OutputFeatures", "请检查数据定义中的字段名称、类型、维度和单位。"),
    "adapters": ("InputAdapter", "请检查原始文件配置中的 MCAP 话题、关节和适配器。"),
    "output": ("OutputDefinition", "请检查数据定义的时间轴和输出结构。"),
}


def save_validation_hint(errors):
    for error in errors:
        text = error.get("msg", "").removeprefix("Value error, ")
        if text in REASONS:
            return REASONS[text]
    for field, hint in SECTIONS.items():
        if any(field in error.get("loc", ()) for error in errors):
            return hint
    return "DraftDefinition", "方案字段未通过校验，请核对适用范围、数据定义和原始文件配置。"
