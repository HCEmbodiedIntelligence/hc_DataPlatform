"""Consume optional recorder targets, preserving actual commands and all other QC."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from hc_data_platform.continuous_recordings.openarm_raw.config import validate
from hc_data_platform.continuous_recordings.openarm_raw.prepare import load_index, rows

FIXTURE = Path(__file__).parents[1] / "fixtures/openarm-raw-v1"


@pytest.fixture
def recording():
    cfg = validate(json.loads((FIXTURE / "session.json").read_text())["config"])
    manifest = json.loads((FIXTURE / "manifest.json").read_text())
    cfg["alignment"]["derive_action_from_next_state"] = True
    with sqlite3.connect(":memory:") as db:
        load_index(FIXTURE, db)
        start, end = manifest["t0_ns"], manifest["end_ns"]
        before = list(rows(db, cfg, start, end))
        sources = {ident: s for ident, s in cfg["sources"].items() if s["kind"] == "action"}
        for current, following in zip(before, before[1:], strict=False):
            by_name = {a["name"]: {key: values[i] for key, values in
                following["state"][ident]["components"].items()}
                for ident, s in cfg["sources"].items() if s["kind"] == "state"
                for i, a in enumerate(s["named_axes"])}
            for ident, source in sources.items():
                axes = source["named_axes"]
                values = [by_name[a["name"]]["values"] for a in axes]
                valid = [by_name[a["name"]]["valid"] for a in axes]
                data = {"schema_version": "recorder-next-state/v1", "source_id": ident,
                    "action_origin": "next_state", "action_stage": "derived",
                    "selection": "only_if_no_real_source_in_recording",
                    "target_frame_index": current["target_frame_index"],
                    "target_time_ns": current["target_time_ns"],
                    "capture_time_ns": current.get("association_time_ns", current["target_time_ns"]),
                    "clock_epoch": current["clock_epoch"],
                    "next_frame_index": following["target_frame_index"],
                    "next_target_time_ns": following["target_time_ns"],
                    "next_state_time_ns": following.get("association_time_ns", following["target_time_ns"]),
                    "joint_names": [a["name"] for a in axes], "joint_units": [a["unit"] for a in axes],
                    "components": {"values": values, "valid": valid,
                        "source_ns": [following["target_time_ns"]]*len(axes),
                        "evidence": [{} for _ in axes]}}
                db.execute("INSERT INTO action_targets VALUES(?,?,?)",
                           (ident, current["target_frame_index"], json.dumps(data)))
        original = db.execute("SELECT * FROM raw_samples WHERE source IN (%s)" %
                             ",".join("?" for _ in sources), list(sources)).fetchall()
        for ident in sources:
            db.execute("DELETE FROM raw_samples WHERE source=?", (ident,))
        yield db, cfg, start, end, before, original


def test_uses_saved_targets_without_generating_on_platform_and_keeps_last_invalid(recording):
    db, cfg, start, end, before, _ = recording
    result = list(rows(db, cfg, start, end))
    assert any(r["export_qualified"] for r in result[:-1])
    for current, following in zip(result, before[1:], strict=False):
        for ident, action in current["action"].items():
            expected = {a["name"]: following["state"][s]["components"]["values"][i]
                        for s, spec in cfg["sources"].items() if spec["kind"] == "state"
                        for i, a in enumerate(spec["named_axes"])}
            assert action["action_origin"] == "next_state"
            assert action["components"]["values"] == [expected[a["name"]]
                                                      for a in cfg["sources"][ident]["named_axes"]]
    assert not result[-1]["export_qualified"]
    db.execute("DELETE FROM action_targets")
    assert all(not r["export_qualified"] for r in rows(db, cfg, start, end))


def test_disabled_ignores_candidates_and_legacy_hash_stays_stable(recording):
    db, cfg, start, end, _, _ = recording
    cfg["alignment"].pop("derive_action_from_next_state")
    assert all(not r["export_qualified"] for r in rows(db, cfg, start, end))
    for r in rows(db, cfg, start, end):
        assert all("action_origin" not in a for a in r["action"].values())


def test_any_real_action_in_recording_wins_even_if_late_or_invalid(recording):
    db, cfg, start, end, before, original = recording
    # A late invalidation still forbids using earlier candidates for this source.
    source, seq, stamp, body = original[-1]
    data = json.loads(body)
    data.update(valid=False, validity_known=False)
    db.execute("INSERT INTO raw_samples VALUES(?,?,?,?)", (source, seq, stamp, json.dumps(data)))
    result = list(rows(db, cfg, start, end))
    assert all("action_origin" not in r["action"][source] for r in result)
    assert all(not r["export_qualified"] for r in result)


@pytest.mark.parametrize("field,value", [("capture_time_ns", 1), ("clock_epoch", 99),
    ("joint_units", ["wrong"]), ("next_frame_index", 999), ("action_stage", "sent")])
def test_mismatched_recorded_target_cannot_resolve_missing_action(recording, field, value):
    db, cfg, start, end, _, _ = recording
    for ident, k, body in db.execute("SELECT * FROM action_targets").fetchall():
        data = json.loads(body)
        data[field] = value
        db.execute("UPDATE action_targets SET body=? WHERE source=? AND k=?",
                   (json.dumps(data), ident, k))
    result = list(rows(db, cfg, start, end))
    assert all(not r["export_qualified"] for r in result)
    assert any("recorded_action_target_mismatch" in reason
               for r in result for reason in r["invalid_reasons"])
