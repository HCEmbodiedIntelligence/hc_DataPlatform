"""Gate MCAP admission on the verified original bytes across upload locations."""

from hc_data_platform.lerobot_imports.mcap_original import ORIGINAL_PATH
from hc_data_platform.security.audit import canonical_hash


def check_original_mcap(cursor, recording, document):
    if recording.recording_upload_id is None:
        return
    scope = recording.scope
    tenant = (scope.organization_id, scope.project_id, scope.region_code)
    cursor.execute(
        """SELECT expected_sha256 FROM ingest.recording_upload_assets
           WHERE (organization_id,project_id,region_code,upload_id)=(%s,%s,%s,%s)
             AND asset_path=%s AND status='COMMITTED'""",
        (*tenant, recording.recording_upload_id, ORIGINAL_PATH),
    )
    original = cursor.fetchone()
    if original is None:
        return

    sha256 = original[0]
    # Under READ COMMITTED the query after this lock sees the winner's verdict.
    # Hold it until the caller commits the quality report and recording status.
    cursor.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("recording-content:" + canonical_hash([*tenant, sha256]),),
    )
    cursor.execute(
        """SELECT r.recording_id
           FROM ingest.recording_upload_assets a
           JOIN ingest.continuous_recordings r
             ON (r.organization_id,r.project_id,r.region_code,r.recording_upload_id)
              = (a.organization_id,a.project_id,a.region_code,a.upload_id)
           WHERE (a.organization_id,a.project_id,a.region_code)=(%s,%s,%s)
             AND a.asset_path=%s AND a.expected_sha256=%s AND a.status='COMMITTED'
             AND r.recording_id<>%s AND r.superseded_by_processing_id IS NULL
             AND r.status IN ('READY_FOR_SLICING','SLICED')
             -- A new processing generation of the same Raw member is a replacement.
             AND NOT EXISTS (
               SELECT 1 FROM ingest.mcap_processing_files prior
               JOIN ingest.mcap_processing_files current_file
                 USING (organization_id,project_id,region_code,raw_source_id,source_path)
               WHERE (prior.organization_id,prior.project_id,prior.region_code,prior.recording_id)
                   = (r.organization_id,r.project_id,r.region_code,r.recording_id)
                 AND current_file.recording_id=%s)
           ORDER BY r.created_at,r.recording_id LIMIT 1""",
        (*tenant, ORIGINAL_PATH, sha256, recording.recording_id, recording.recording_id),
    )
    match = cursor.fetchone()
    if match:
        document["status"] = "REJECT"
        document["finding_codes"] = sorted({*document["finding_codes"], "MCAP_DUPLICATE_RECORDING"})
        document["duplicate_of_recording_id"] = match[0]
        document["original_mcap_sha256"] = sha256
        document.pop("scan_fingerprint", None)
