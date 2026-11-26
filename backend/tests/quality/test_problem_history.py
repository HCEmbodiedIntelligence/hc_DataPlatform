"""Reprocessing history follows the current source member in the dashboard."""
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

from hc_data_platform.quality.postgres import _recording_problems


def test_history_uses_source_member_and_prefers_current_evidence():
    report = json.loads((Path(__file__).parent / "fixtures/archived-be06-qc-3.json").read_text())["report"]
    document = {"schema_version": "continuous-recording-qc/v1", "status": "REJECT",
                "quality_report": report, "finding_codes": ["MCAP_INTERIOR_ACTION_MISSING"]}
    def row(source, recording, discarded=False):
        return (document, "b" * 64, recording, recording, datetime.now(timezone.utc),
                "task", discarded, "dir/capture.mcap", source)

    def capture(source, stage):
        return SimpleNamespace(source_import_id=source, source_path="dir/capture.mcap",
                               recording_id=source, canonical_episode_id=None, stage=stage,
                               qc_outcome=None)

    rows = [row("a", "old"), row("a", "a", True), row("b", "old-b", True),
            row("c", "old-c"), row("d", "d"), row("alias", "duplicate")]
    captures = [capture("a", "DISCARDED"), capture("b", "READY"),
                capture("c", "PREPARING"), capture("d", "QC_FAILED")]
    result = _recording_problems(rows, captures)
    assert [(r.recording_id, r.progress, r.discarded) for r in result] == [
        ("a", "DISCARDED", True),
        ("old-c", "IN_PROGRESS", False), ("d", "OPEN", False),
    ]
