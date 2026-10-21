"""Resume robot Raw preparation through the browser's existing processing channel."""

from uuid import NAMESPACE_URL, uuid5

from hc_data_platform.robot_ingest import processing_store, recording_bridge
from hc_data_platform.robot_ingest.models import RobotIngestUpload
from hc_data_platform.robot_ingest.processing_dispatch import mcap_eligible, retry_mcap


def retry_recording_source(organization_id, project_id, region_code, import_id, *, factory):
    with factory() as db:
        row = db.execute(
            """SELECT u.upload_document,j.updated_at FROM ingest.raw_sources s
            JOIN ingest.robot_ingest_uploads u
              ON (u.organization_id,u.project_id,u.region_code,u.raw_source_id)
               = (s.organization_id,s.project_id,s.region_code,s.raw_source_id)
            JOIN ingest.raw_ingest_jobs j
              ON (j.organization_id,j.project_id,j.region_code,j.raw_source_id)
               = (s.organization_id,s.project_id,s.region_code,s.raw_source_id)
            WHERE s.organization_id=%s AND s.project_id=%s AND s.region_code=%s
              AND s.raw_source_id=%s AND s.raw_status='COMMITTED'
              AND s.source_format IN ('CAPTURE_BUNDLE','MCAP','LEROBOT_V3')""",
            (organization_id, project_id, region_code, import_id),
        ).fetchone()
    if row is None:
        return False
    upload = RobotIngestUpload.model_validate(row[0])
    if (
        upload.target.organization_id,
        upload.target.project_id,
        upload.target.region_code,
        upload.raw_source_id,
    ) != (organization_id, project_id, region_code, import_id):
        raise ValueError("recording upload receipt differs from its source scope")
    if mcap_eligible(upload):
        retry_mcap(factory, upload)
        return True
    if upload.source_format == "LEROBOT_V3":
        request_id = str(uuid5(NAMESPACE_URL, f"browser-native-retry:{import_id}:{row[1]}"))
        processing_store.ProcessingStore(factory).retry(upload, request_id)
        return True
    if not recording_bridge.eligible(upload):
        return False
    # Concurrent retries of the same failed preparation use the same outbox key.
    request_id = str(uuid5(NAMESPACE_URL, f"browser-recording-retry:{import_id}:{row[1]}"))
    recording_bridge.retry(factory, upload, request_id)
    return True
