"""Serialize admission by original MCAP bytes before producing local derivatives."""

from time import sleep

from hc_data_platform.lerobot_imports.mcap_original import ORIGINAL_PATH
from hc_data_platform.security.audit import canonical_hash

from .mcap_reference import materialize, read_receipt, require_version


def verified_original(storage, entry, checkpoint):
    if entry.get("verification_key"):
        sha = read_receipt(storage, entry)
        if sha:
            if entry.get("sha256") not in (None, sha):
                raise ValueError("MCAP object differs from its verified SHA-256")
            require_version(storage, entry["object_key"], entry["size"], entry["source_etag"])
            return {**entry, "sha256": sha}
    return materialize(storage, entry, None, checkpoint)


def existing_recording(lock, scope, raw_id, original, checkpoint):
    # The caller's outer transaction ends after recording registration commits on
    # its own connection. A concurrent uploader then sees that committed owner.
    key = "mcap-admission:" + canonical_hash([*scope, original["sha256"]])
    while not lock.execute(
        "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))", (key,)
    ).fetchone()[0]:
        checkpoint()
        sleep(0.25)
    row = lock.execute(
        """SELECT r.recording_id FROM ingest.recording_upload_assets a
           JOIN ingest.continuous_recordings r
             ON (r.organization_id,r.project_id,r.region_code,r.recording_upload_id)
              = (a.organization_id,a.project_id,a.region_code,a.upload_id)
           WHERE (a.organization_id,a.project_id,a.region_code)=(%s,%s,%s)
             AND a.status='COMMITTED' AND a.asset_path=%s AND a.expected_sha256=%s
             AND r.superseded_by_processing_id IS NULL
             AND NOT EXISTS (SELECT 1 FROM ingest.mcap_processing_files d
                 WHERE (d.organization_id,d.project_id,d.region_code,d.recording_id) =
                       (r.organization_id,r.project_id,r.region_code,r.recording_id)
                   AND d.duplicate_of IS NOT NULL)
             AND NOT EXISTS (
               SELECT 1 FROM ingest.mcap_processing_files own
               WHERE (own.organization_id,own.project_id,own.region_code,own.recording_id)
                   = (r.organization_id,r.project_id,r.region_code,r.recording_id)
                 AND own.raw_source_id=%s AND own.source_path=%s)
           ORDER BY (r.recording_document->'quality_summary'->>'status'='PASS') DESC NULLS LAST,
                    r.created_at,r.recording_id LIMIT 1""",
        (*scope, ORIGINAL_PATH, original["sha256"], raw_id, original["path"]),
    ).fetchone()
    if row:
        return row[0]
    # A failed preparation can own an original too. Keep its diagnostic and retry
    # on that submission without generating the same partial files elsewhere.
    failed = lock.execute(
        """SELECT processing_id FROM ingest.mcap_processing_files f
           WHERE (organization_id,project_id,region_code)=(%s,%s,%s)
             AND source_document->>'sha256'=%s AND status='FAILED' AND duplicate_of IS NULL
             AND (raw_source_id,source_path)<>(%s,%s)
             AND COALESCE(error,'') NOT LIKE 'MCAP_DUPLICATE_RECORDING:%%'
             AND NOT EXISTS (SELECT 1 FROM ingest.mcap_processing_files newer
                 WHERE (newer.organization_id,newer.project_id,newer.region_code,
                        newer.raw_source_id,newer.source_path) =
                       (f.organization_id,f.project_id,f.region_code,f.raw_source_id,f.source_path)
                   AND (newer.created_at,newer.processing_id)>(f.created_at,f.processing_id))
           ORDER BY created_at,processing_id LIMIT 1""",
        (*scope, original["sha256"], raw_id, original["path"]),
    ).fetchone()
    return "processing:" + failed[0] if failed else None
