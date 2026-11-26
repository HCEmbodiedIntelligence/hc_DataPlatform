import json

import pytest
from mcap.reader import make_reader
from mcap.writer import Writer

from hc_data_platform.capture_schemes.schema import native_topic
from hc_data_platform.continuous_recordings.capture_quality import interior_action_gaps
from hc_data_platform.continuous_recordings.mcap_native import prepare

from .test_mcap_v3 import NAMES, configuration, source


@pytest.mark.parametrize("enabled", [True, False])
def test_interpolation_on_preserved_camera_frames(tmp_path, enabled):
    cfg, _ = configuration()
    cfg = cfg.model_dump(mode="json")
    cfg.update(time_source="RGB_LOG_TIME", primary_camera=cfg["cameras"][0]["topic"], maximum_time_error_ms=1)
    cfg["semantic_output"]["time_source"] = "RGB_LOG_TIME"
    cfg["postprocessing_rules"] = {"version": 2, "rules": [{"kind": "interpolate_short_gaps", "enabled": enabled}]}
    times = [0, 44, 100, 170, 211, 265, 300, 355, 415, 480, 570, 621, 680]
    raw = tmp_path / "raw.mcap"
    template = source(tmp_path / "template.mcap", [NAMES] * len(times))
    with template.open("rb") as src, raw.open("wb") as dest:
        w = Writer(dest)
        w.start(profile="ros2")
        channels = {}
        for schema, channel, msg in make_reader(src).iter_messages():
            topic, i = channel.topic, msg.sequence
            numeric = topic in (cfg["state_topic"], cfg["action_topic"])
            if topic not in channels:
                sid = w.register_schema(schema.name, schema.encoding, schema.data)
                channels[topic] = w.register_channel(topic, "json" if numeric else channel.message_encoding, sid)
            action = topic == cfg["action_topic"]
            stamp = 10**12 + times[i] * 10**6
            payload = msg.data
            if numeric:
                if i in ({0, 2, 4, 5, 7, 8, 9, 12} if action else {4, 5, 11}):
                    continue
                names = NAMES[:-1] if action and i == 10 else NAMES
                offset = 0 if action else 1000
                payload = json.dumps({"name": names, "position": [offset + times[i] + j for j in range(len(names))]}).encode()
            w.add_message(channels[topic], stamp, payload, stamp, i)
        w.finish()
    dest = tmp_path / "derived"
    _, evidence = prepare(raw, dest, cfg)
    file = dest / "sensors/robot.mcap"
    with file.open("rb") as stream:
        rows = {(c.topic, m.sequence): json.loads(m.data) for _, c, m in make_reader(stream).iter_messages()}
    for topic, missing, offset in (("action", [2, 4, 5], 0), ("observation.state", [4, 5, 11], 1000)):
        for i in missing:
            row = rows[native_topic(topic), i]
            assert row["validity"][0] is enabled
            if enabled:
                assert row["values"][0] == pytest.approx(offset + times[i])
                assert row["source_timestamp_ns"] is None
                assert rows["/metadata/source", i]["short_gap_interpolation"][topic][NAMES[0]]["source"] == "linear_interpolation"
    for i in (0, 7, 8, 9, 12):
        assert not any(rows["/action", i]["validity"])
    for i in (1, 3, 6, 10, 11):
        assert rows["/action", i]["values"][0] == times[i]
    assert not rows["/action", 10]["validity"][-1]  # Four missing frames on this axis.
    assert rows["/openarm/capture_validity", 4]["valid"] is enabled
    gaps = interior_action_gaps([file], 0, 10**12)
    assert [g["frame"] for g in gaps] == ([7, 8, 9] if enabled else [2, 4, 5, 7, 8, 9])
    if enabled:
        assert evidence["short_gap_interpolation"]["filled_frames"] == {"state": 3, "action": 3}


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("extra_ns", [0, 1])
def test_normalized_recorded_action_precedes_interpolation(tmp_path, direction, extra_ns):
    from .test_mcap_v4 import JOINTS, config, fixture

    cfg = config().model_dump(mode="json")
    cfg["maximum_time_error_ms"] = 20  # Previously saved HC TJ scheme.
    cfg["postprocessing_rules"] = {"version": 2, "rules": [
        {"kind": "interpolate_short_gaps", "enabled": True},
        {"kind": "fill_action_from_next_frame", "enabled": True},
    ]}
    times = [100, 200, 300, 400]
    commands = [100, 200 + direction * 23, 300 + direction * 25, 400]
    raw = fixture(tmp_path / "raw.mcap", [(t, list(JOINTS)) for t in commands],
                  state_stamps=times, command_offset_ns=direction * extra_ns)
    dest = tmp_path / "derived"
    _, evidence = prepare(raw, dest, cfg)
    with (dest / "sensors/robot.mcap").open("rb") as src:
        rows = [json.loads(m.data) for _, _, m in make_reader(src).iter_messages(topics=["/action"])]
    for i in ([1] if extra_ns else [1, 2]):
        assert rows[i]["source_timestamp_ns"] == 10**12 + commands[i] * 10**6 + direction * extra_ns
        assert rows[i]["values"][0] == pytest.approx(commands[i] / 1000)
        assert "source_origins" not in rows[i]
        assert rows[i].get("action_origin") != "linear_interpolation"
    if extra_ns:
        assert rows[2]["source_timestamp_ns"] is None
        assert rows[2]["action_origin"] == "linear_interpolation"
    assert evidence["short_gap_interpolation"]["filled_frames"]["action"] == extra_ns


def test_normalized_scheme_interpolates_interior_alongside_edge_labels(tmp_path):
    from .test_mcap_v4 import JOINTS, config, fixture

    cfg = config().model_dump(mode="json")
    cfg["postprocessing_rules"] = {"version": 2, "rules": [
        {"kind": "interpolate_short_gaps", "enabled": True},
        {"kind": "fill_action_from_next_frame", "enabled": True},
    ]}
    times = [50, 100, 160, 200, 300, 350, 400, 450, 500, 550]
    raw = fixture(tmp_path / "raw.mcap", [(t, list(JOINTS)) for t in (100, 300, 500)], state_stamps=times)
    dest = tmp_path / "derived"
    _, evidence = prepare(raw, dest, cfg)
    with (dest / "sensors/robot.mcap").open("rb") as src:
        rows = [json.loads(m.data) for _, _, m in make_reader(src).iter_messages(topics=["/action"])]
    assert rows[0]["action_origin"] == "next_frame_state"
    assert rows[-1]["action_origin"] == "terminal_state"
    for i in (2, 3):
        assert rows[i]["action_origin"] == "linear_interpolation"
        assert rows[i]["values"][0] == pytest.approx(times[i] / 1000)
    assert all(not any(rows[i]["validity"]) for i in (5, 6, 7))
    assert evidence["short_gap_interpolation"]["filled_frames"] == {"state": 0, "action": 2}
