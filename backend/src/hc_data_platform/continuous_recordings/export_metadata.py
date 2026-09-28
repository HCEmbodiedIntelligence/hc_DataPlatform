"""Resolve a published continuous Episode's immutable slice and source receipt."""

import hashlib
import json

from hc_data_platform.publishing.export_assets import export_error


def frozen_metadata(cursor, storage, organization, region, manifest, rollout):
    cursor.execute(
        """SELECT slice.episode_document, upload.upload_document
        FROM ingest.recording_episode_processing episode
        JOIN ingest.continuous_recordings recording
          USING (organization_id, project_id, region_code, recording_id)
        JOIN ingest.recording_episode_slices slice
          ON slice.organization_id=episode.organization_id
         AND slice.project_id=episode.project_id AND slice.region_code=episode.region_code
         AND slice.recording_id=episode.recording_id AND slice.episode_id=episode.episode_id
         AND slice.revision=episode.finalized_revision
        JOIN ingest.recording_uploads upload
          ON upload.organization_id=recording.organization_id
         AND upload.project_id=recording.project_id AND upload.region_code=recording.region_code
         AND upload.upload_id=recording.recording_upload_id
        JOIN ingest.recording_episode_qc_reports qc
          ON qc.organization_id=episode.organization_id AND qc.project_id=episode.project_id
         AND qc.region_code=episode.region_code AND qc.report_id=episode.qc_report_id
        WHERE episode.organization_id=%s AND episode.project_id=%s AND episode.region_code=%s
          AND episode.recording_id || ':' || episode.episode_id=%s
          AND qc.report_document->>'source_sha256'=%s AND episode.status='READY'""",
        (organization, manifest.project_id, region, rollout.rollout_id, rollout.source_mcap_sha256),
    )
    row = cursor.fetchone()
    if row is None:
        return {}
    episode, upload = row
    command = upload["command"]
    result = {
        "task": episode.get("task_label") or episode.get("title") or "Robot demonstration",
        "source_episode": episode,
        "recording_upload": command,
    }
    from hc_data_platform.publishing.holobrain_depth import DEPTH_INFO

    result["source_features"] = {
        camera["topic"]: {
            "dtype": "video",
            "shape": [camera["height"], camera["width"], 1],
            "names": ["height", "width", "channels"],
            "info": dict(DEPTH_INFO),
        }
        for camera in command["recording_config"]["cameras"]
        if camera.get("topic", "").startswith("observation.images.")
        and camera["topic"].endswith("_depth")
        and camera["codec"] == "hevc"
    }
    cursor.execute(
        """SELECT asset_document FROM ingest.recording_upload_assets
        WHERE organization_id=%s AND project_id=%s AND region_code=%s AND upload_id=%s
          AND asset_path='capture-provenance.json' AND status='COMMITTED'""",
        (organization, manifest.project_id, region, upload["upload_id"]),
    )
    found = cursor.fetchone()
    if found:
        asset = found[0]
        if asset["manifest"]["size"] > 16 * 1024 * 1024:
            raise export_error("EXPORT_SOURCE_HASH_MISMATCH", "Capture provenance exceeds limit.")
        content = bytearray()
        for chunk in storage.read_chunks(asset["object_key"]):
            content.extend(chunk)
            if len(content) > asset["manifest"]["size"]:
                raise export_error(
                    "EXPORT_SOURCE_HASH_MISMATCH", "Capture provenance size changed."
                )
        if (
            len(content) != asset["manifest"]["size"]
            or hashlib.sha256(content).hexdigest() != asset["manifest"]["sha256"]
        ):
            raise export_error("EXPORT_SOURCE_HASH_MISMATCH", "Capture provenance hash changed.")
        provenance = json.loads(content)
        result.update(
            capture_context=provenance["capture_context"],
            robot_type=provenance["capture_context"]["profile"]["robot_type"],
        )
    return result
