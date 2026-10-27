"""Task deletion distinguishes owned local files, GC TTLs, and real protection."""

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from hc_data_platform.collection_tasks.deletion_local import TaskLocalArtifacts
from hc_data_platform.collection_tasks.deletion_plan import (
    _assert_idle_unprotected,
    registered_locators,
)
from hc_data_platform.core.errors import ProblemException


def alignment_row(root, rollout="rollout", attempt="attempt", payload=b"arrow"):
    def sha(value):
        return hashlib.sha256(value.encode()).hexdigest()
    content = hashlib.sha256(payload).hexdigest()
    path = root / sha(rollout) / sha(attempt) / (content + ".arrow")
    return {
        "rollout_id": rollout, "attempt_id": attempt,
        "content_sha256": content, "staging_uri": path.as_uri(), "status": "READY",
    }, path


def test_local_alignment_is_owned_exactly_and_cleanup_is_idempotent(tmp_path):
    local = TaskLocalArtifacts(tmp_path)
    row, path = alignment_row(tmp_path)
    _, keep = alignment_row(tmp_path, rollout="other-rollout")
    for file in (path, keep):
        file.parent.mkdir(parents=True)
        file.write_bytes(b"arrow")
    assert local.locator("public.aligned_fragment_attempts", row) == str(path)
    assert registered_locators("public.aligned_fragment_attempts", row, "bucket",
                               local_artifacts=local) == (set(), set())
    assert local.size(str(path)) == 5
    local.purge([str(path)])
    local.purge([str(path)])
    assert local.size(str(path)) is None
    assert keep.read_bytes() == b"arrow"


@pytest.mark.parametrize("change", [
    {"rollout_id": "other"}, {"attempt_id": "other"}, {"content_sha256": "b" * 64},
    {"staging_uri": "file:///etc/passwd"}, {"staging_uri": "file://another-host/tmp/a"},
])
def test_local_file_must_match_persisted_attempt_identity(tmp_path, change):
    row, _ = alignment_row(tmp_path)
    with pytest.raises(ProblemException) as error:
        registered_locators("public.aligned_fragment_attempts", {**row, **change}, "bucket",
                            local_artifacts=TaskLocalArtifacts(tmp_path))
    assert error.value.problem.code == "TASK_DELETE_INVALID_LOCATOR"


@pytest.mark.parametrize("parent_link", [False, True])
def test_local_cleanup_rejects_symlinks_without_touching_target(tmp_path, parent_link):
    root = tmp_path / "staging"
    root.mkdir()
    _, path = alignment_row(root)
    target = tmp_path / "outside"
    target.mkdir()
    keep = target / path.name
    keep.write_bytes(b"keep")
    if parent_link:
        path.parent.parent.mkdir()
        path.parent.symlink_to(target, target_is_directory=True)
    else:
        path.parent.mkdir(parents=True)
        path.symlink_to(keep)
    local = TaskLocalArtifacts(root)
    for operation in (lambda: local.size(str(path)), lambda: local.purge([str(path)])):
        with pytest.raises(ProblemException):
            operation()
    assert keep.read_bytes() == b"keep"


@pytest.mark.parametrize("uri", ["s3://external-bucket/a/file", "https://external/a", "file:///a"])
def test_local_support_does_not_authorize_external_exports(tmp_path, uri):
    with pytest.raises(ProblemException) as error:
        registered_locators("publishing.export_attempts", {"staging_uri": uri}, "bucket",
                            local_artifacts=TaskLocalArtifacts(tmp_path))
    assert error.value.problem.code == "TASK_DELETE_EXTERNAL_STORAGE"


FUTURE = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()


def test_default_sampling_gc_ttl_does_not_prevent_explicit_task_purge():
    _assert_idle_unprotected("annotation.frame_selection_manifests", {
        "retention_policy": "TASK_DATASET_90D", "retention_until": FUTURE,
        "legal_hold": False, "governance_hold": False,
    })


@pytest.mark.parametrize("table,change,code", [
    ("annotation.frame_selection_manifests", {"legal_hold": True}, "TASK_DELETE_PROTECTED"),
    ("annotation.frame_selection_manifests", {"governance_hold": True}, "TASK_DELETE_PROTECTED"),
    ("annotation.frame_selection_manifests", {"retention_policy": "PROJECT_OVERRIDE"},
     "TASK_DELETE_PROTECTED"),
    ("storage.managed_objects", {}, "TASK_DELETE_PROTECTED"),
    ("annotation.frame_selection_manifests", {"lease_expires_at": FUTURE}, "TASK_DELETE_BUSY"),
    ("annotation.frame_selection_manifests", {"claimed_until": FUTURE}, "TASK_DELETE_BUSY"),
    ("public.aligned_fragment_attempts", {"retention_until": None, "status": "WRITING"},
     "TASK_DELETE_BUSY"),
])
def test_real_holds_retention_and_active_writers_still_block(table, change, code):
    row = {"retention_policy": "TASK_DATASET_90D", "retention_until": FUTURE, **change}
    with pytest.raises(ProblemException) as error:
        _assert_idle_unprotected(table, row)
    assert error.value.problem.code == code
