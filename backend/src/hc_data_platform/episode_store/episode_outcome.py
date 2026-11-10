"""Whole-Episode results from the explicitly selected immutable annotation."""

import hashlib
import json

from hc_data_platform.annotation.models import EpisodeOutcome
from hc_data_platform.annotation.subtasks import verify_snapshot


def outcome_fields(value):
    outcome = EpisodeOutcome(value)
    return {
        "episode_outcome": (
            "fail" if outcome in {EpisodeOutcome.FAILURE, EpisodeOutcome.LEGACY_FAILURE}
            else "success"
        ),
    }



def annotation_outcome(annotation_file, data):
    if annotation_file is None:
        return {}
    receipt = annotation_file.file
    if hashlib.sha256(annotation_file.content).hexdigest() != receipt.content_sha256:
        raise ValueError("EXPORT_ANNOTATION_HASH_MISMATCH")
    saved = verify_snapshot(json.loads(annotation_file.content), data)
    if (saved.task_id != receipt.task_id or saved.revision != receipt.revision
            or saved.content_hash != receipt.revision_content_hash or receipt.status != "READY"):
        raise ValueError("EXPORT_ANNOTATION_REVISION_MISMATCH")
    return outcome_fields(saved.episode_outcome)


def selection_outcome(selection):
    return annotation_outcome(selection.annotation_file, selection.package.receipt.data)
