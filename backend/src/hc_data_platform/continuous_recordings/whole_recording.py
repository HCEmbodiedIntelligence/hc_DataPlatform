"""Commit one full recording before any human interval authoring."""

from hc_data_platform.ingest.models import utc_now

from .models import (
    EpisodeSlice,
    RecordingSliceRevision,
    RecordingStatus,
    SliceAuthoringMode,
    SliceRevisionStatus,
)

WHOLE_EPISODE_ID = "episode_full_recording"


def whole_recording_revision(recording):
    # Never replace a draft, a historical finalized selection, or rejected data.
    if not (
        recording.ingest_mode == "WHOLE_RECORDING"
        and recording.status is RecordingStatus.READY_FOR_SLICING
        and recording.current_revision == 0
        and recording.finalized_revision is None
        and recording.quality_summary is not None
        and recording.quality_summary.status == "PASS"
        and recording.recording_upload_id is not None
    ):
        return None
    now = utc_now()
    episode = EpisodeSlice(
        scope=recording.scope,
        recording_id=recording.recording_id,
        revision=1,
        ordinal=0,
        episode_id=WHOLE_EPISODE_ID,
        start_offset_ns=0,
        end_offset_ns=recording.duration_ns,
        started_at=recording.capture_started_at,
        ended_at=recording.capture_ended_at,
        title="完整录制",
        source_sha256=recording.source_sha256,
        source_recording_upload_id=recording.recording_upload_id,
    )
    revision = RecordingSliceRevision(
        scope=recording.scope,
        recording_id=recording.recording_id,
        revision=1,
        status=SliceRevisionStatus.FINALIZED,
        slices=(episode,),
        authoring_mode=SliceAuthoringMode.SYSTEM,
        created_by="recording-ingest-worker",
        created_at=now,
    )
    updated = recording.model_copy(
        update={
            "status": RecordingStatus.SLICED,
            "current_revision": 1,
            "finalized_revision": 1,
            "etag": '"v2"',
            "updated_at": now,
        }
    )
    return updated, revision


def enqueue_whole_recording(cursor, recording, *, request_id):
    # The caller holds the recording row lock. Revision + processing + outbox
    # share one transaction; a replay after commit observes a finalized source.
    from .repository import PostgresContinuousRecordingRepository

    prepared = whole_recording_revision(recording)
    if prepared is None:
        return None
    updated, revision = prepared
    PostgresContinuousRecordingRepository.write_revision(
        cursor,
        recording=updated,
        revision=revision,
        expected_version=0,
        actor_id=revision.created_by,
        request_id=request_id,
    )
    return prepared
