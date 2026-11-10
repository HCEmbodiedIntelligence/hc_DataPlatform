"""Frozen whole-Episode results, independent of Tags and source bytes."""
import copy
import json
from types import SimpleNamespace
from dataclasses import replace

import pytest

from hc_data_platform.annotation.models import EpisodeOutcome
from hc_data_platform.annotation.revision_models import AnnotationRevisionFile, RevisionFileDownload
from hc_data_platform.annotation.validation import revision_content_hash
from hc_data_platform.episode_store.episode_outcome import selection_outcome
from hc_data_platform.episode_store.models import canonical, digest
from tests.annotation.test_subtasks import snapshot  # noqa: F401


def frozen_result(snapshot, data, outcome):
    document, _, track = copy.deepcopy(snapshot)
    document["data_binding"].update(data_ref=data.model_dump(mode="json"), base_step_count=data.frame_count)
    document["revision"].update(data_ref=data.model_dump(mode="json"), tags=[], episode_outcome=outcome)
    document["revision"]["content_hash"] = revision_content_hash(
        base_lance_version=None, data_ref=data,
        tag_schema_id=document["revision"]["tag_schema_id"],
        tag_schema_version=document["revision"]["tag_schema_version"],
        tags=(), operations=(), episode_outcome=EpisodeOutcome(outcome),
    )
    body = canonical(document)
    receipt = AnnotationRevisionFile(
        task_id=document["revision"]["task_id"], revision=document["revision"]["revision"],
        revision_content_hash=document["revision"]["content_hash"],
        status="READY", generation=1, attempts=1,
        content_sha256=digest(document), size_bytes=len(body),
    )
    return RevisionFileDownload(body, receipt, False, None, None), track.model_copy(update={"annotation_ids": ()})


@pytest.mark.parametrize("outcome", ["success", "fail"])
def test_result_export_verifies_selected_snapshot(snapshot, outcome):
    data = snapshot[1]
    annotation, track = frozen_result(snapshot, data, outcome)
    selection = SimpleNamespace(package=SimpleNamespace(receipt=SimpleNamespace(data=data)),
                                annotation_file=annotation, track=track)
    assert selection_outcome(selection) == {"episode_outcome": outcome}
    document = json.loads(annotation.content)
    document["revision"]["episode_outcome"] = "fail" if outcome != "fail" else "success"
    selection.annotation_file = replace(annotation, content=canonical(document))
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        selection_outcome(selection)
    selection.annotation_file = replace(annotation, file=annotation.file.model_copy(update={"revision": 999}))
    with pytest.raises(ValueError, match="REVISION_MISMATCH"):
        selection_outcome(selection)
