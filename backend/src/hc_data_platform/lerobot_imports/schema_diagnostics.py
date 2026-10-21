"""Explain a frozen data schema conflict without changing either schema."""

from contextlib import suppress

from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot


def feature_label(topic: str) -> str:
    name = topic
    if topic.startswith("/lerobot/features/"):
        with suppress(ValueError, UnicodeDecodeError):
            name = bytes.fromhex(topic.removeprefix("/lerobot/features/")).decode("utf-8")
    elif topic.startswith("/camera/"):
        name = "observation.images." + topic.split("/")[2]
    elif topic in {"/humanoid/observation/state", "/observation/state"}:
        name = "observation.state"
    elif topic in {"/humanoid/action", "/action"}:
        name = "action"
    elif topic == "/metadata/source":
        name = "metadata.source"
    friendly = {
        "action.base.velocity": "底盘速度命令",
        "observation.base.pose": "底盘位姿",
        "action.left_arm.ee_pose": "左臂末端位姿命令",
        "action.right_arm.ee_pose": "右臂末端位姿命令",
        "observation.left_arm.ee_pose": "左臂末端位姿",
        "observation.right_arm.ee_pose": "右臂末端位姿",
        "observation.headset.pose": "头显位姿",
        "observation.state": "关节状态",
        "action": "关节动作命令",
    }.get(name)
    if name.startswith("observation.images."):
        camera = name.removeprefix("observation.images.")
        friendly = (
            next(
                (
                    label
                    for key, label in (("head", "头部"), ("left", "左腕"), ("right", "右腕"))
                    if key in camera
                ),
                camera,
            )
            + "相机视频"
        )
    return f"{name}（{friendly}）" if friendly else name


def schema_conflict_detail(
    incoming: DatasetSchemaSnapshot, existing: DatasetSchemaSnapshot | None
) -> str:
    prefix = "数据集结构已固定，上传数据与已有结构冲突："
    if existing is None:
        return prefix + "无法匹配已固定的数据结构，请使用结构兼容的数据集。"
    parts = []
    for title, fields in (
        ("缺少", existing.fields.keys() - incoming.fields.keys()),
        ("新增", incoming.fields.keys() - existing.fields.keys()),
    ):
        if fields:
            parts.append(title + " " + "、".join(feature_label(key) for key in sorted(fields)))
    if existing.frequency_hz != incoming.frequency_hz:
        parts.append(
            f"采集频率不一致（已有 {existing.frequency_hz:g}Hz，本次 {incoming.frequency_hz:g}Hz）"
        )
    if existing.semantic_contract != incoming.semantic_contract:
        before = (existing.semantic_contract or {}).get("features", {})
        after = (incoming.semantic_contract or {}).get("features", {})
        changed = [
            key for key in sorted(before.keys() | after.keys()) if before.get(key) != after.get(key)
        ]
        parts.append(
            "字段语义不一致（维度、轴名、单位或缺失规则）："
            + "、".join(feature_label(key) for key in changed)
            if changed
            else "采集方案输出约定不一致"
        )
    return prefix + "；".join(parts or ["字段类型或固定版本不一致"])
