"""Display all rejected raw verification findings with their locations."""

from hc_data_platform.quality.recording_diagnostics import LABELS, RawProblem


def verification_problem(findings):
    errors = [f for f in findings or () if f.get("severity") == "error"]
    if not errors:
        return None
    details = []
    for finding in errors:
        code = finding["code"]
        reason = {
            "decoder probe is not configured": "尚未配置消息解码器",
            "no decoder supports the schema pair": "解码器不支持该消息格式",
        }.get(finding.get("message"), LABELS.get(code, f"原始数据校验异常（{code}）"))
        if finding.get("topic"):
            reason += f"（话题 {finding['topic']}）"
        if finding.get("record_offset") is not None:
            reason += f"，文件字节偏移 {finding['record_offset']}"
        message = finding.get("message", "")
        if message and code not in {
            "MCAP_REQUIRED_TOPIC_MISSING", "MCAP_TOPIC_EMPTY", "MCAP_SCHEMA_MISSING",
        } and message not in {"decoder probe is not configured", "no decoder supports the schema pair"}:
            reason += f"（校验记录：{message}）"
        details.append(reason)
    code = errors[0]["code"]
    return RawProblem(code, LABELS.get(code, "原始数据校验异常"), "。\n".join(dict.fromkeys(details)) + "。")
