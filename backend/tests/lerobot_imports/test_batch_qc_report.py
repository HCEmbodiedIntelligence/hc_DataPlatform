import xml.etree.ElementTree as ET
from zipfile import ZipFile

from hc_data_platform.lerobot_imports import batch_report_analysis as analysis
from hc_data_platform.lerobot_imports.batch_report_xlsx import write_workbook


def test_only_rejecting_items_and_their_frame_ranges_are_exported():
    report = {
        "finding_codes": ["MCAP_INTERIOR_ACTION_MISSING", "QUALITY_REJECT"],
        "capture_quality": {"interior_action_gaps": [{"frame": 8}, {"frame": 9}]},
        "quality_report": {"findings": [
            {"code": "QC_REQUIRED_TOPIC_MISSING", "severity": "error", "topic": "/camera/depth/image"},
            {"code": "QC_FREQUENCY_LOW", "severity": "warning", "topic": "/camera/head/image"},
        ]},
    }
    cfg = {"action_topic": "/cmd", "cameras": [{"camera_id": "depth", "topic": "/depth"}]}
    items = analysis.failure_items("QC_FAILED", report, None, cfg)
    assert len(items) == 2
    assert [(topic, note) for _, topic, note in items] == [("/depth", ""), ("/cmd", "")]
    assert "第 8–9 帧" in items[1][0]


def test_processing_failure_preserves_exact_capture_timestamp():
    items = analysis.failure_items("FAILED", None, "MCAP_FOLLOWING_STATE_UNAVAILABLE: /cmd capture_time_ns=123", {})
    assert items == [("处理失败 · 动作之后无可匹配的关节状态", "/cmd", "动作采集时间 123 ns，缺后续 state")]


def test_excel_literal_values(tmp_path):
    path = tmp_path / "report.xlsx"
    note = "第 1、" * 12000
    write_workbook(path, 882, [["=sample.mcap", "左夹爪", "/left", note]])
    with ZipFile(path) as book:
        row = ET.fromstring(book.read("xl/worksheets/sheet1.xml")).findall(".//{*}row")[-1]
        cells = [c.findtext(".//{*}t") for c in row]
        assert cells[0] == "=sample.mcap"
        assert row[0].get("t") == "inlineStr"
        assert "".join(cells[3:]) == note


def test_multi_batch_empty_sheet_keeps_headers_and_filters(tmp_path):
    from hc_data_platform.lerobot_imports.batch_report_xlsx import write_batch_workbook

    path = tmp_path / "combined.xlsx"
    write_batch_workbook(path, {881: [], 883: [["clip", "处理失败", "/cmd", ""]]})
    with ZipFile(path) as book:
        tabs = ET.fromstring(book.read("xl/workbook.xml")).findall(".//{*}sheet")
        assert [t.get("name") for t in tabs] == ["第881批", "第883批"]
        for index, count in [(1, 3), (2, 4)]:
            sheet = ET.fromstring(book.read(f"xl/worksheets/sheet{index}.xml"))
            assert len(sheet.findall(".//{*}row")) == count
            assert sheet.find("{*}autoFilter").get("ref") == f"A3:D{count}"


def test_whole_action_gap_and_filled_gap_use_the_recorded_axis_contract():
    report = {
        "finding_codes": ["MCAP_INTERIOR_ACTION_MISSING"],
        "capture_quality": {
            "axis_validity": {"action": {"required_axes": ["Joint1_L", "zhi"], "optional_axes": ["head"]}},
            "interior_action_gaps": [{"frame": 56, "origin": "missing"}, {"frame": 99, "origin": "next_frame_state"}],
        },
        "quality_report": {"status": "RISK", "findings": [
            {"severity": "warning", "code": "QC_COMPLETE_STEP_RATIO_LOW", "topic": "__multimodal_step__"}
        ]},
    }
    rows = analysis.failure_items("QC_FAILED", report, None, {"action_joint_names": ["head", "Joint1_L", "zhi"]})
    assert len(rows) == 2
    assert "第 56 帧" in rows[0][0] and "缺少 2 个关节的动作位置（position）：Joint1_L、zhi" in rows[0][0]
    assert "下一帧状态补写" in rows[1][0] and "缺少 2 个关节的动作位置" not in rows[1][0]


def test_following_state_uses_only_the_axes_from_the_enabled_replacement():
    cfg = {"state_topic": "/actual", "postprocessing_rules": {"rules": [
        {"kind": "action_from_state", "enabled": False, "axes": ["head"]},
        {"kind": "action_from_state", "enabled": True, "axes": ["leg_1", "zhi"]},
    ]}}
    row, = analysis.failure_items("FAILED", None, "MCAP_FOLLOWING_STATE_UNAVAILABLE: /cmd capture_time_ns=123", cfg)
    assert "实测位置（position）：leg_1、zhi" in row[0] and "head" not in row[0]
    assert row[1] == "/actual" and "/cmd" in row[2]


def test_partial_layout_error_keeps_only_reported_missing_names():
    row, = analysis.failure_items("FAILED", None, "MCAP_JOINT_LAYOUT_MISMATCH: /cmd missing=['zhi'] at 123", {})
    assert row[1] == "/cmd" and "位置（position）：zhi" in row[0]


def test_ratio_details_use_required_axis_counts_with_the_correct_modality():
    capture = {"valid_ratio": 0.5, "valid_rows": 1, "total_rows": 2, "axis_validity": {
        "action": {"required_axes": ["zhi", "Joint1_L"], "missing_rows_by_axis": {"zhi": 1, "head": 1}},
        "observation.state": {"required_axes": ["Joint1_L"], "missing_rows_by_axis": {"Joint1_L": 2}},
    }}
    rows = analysis.failure_items("QC_FAILED", {"finding_codes": ["CAPTURE_VALID_RATIO_LOW"], "capture_quality": capture},
                                 None, {"action_topic": "/cmd", "state_topic": "/actual"})
    assert [(r[1], r[0].split("：")[-1]) for r in rows[1:]] == [("/cmd", "zhi（1 帧）"), ("/actual", "Joint1_L（2 帧）")]
