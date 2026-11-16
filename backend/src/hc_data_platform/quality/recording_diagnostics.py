"""Chinese diagnostics for rejected raw reports and failed MCAP preparations."""

import re
from dataclasses import dataclass

from hc_data_platform.quality.processing_errors import (
    MCAP_ASSET_CONFLICT,
    PROCESSING_ERRORS,
    recording_preparation_error,
)


@dataclass(frozen=True)
class RawProblem:
    code: str
    label: str
    description: str

LABELS = {
    "MCAP_UNKNOWN_OPTIONAL_TOPIC": "发现未识别的可选话题",
    "MCAP_REQUIRED_TOPIC_MISSING": "缺少必需话题",
    "MCAP_TOPIC_EMPTY": "话题已登记，但消息数为 0",
    "MCAP_DECODE_FAILED": "话题消息无法解码",
    "MCAP_SCHEMA_MISSING": "话题缺少消息结构定义",
    "MCAP_MISSING_MAGIC": "文件开头缺少 MCAP 标识",
    "MCAP_TRUNCATED": "文件内容提前结束",
    "MCAP_HEADER_MISSING": "缺少 MCAP 文件头",
    "MCAP_DATA_END_MISSING": "缺少数据区结束记录",
    "MCAP_FOOTER_MISSING": "缺少 MCAP 文件尾",
    "MCAP_TRAILING_MAGIC_MISSING": "文件结尾缺少 MCAP 标识",
    "MCAP_RECORD_TOO_LARGE": "单条记录超出大小限制",
    "MCAP_RECORD_INVALID": "MCAP 记录格式错误",
    "MCAP_DATA_SECTION_CRC_MISMATCH": "数据区校验和不一致",
    "MCAP_CHUNK_CRC_MISMATCH": "数据块校验和不一致",
    "MCAP_CHUNK_SIZE_MISMATCH": "数据块实际大小与声明值不同",
    "MCAP_CHUNK_TOO_LARGE": "数据块超出大小限制",
    "MCAP_CHUNK_DECOMPRESSION_FAILED": "数据块解压失败",
    "MCAP_UNSUPPORTED_COMPRESSION": "无法读取此压缩格式",
    "MCAP_SUMMARY_INVALID": "MCAP 摘要格式错误",
    "MCAP_SUMMARY_CRC_MISMATCH": "MCAP 摘要校验和不一致",
    "MCAP_INDEX_INVALID": "MCAP 索引无效",
    "MCAP_ACTION_EVIDENCE_MISSING": "缺少用于生成动作的来源记录",
    "MCAP_DEPTH_UNITS_REQUIRED": "深度图像未配置深度单位",
    "MCAP_DURATION_LIMIT": "录制时长超出处理上限",
    "MCAP_FRAME_CLOCK_REQUIRES_STATE": "生成帧时间轴需要关节状态消息",
    "MCAP_GENERATED_ACTION_AXIS_MISSING": "生成动作时缺少关节轴",
    "MCAP_NAMED_JOINT_REQUIRED": "关节字段需要按关节名称配置",
    "MCAP_PREPARATION_FAILED": "录制准备失败",
    "MCAP_REBUILD_METADATA_MISMATCH": "重建文件的元数据与配置不一致",
    "MCAP_RECORDING_TIME_LAYOUT_INVALID": "录制的时间顺序或时间范围无效",
    "MCAP_ROBOT_NOT_FOUND": "找不到录制关联的机器人",
    "MCAP_SOURCE_REQUIRED": "缺少 MCAP 源文件",
    "MCAP_TAKEOVERS_INVALID": "接管区间记录无效",
    "MCAP_INVALID": "MCAP 文件无法解析",
    "MCAP_FOLLOWING_STATE_UNAVAILABLE": "动作帧找不到可用于补值的后续状态帧",
    "MCAP_WAIST_STATE_UNAVAILABLE": "动作关节缺少可用于补值的状态帧",
    "MCAP_CAPTURE_CLOCK_REQUIRED": "消息缺少有效的采集时间",
    "MCAP_CLOCK_DOMAIN_MISMATCH": "消息采集时间与录制时钟不一致",
    "MCAP_SELECTED_SOURCE_MISSING": "方案要求的话题没有可用消息",
    "MCAP_EMPTY_REQUIRED_TOPIC": "必需话题没有可用消息",
    "MCAP_REQUIRED_RGB_MISSING": "缺少必需的彩色图像消息",
    "MCAP_REQUIRED_DEPTH_MISSING": "缺少必需的深度图像消息",
    "MCAP_SCHEMA_REQUIRED": "话题缺少消息结构定义",
    "MCAP_MAPPING_INVALID": "话题与采集配置不匹配",
    "MCAP_MAPPING_REQUIRED": "缺少话题映射配置",
    "MCAP_NUMERIC_TYPE_MISMATCH": "信号的消息类型与配置不一致",
    "MCAP_REQUIRED_SIGNAL_MISSING": "缺少必需信号",
    "MCAP_AMBIGUOUS_SOURCE": "多个话题匹配同一配置",
    "MCAP_JOINT_LAYOUT_MISMATCH": "关节名称或字段长度与配置不一致",
    "MCAP_NUMERIC_INVALID": "字段缺失或数值无效",
    "MCAP_NUMERIC_RANGE_INVALID": "字段数值超出允许范围",
    "MCAP_NUMERIC_LAYOUT_MISMATCH": "数值字段维度不匹配",
    "MCAP_NUMERIC_DTYPE_UNSUPPORTED": "不支持此数值类型",
    "MCAP_SOURCE_MAPPING_REQUIRED": "缺少输出字段的话题映射",
    "MCAP_SOURCE_MAPPING_INVALID": "输出字段的话题映射无效",
    "MCAP_REFERENCE_FRAME_CHANGED": "信号的参考坐标系发生变化",
    "MCAP_SOURCE_TIME_LAYOUT_INVALID": "信号的时间戳异常",
    "MCAP_CAMERA_LAYOUT_CHANGED": "相机图像结构发生变化",
    "MCAP_BOOLEAN_INVALID": "字段值不符合布尔类型",
    "MCAP_NONFINITE_JOINT": "关节字段含无效数值",
    "MCAP_TIME_DISCONTINUITY": "消息时间戳异常",
    "CAPTURE_MAPPING_MISMATCH": "机器人通道映射与采集方案不匹配",
    "ROBOT_PROCESSING_CONFIG_REQUIRED": "机器人缺少录制处理配置",
    "ROBOT_PROCESSING_CONFIG_CONFLICT": "机器人处理配置冲突",
    "ROBOT_PROCESSING_CONFIG_VERSION_UNKNOWN": "找不到指定的机器人处理配置版本",
    "ROBOT_TASK_DATA_INCOMPATIBLE": "机器人数据与任务配置不匹配",
    "CAPTURE_MAPPING_UNVERIFIED": "机器人通道映射尚未验证",
}


def _channel_lines(state_topic, action_topic, inventory):
    lines = []
    for role, topic in (("动作", action_topic), ("状态", state_topic)):
        if not topic:
            continue
        channel = (inventory or {}).get(topic) or {}
        facts = []
        if channel.get("message_count") is not None:
            facts.append(f"{channel['message_count']} 条消息")
        if channel.get("schema"):
            facts.append(f"类型 {channel['schema']}")
        suffix = "（" + "，".join(facts) + "）" if facts else ""
        lines.append(f"{role}话题：{topic}{suffix}")
    return lines


def preparation_problem(error, path, state_topic, action_topic, inventory, rules=None):
    code, _, detail = (recording_preparation_error(error) or "").partition(":")
    detail = detail.strip()
    if code == "MCAP_DUPLICATE_RECORDING":
        return RawProblem(code, "重复录制", f"{path} 与录制 {detail} 的原始内容相同，已跳过解析和入库。")
    if code == MCAP_ASSET_CONFLICT:
        return RawProblem(code, "录制登记时文件路径冲突", f"{path}：文件路径已被其他录制占用，质检尚未开始。")
    if code == "RECORDING_WORKER_SHUTDOWN":
        observations = []
        for topic in dict.fromkeys((state_topic, action_topic)):
            channel = (inventory or {}).get(topic) or {}
            if channel.get("message_count") is not None:
                observations.append(f"{topic} 有 {channel['message_count']} 条消息")
        reason = "后台处理进程在准备录制时停止，本次没有得出话题缺失或格式错误的结论。"
        if observations:
            reason += "文件中已读到 " + "，".join(observations) + "。"
        return RawProblem(code, "录制准备中断", f"{path}：{reason}可点击“重试处理”继续。")
    if not code:
        return None
    if not re.fullmatch(r"[A-Z][A-Z0-9_]+", code):
        # Some older preparation attempts stored a message without a domain code.
        lines = [f"文件：{path}", f"处理记录：{error.splitlines()[0]}", *_channel_lines(state_topic, action_topic, inventory)]
        return RawProblem(code, "录制准备失败", "\n".join(lines))
    label = LABELS.get(code) or PROCESSING_ERRORS.get(code) or "录制准备失败"
    if code == "MCAP_FOLLOWING_STATE_UNAVAILABLE":
        lines = ["方案要求从采集时间等于或晚于这条动作的状态帧补关节值，符合条件的状态帧为 0 条。"]
        match = re.fullmatch(r"(\S+) capture_time_ns=(\d+)", detail)
        if match:
            lines.append(f"出错动作：{match[1]}，采集时间 {match[2]} ns")
        elif detail:
            lines.append(f"处理记录：{detail}")
        lines.extend(_channel_lines(state_topic, action_topic, inventory))
        for rule in rules or ():
            if rule.get("kind") == "action_from_state" and rule.get("enabled", True) and rule.get("axes"):
                lines.append("需从状态帧补值的关节：" + "、".join(rule["axes"]))
        lines.append(f"文件：{path}")
        return RawProblem(code, label, "\n".join(lines))
    reason = label
    expected = re.fullmatch(r"(\S+) expected (.+), actual (.+)", detail)
    missing_joints = re.fullmatch(r"(state|action) missing=(.+); actual=(.+)", detail)
    camera = re.search(r"required topic=([^;]+); message_count=0", detail)
    mapping = re.fullmatch(r"(state|action) topic '([^']+)' absent, empty or incompatible; candidates=.+", detail)
    if expected:
        topic, required, actual = expected.groups()
        reason = f"话题 {topic} 格式不符，方案要求 {required}，文件实际为 {actual}"
    elif missing_joints:
        role, missing, actual = missing_joints.groups()
        topic = state_topic if role == "state" else action_topic
        reason = f"{topic or role} 缺少关节 {missing}，实际关节为 {actual}"
    elif camera:
        topic = camera[1]
        reason = f"{label}，话题 {topic} 的消息数为 0"
    elif mapping:
        topic = mapping[2]
        channel = (inventory or {}).get(topic)
        if inventory and channel is None:
            reason = f"文件中缺少配置的话题 {topic}"
        elif channel and channel.get("message_count") == 0:
            reason = f"话题 {topic} 已登记，但消息数为 0"
        elif channel and channel.get("schema"):
            reason = f"话题 {topic} 要求有效的关节消息，实际消息类型为 {channel['schema']}"
        else:
            reason = f"话题 {topic} 没有可用的关节消息"
    elif code == "MCAP_WAIST_STATE_UNAVAILABLE" and (
        match := re.fullmatch(r"(\S+) axes=(.+?) capture_time_ns=(\d+)(?: tolerance_ms=([\d.]+))?", detail)
    ):
        reason = f"话题 {match[1]} 的关节 {match[2]} 缺少可用于补值的状态帧，采集时间 {match[3]} ns"
        if match[4]:
            reason += f"，允许时间差 {match[4]} ms"
    elif detail:
        # Preserve recorded topic/field names and values in older worker messages.
        joint = re.fullmatch(r"(\S+) missing=(.+) at (\d+)", detail)
        if joint:
            reason = f"话题 {joint[1]} 缺少关节 {joint[2]}，时间戳 {joint[3]}"
        elif detail.endswith(" requires JointState"):
            reason = f"话题 {detail.split()[0]} 的消息类型必须为 sensor_msgs/msg/JointState"
        else:
            reason += "：" + detail
    lines = [f"{path}：{reason}。", *_channel_lines(state_topic, action_topic, inventory)]
    return RawProblem(code, label, "\n".join(lines))
