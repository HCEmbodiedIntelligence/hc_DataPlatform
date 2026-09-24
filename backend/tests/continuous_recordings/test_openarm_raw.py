import json
import shutil
from pathlib import Path

import pytest
from mcap.reader import make_reader

from hc_data_platform.continuous_recordings.openarm_raw.prepare import load_index, prepare, rows
from hc_data_platform.robot_ingest.recording_contract import OpenArmRecordingComplete

FIXTURE = Path(__file__).parents[1] / "fixtures/openarm-raw-v1"


def test_original_capture_is_indexed_and_aligned_only_on_platform(tmp_path):
    root = tmp_path / "original"
    shutil.copytree(FIXTURE, root)
    marker = OpenArmRecordingComplete.model_validate_json(
        (root / "recording-complete.json").read_bytes()
    )
    config, roles = prepare(root, tmp_path / "derived", marker.recording_upload)
    assert not list(root.rglob("*.sqlite3")) and not (root / "quality.json").exists()
    assert (tmp_path / "worker-index.sqlite3").exists()
    assert len([c for c in config["cameras"] if c["codec"] == "h264"]) == 3
    with (tmp_path / "derived/sensors/robot.mcap").open("rb") as stream:
        samples = [(c.topic, json.loads(m.data)) for _, c, m in make_reader(stream).iter_messages()]
    states = [s for t, s in samples if t == "/observation/state"]
    actions = [s for t, s in samples if t == "/action"]
    validity = [s for t, s in samples if t == "/openarm/capture_validity"]
    assert len(states) == len(actions) == len(validity) == 21
    assert all(v["valid"] for v in validity[:-1]), validity
    # Stop has no post-cutoff tail: the final 20 Hz gripper bracket is absent.
    assert not validity[-1]["valid"]
    assert any("gripper" in reason for reason in validity[-1]["reasons"])
    for k, (state, action) in enumerate(zip(states, actions, strict=False)):
        # Independent synthetic generator oracle: arm states at 100 Hz;
        # actions hold the most recent actually sent source command.
        assert state["names"] == actions[k]["names"]
        assert state["units"] == ["rad"] * 7 + ["m"] + ["rad"] * 7 + ["m"]
        assert state["values"][0] == pytest.approx(0.8 + k / 30 * 0.1, abs=1e-6)
        assert action["values"][0] == pytest.approx(0.85 + (k * 100 // 30) * 0.001, abs=1e-6)
    for file in FIXTURE.rglob("*"):
        if file.is_file():
            assert (root / file.relative_to(FIXTURE)).read_bytes() == file.read_bytes()


def test_action_invalidation_is_not_repaired_into_valid_training_data(tmp_path):
    import sqlite3

    from hc_data_platform.continuous_recordings.openarm_raw.config import validate

    with sqlite3.connect(tmp_path / "index.sqlite3") as db:
        load_index(FIXTURE, db)
        snapshot = json.loads((FIXTURE / "session.json").read_text())
        manifest = json.loads((FIXTURE / "manifest.json").read_text())
        db.execute(
            "UPDATE raw_samples SET body=json_set(body,'$.validity_known',json('false')) WHERE json_extract(body,'$.kind')='action'"
        )
        aligned = list(
            rows(db, validate(snapshot["config"]), manifest["t0_ns"], manifest["end_ns"])
        )
        assert aligned and all(not row["export_qualified"] for row in aligned)


def test_raw_manifest_rejects_robot_index():
    value = json.loads((FIXTURE / "recording-complete.json").read_text())
    asset = dict(value["recording_upload"]["assets"][0], path="index.sqlite3")
    value["recording_upload"]["assets"].append(asset)
    with pytest.raises(ValueError):
        OpenArmRecordingComplete.model_validate(value)
