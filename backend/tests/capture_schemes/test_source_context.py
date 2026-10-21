import copy
import json

import pytest

from hc_data_platform.capture_schemes.common import digest
from hc_data_platform.lerobot_imports.context import read_context, verify_source_context
from hc_data_platform.lerobot_imports.reader import read_episode
from tests.capture_schemes.processing_fixture import make_source, output_for, snapshot


def source(tmp_path):
    root = tmp_path / "source"
    info = make_source(root)
    frozen = snapshot(output_for(info))
    context = {
        "schema_version": "hc-capture-context/v1",
        "recording_id": frozen["recording_id"],
        "capture_snapshot": frozen,
        "feature_presence": {key: True for key in frozen["output"]["features"]},
    }
    (root / "capture-context.json").write_text(json.dumps(context))
    return root, frozen, context


def test_shared_context_reads_actual_rows_without_openarm_completion_marker(tmp_path):
    root, frozen, context = source(tmp_path)
    assert verify_source_context(root, {}) == context
    assert read_context(root, expected_snapshot=frozen) == context
    _, _, episode = read_episode(root, 0, preserve_features=True, capture_snapshot=frozen)
    assert episode.frame_count == 167 and episode.fps == 15
    assert episode.context == context
    assert not (root / "export-complete.json").exists()
    changed = copy.deepcopy(frozen)
    changed["plan_revision"] += 1
    changed["snapshot_sha256"] = digest(
        {k: v for k, v in changed.items() if k != "snapshot_sha256"}
    )
    with pytest.raises(ValueError, match="CAPTURE_VERSION_MISMATCH"):
        read_context(root, expected_snapshot=changed)


@pytest.mark.parametrize("kind", ["hash", "identity", "presence", "unknown"])
def test_client_context_does_not_override_identity_presence_or_contract(tmp_path, kind):
    root, frozen, context = source(tmp_path)
    if kind == "hash":
        context["capture_snapshot"]["plan_revision"] += 1
    elif kind == "identity":
        context["recording_id"] = "other"
    elif kind == "presence":
        context["feature_presence"]["action"] = False
    else:
        context["schema_version"] = "unknown/v1"
    (root / "capture-context.json").write_text(json.dumps(context))
    with pytest.raises(ValueError, match="CAPTURE_CONTEXT_"):
        read_context(root, expected_snapshot=frozen)
