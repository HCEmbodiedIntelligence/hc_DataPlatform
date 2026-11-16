"""Match an Episode to the immutable inputs of a successful whole-recording scan."""

from contextlib import closing

from hc_data_platform.security.audit import canonical_hash


def scan_fingerprint(request):
    source = request.projection
    return canonical_hash(
        {
            "scan_version": 2,
            "recording_upload_id": source.recording_upload_id,
            "manifest": source.manifest_fingerprint,
            "start_offset_ns": request.start_offset_ns,
            "end_offset_ns": request.end_offset_ns,
            "started_at": source.started_at.isoformat(),
            "ended_at": source.ended_at.isoformat(),
            "assets": [asset.model_dump(mode="json") for asset in request.assets],
            "recording_config": request.recording_config.model_dump(mode="json"),
            "capture_snapshot": request.capture_snapshot,
            "quality_profile": request.quality_profile.content_sha256(),
            "frequency_hz": request.alignment_profile.frequency_hz,
            "required_modalities": sorted(request.alignment_profile.required_modalities),
            "storage_format": request.storage_format,
        }
    )


def matching_recording_report(request, connection_factory):
    source = request.projection
    if request.start_offset_ns != 0 or request.storage_format != "lerobot_v3":
        return None
    with closing(connection_factory()) as connection, connection.cursor() as cursor:
        cursor.execute(
            """SELECT q.report_id, q.report_document
               FROM ingest.continuous_recordings r
               JOIN ingest.recording_quality_reports q
                 USING (organization_id, project_id, region_code, recording_id)
               WHERE (r.organization_id, r.project_id, r.region_code, r.recording_id)
                     = (%s, %s, %s, %s)
                 AND r.recording_upload_id = %s AND r.duration_ns = %s
                 AND q.report_id = r.recording_document->'quality_summary'->>'report_id'""",
            (
                source.organization_id,
                source.project_id,
                source.region_code,
                source.recording_id,
                source.recording_upload_id,
                request.end_offset_ns,
            ),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    report_id, document = row
    if (
        document.get("schema_version") == "continuous-recording-qc/v1"
        and document.get("status") == "PASS"
        and document.get("scan_fingerprint") == scan_fingerprint(request)
        and report_id == "recording_qc_" + canonical_hash(document)
    ):
        return document
    return None
