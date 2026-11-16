import pytest

from hc_data_platform.dashboard.raw_problems import verification_problem
from hc_data_platform.quality.recording_diagnostics import preparation_problem
from hc_data_platform.dashboard.repository import TaskStatusPackageFact
from hc_data_platform.dashboard.service import _data_issues


def issue_for(problem):
    fact = TaskStatusPackageFact(
        task_id="task", rollout_id="rollout", data_package_id="pkg", rollout_status="COMMITTED",
        raw_committed=True, verification_status="REJECTED", verification_reason_code=problem.code,
        raw_problem=problem,
    )
    return _data_issues((fact,))[0]


def test_worker_shutdown_reports_observations_without_claiming_invalid_data():
    issue = issue_for(preparation_problem(
        "RECORDING_WORKER_SHUTDOWN: Recording worker is shutting down",
        "example.mcap", "/state", "/action", {"/state": {"message_count": 13}},
    ))
    assert issue.label == "录制准备中断"
    assert "example.mcap" in issue.description and "/state 有 13 条消息" in issue.description
    assert "重试处理" in issue.description and issue.reason_code == "RECORDING_WORKER_SHUTDOWN"
    assert "/action" not in issue.description


@pytest.mark.parametrize(("error", "evidence"), [
    ("MCAP_SELECTED_SOURCE_MISSING: ['/camera']", "/camera"),
    ("MCAP_MAPPING_INVALID: /cam expected Image/rgb, actual PointCloud/other", "文件实际为 PointCloud/other"),
    ("MCAP_JOINT_LAYOUT_MISMATCH: state missing=['head']; actual=['arm']", "/state 缺少关节 ['head']"),
    ("MCAP_JOINT_LAYOUT_MISMATCH: /arm missing=['hand'] at 99", "时间戳 99"),
])
def test_preparation_keeps_topic_and_actual_values(error, evidence):
    assert evidence in issue_for(preparation_problem(
        error, "a.mcap", "/state", "/action", None,
    )).description


def test_verification_preserves_all_error_locations_and_skips_warnings():
    problem = verification_problem([
        {"code": "MCAP_REQUIRED_TOPIC_MISSING", "topic": "/rgb", "severity": "error"},
        {"code": "MCAP_TOPIC_EMPTY", "topic": "/depth", "severity": "error"},
        {"code": "MCAP_RECORD_INVALID", "record_offset": 0, "severity": "error"},
        {"code": "MCAP_UNKNOWN_OPTIONAL_TOPIC", "topic": "/debug", "severity": "warning"},
    ])
    message = issue_for(problem).description
    assert "缺少必需话题（话题 /rgb）" in message
    assert "消息数为 0（话题 /depth）" in message
    assert "字节偏移 0" in message and "/debug" not in message


def test_missing_following_state_reports_capture_clock_and_both_channels():
    problem = preparation_problem(
        "MCAP_FOLLOWING_STATE_UNAVAILABLE: /action/joint_states capture_time_ns=1794279194669859704",
        "mcap/teleop_20261110_105302.mcap", "/observation/joint_states", "/action/joint_states",
        {"/observation/joint_states": {"schema": "sensor_msgs/msg/JointState", "message_count": 1195,
                                      "last_log_time_ns": 1794279194669386411},
         "/action/joint_states": {"message_count": 769}},
        [{"kind": "action_from_state", "enabled": True, "axes": ["Joint1_L", "zhi"]}],
    )
    issue = issue_for(problem)
    for expected in ("符合条件的状态帧为 0 条", "采集时间 1794279194669859704 ns",
                     "状态话题：/observation/joint_states（1195 条消息",
                     "动作话题：/action/joint_states（769 条消息", "Joint1_L、zhi"):
        assert expected in issue.description
    assert "1794279194669386411" not in issue.description


def test_unlisted_preparation_code_keeps_worker_evidence():
    issue = issue_for(preparation_problem(
        "MCAP_NEW_FORMAT_ERROR: /cam frame=284", "sample.mcap", None, None, None
    ))
    assert "/cam frame=284" in issue.description
    assert issue.reason_code == "MCAP_NEW_FORMAT_ERROR"
