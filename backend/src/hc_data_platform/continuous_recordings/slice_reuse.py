"""Reuse confirmed intervals only for the same raw member and recording clock."""

import hashlib
import json

from .models import (
    ConfirmedSliceSource,
    ContinuousRecording,
    RecordingSliceRevision,
    RecordingStatus,
    SliceRevisionStatus,
)


def eligible(recording):
    return (
        recording.status is RecordingStatus.READY_FOR_SLICING
        and recording.current_revision == 0
        and recording.finalized_revision is None
        and recording.quality_summary is not None
        and recording.quality_summary.status == "PASS"
        and recording.recording_upload_id is not None
    )


def _clock(storage, asset):
    if not storage or not asset:
        return None
    key, digest, size = asset
    if not 0 < size <= 16 * 1024**2 or storage.head(key) is None:
        return None
    body = bytearray()
    for chunk in storage.read_chunks(key):
        if len(body) + len(chunk) > size:
            return None
        body.extend(chunk)
    if len(body) != size or hashlib.sha256(body).hexdigest() != digest:
        return None
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    clock, origin, duration = (data.get(k) for k in ("clock", "source_origin_ns", "duration_ns"))
    if (
        clock not in {"ros_header_stamp", "mcap_log_time"}
        or type(origin) is not int
        or type(duration) is not int
        or duration <= 0
    ):
        return None
    return clock, origin, duration


def previous_confirmed(cursor, recording, storage=None):
    if not eligible(recording):
        return None
    scope = (
        recording.scope.organization_id,
        recording.scope.project_id,
        recording.scope.region_code,
    )
    cursor.execute(
        """SELECT r.recording_upload_id, r.recording_document, v.revision_document,
               ARRAY(SELECT p.episode_id FROM ingest.recording_episode_processing p
                 WHERE (p.organization_id,p.project_id,p.region_code,
                        p.recording_id,p.finalized_revision)
                    =(r.organization_id,r.project_id,r.region_code,
                      r.recording_id,r.finalized_revision)
                 AND p.status='DISCARDED')
           FROM ingest.mcap_processing_files target
           JOIN ingest.mcap_processing_files prev USING
             (organization_id,project_id,region_code,raw_source_id,source_path,
              collection_task_id,robot_id,dataset_id)
           JOIN ingest.continuous_recordings r ON
             (r.organization_id,r.project_id,r.region_code,r.recording_id)=
             (prev.organization_id,prev.project_id,prev.region_code,prev.recording_id)
           JOIN ingest.recording_slice_revisions v ON
             (v.organization_id,v.project_id,v.region_code,v.recording_id,v.revision)=
             (r.organization_id,r.project_id,r.region_code,r.recording_id,r.finalized_revision)
           WHERE (target.organization_id,target.project_id,target.region_code,
                  target.recording_id)=(%s,%s,%s,%s)
             AND target.config_reference->'reprocessing'->>'raw_source_id'=target.raw_source_id
             AND target.status='READY' AND prev.status='READY'
             AND prev.created_at < target.created_at
             AND prev.source_document->>'sha256'=target.source_document->>'sha256'
             AND r.status='SLICED' AND v.status='FINALIZED'
           ORDER BY prev.created_at DESC, prev.processing_id LIMIT 1""",
        (*scope, recording.recording_id),
    )
    row = cursor.fetchone()
    if not row:
        return None
    old_upload, document, revision_doc, discarded = row
    old = ContinuousRecording.model_validate(document)
    if (old.capture_started_at, old.capture_ended_at, old.duration_ns) != (
        recording.capture_started_at,
        recording.capture_ended_at,
        recording.duration_ns,
    ):
        return None
    cursor.execute(
        """SELECT upload_id,asset_path,object_key,expected_sha256,expected_size
           FROM ingest.recording_upload_assets
           WHERE (organization_id,project_id,region_code)=(%s,%s,%s) AND upload_id IN (%s,%s)
           AND status='COMMITTED'
             AND asset_path IN ('source/frames.jsonl','mcap-provenance.json')""",
        (*scope, old_upload, recording.recording_upload_id),
    )
    assets = {
        (upload, path): (key, sha, size) for upload, path, key, sha, size in cursor.fetchall()
    }
    uploads = (old_upload, recording.recording_upload_id)
    traces = [assets.get((uid, "source/frames.jsonl")) for uid in uploads]
    if not (all(traces) and traces[0][1:] == traces[1][1:]):
        # Validity or camera metadata can change while the interval clock stays identical.
        clocks = [_clock(storage, assets.get((uid, "mcap-provenance.json"))) for uid in uploads]
        if clocks[0] is None or clocks[0] != clocks[1]:
            return None
    revision = RecordingSliceRevision.model_validate(revision_doc)
    excluded = set(discarded)
    slices = tuple(ep for ep in revision.slices if ep.episode_id not in excluded)
    if not slices or any(
        not 0 <= ep.start_offset_ns < ep.end_offset_ns <= recording.duration_ns for ep in slices
    ):
        return None
    return revision.model_copy(update={"slices": slices})


def source_reference(previous):
    return ConfirmedSliceSource(
        recording_id=previous.recording_id,
        revision=previous.revision,
        episode_count=len(previous.slices),
    )


def finalize_previous(cursor, recording, *, storage, actor_id, request_id):
    """The target row is locked. Register new windows and dispatch in this transaction."""
    from hc_data_platform.ingest.models import utc_now

    from .repository import PostgresContinuousRecordingRepository

    previous = previous_confirmed(cursor, recording, storage)
    if previous is None:
        return None
    # Revision zero is part of eligibility. Only the approved editorial information
    # crosses generations: every source reference and workflow belongs to the new upload.
    now = utc_now()
    slices = tuple(
        ep.model_copy(
            update={
                "scope": recording.scope,
                "recording_id": recording.recording_id,
                "revision": 1,
                "ordinal": i,
                "source_sha256": recording.source_sha256,
                "source_upload_session_id": recording.upload_session_id,
                "source_recording_upload_id": recording.recording_upload_id,
            }
        )
        for i, ep in enumerate(previous.slices)
    )
    revision = RecordingSliceRevision(
        scope=recording.scope,
        recording_id=recording.recording_id,
        revision=1,
        status=SliceRevisionStatus.FINALIZED,
        slices=slices,
        reused_from=source_reference(previous),
        authoring_mode=previous.authoring_mode,
        model=previous.model,
        created_by=actor_id,
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
    PostgresContinuousRecordingRepository.write_revision(
        cursor,
        recording=updated,
        revision=revision,
        expected_version=0,
        actor_id=actor_id,
        request_id=request_id,
    )
    return updated, revision
