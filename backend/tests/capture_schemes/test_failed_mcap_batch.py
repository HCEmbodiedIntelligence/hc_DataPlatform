from types import SimpleNamespace

import psycopg
import pytest

from hc_data_platform.capture_schemes.failed_mcap import candidates
from hc_data_platform.capture_schemes.reprocess import ReprocessMcap, request
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.lerobot_imports.mcap_processing import record_rejection

from .test_transactions import environment  # noqa: F401
from .test_versioned_reprocess import seed


def test_selected_failures_only_and_stale_requests(environment):
    dsn, scope, task, raw, manifest, original = seed(environment)
    with psycopg.connect(dsn) as db:
        more = {"files": [{"path": path, "size": 100, "sha256": "b"*64}
                          for path in ("second.mcap", "ready.mcap")]}
        record_rejection(db, scope, raw, more, SimpleNamespace(collection_task_id=task, robot_id="robot-1"),
                         {**original, "error": "MCAP_JOINT_LAYOUT_MISMATCH"})
        manifest["files"].extend(more["files"])
        db.execute("UPDATE ingest.mcap_processing_files SET status='READY' WHERE raw_source_id=%s AND source_path='ready.mcap'",
                   (raw.raw_source_id,))
        db.commit()
        choices = candidates(db, scope, task)
        assert [f["source_path"] for f in choices] == ["raw.mcap", "second.mcap"]
        choice = choices[1]
        selection = {"collection_task_id": task, "processing_ids": [choice["processing_id"]]}
        cmd = ReprocessMcap(request_id="batch-1", plan_revision=2, failed_selection=selection)
        with pytest.raises(ProblemException):
            request(db, scope, raw, manifest, cmd.model_copy(update={"plan_revision": 1}), "test")
        reply = request(db, scope, raw, manifest, cmd, "test")
        assert reply["plan_revision"] == 2
        assert [f["source"]["path"] for f in reply["files"]] == ["second.mcap"]
        assert request(db, scope, raw, manifest, cmd, "test") == reply
        assert candidates(db, scope, task) == []
        db.execute("UPDATE ingest.mcap_processing_files SET status='READY' WHERE processing_id=%s",
                   (reply["files"][0]["processing_id"],))
        assert [f["source_path"] for f in candidates(db, scope, task)] == ["raw.mcap"]
        with pytest.raises(ProblemException):
            request(db, scope, raw, manifest, cmd.model_copy(update={"request_id": "stale"}), "test")
