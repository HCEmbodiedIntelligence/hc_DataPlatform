"""Verified, recoverable recording caches; source and Episode bytes are retained."""

import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from hc_data_platform.episode_store.models import DataVersionRef
from hc_data_platform.episode_store.native_reference import ready as reference_ready
from hc_data_platform.object_storage.immutable import verify_object

from .asset_models import RecordingAsset, RecordingUpload
from .derived_cache import DerivedCache, evictable
from .previews import read_preview


def retirement_plan(connections, storage, packages, scope, recording_id):
    from hc_data_platform.core.context import current_request_context

    context = current_request_context()
    if tuple(scope) != (context.organization_id, context.project_id, context.region_code):
        raise PermissionError("cache retirement requires the exact request scope")
    with connections() as db:
        row = db.execute(
            """SELECT m.processing_id,m.source_document,r.recording_upload_id
            FROM ingest.mcap_processing_files m JOIN ingest.continuous_recordings r
            USING(organization_id,project_id,region_code,recording_id)
            WHERE m.organization_id=%s AND m.project_id=%s AND m.region_code=%s
            AND m.recording_id=%s AND m.status='READY' AND r.status='SLICED'
            AND r.current_revision=r.finalized_revision""",
            (*scope, recording_id),
        ).fetchone()
        if row is None:
            return []
        ident, original, upload_id = row
        episodes = db.execute(
            """SELECT status,data_ref FROM ingest.recording_episode_processing
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND recording_id=%s""",
            (*scope, recording_id),
        ).fetchall()
        if not episodes or any(status != "READY" or not data for status, data in episodes):
            return []
        for _, value in episodes:
            data = DataVersionRef.model_validate(value)
            package = packages.files.open(data.identity, data.data_version_id)
            if not reference_ready(package, storage):
                raise ValueError("Episode Raw references require exact reconstruction verification")
        upload_row = db.execute(
            """SELECT upload_document FROM ingest.recording_uploads
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND upload_id=%s""",
            (*scope, upload_id),
        ).fetchone()
        assets = [
            RecordingAsset.model_validate(r[0])
            for r in db.execute(
                """SELECT asset_document
            FROM ingest.recording_upload_assets WHERE organization_id=%s AND project_id=%s
            AND region_code=%s AND upload_id=%s AND status='COMMITTED' """,
                (*scope, upload_id),
            ).fetchall()
        ]
    upload = RecordingUpload.model_validate(upload_row[0])
    prefix = "/".join(("derived-mcap", *scope, ident)) + "/"
    candidates = [
        a
        for a in assets
        if a.object_key.startswith(prefix)
        and evictable(a.manifest.path)
        and storage.head(a.object_key) is not None
    ]
    # A video reused directly by the viewer is already its only preview copy.
    # Retain that file; absence of a separate RGB preview must not block other caches.
    cameras = {camera.camera_id: camera for camera in upload.command.recording_config.cameras}
    from .previews import is_depth

    safe = []
    for asset in candidates:
        if asset.manifest.role == "RAW_VIDEO":
            preview = read_preview(storage, upload, asset, assets)
            if preview is None and is_depth(cameras[asset.manifest.camera_id]):
                preview = read_preview(storage, upload, asset, assets, selected=False)
            if preview is None:
                continue
        safe.append(asset)
    candidates = safe
    if not candidates:
        return []
    verify_object(storage, original["object_key"], original["size"], original["sha256"])
    with TemporaryDirectory(prefix="retention-rebuild-") as temporary:
        recovery = DerivedCache(storage, connections, scope, Path(temporary))
        for asset in candidates:
            recovered = recovery.recover(
                asset.object_key,
                asset.manifest.path,
                asset.manifest.size,
                asset.manifest.sha256,
                force=True,
            )
            if recovered is None:
                raise ValueError("cache candidate is not reconstructible")
    return [
        {
            "key": a.object_key,
            "size": a.manifest.size,
            "sha256": a.manifest.sha256,
            "recording_id": recording_id,
            "processing_id": ident,
            "rebuild_verified": True,
            "raw_source": original,
            "data_versions": [value["data_version_id"] for _, value in episodes],
        }
        for a in candidates
    ]


def retire_recording(
    connections, storage, packages, scope, recording_id, *, authorize, on_deleted=lambda item: None
):
    """Revalidate dependencies and byte-identical reconstruction before eviction."""
    scope = tuple(scope)
    with connections() as lock:
        token = ":".join(("recording-cache-retention", *scope, recording_id))
        lock.execute("SELECT pg_advisory_lock(hashtextextended(%s,0))", (token,))
        try:
            if authorize(scope, recording_id) is None:
                return []
            plan = retirement_plan(connections, storage, packages, scope, recording_id)
            deleted = []
            for item in plan:
                # Do not accept a stale external plan or broaden deletion to a prefix.
                verify_object(storage, item["key"], item["size"], item["sha256"])
                lifecycle = authorize(scope, recording_id)
                if lifecycle is None:
                    break
                item = {**item, "lifecycle": lifecycle}
                receipt = {
                    **item,
                    "policy": "verified-rebuildable-cache/1",
                    "verified_at": datetime.now(timezone.utc).isoformat(),
                }
                with connections() as db:
                    updated = db.execute(
                        """INSERT INTO ingest.recording_cache_retirements
                        (receipt,organization_id,project_id,region_code,object_key)
                        SELECT %s::jsonb,organization_id,project_id,region_code,object_key
                        FROM ingest.recording_upload_assets
                        WHERE organization_id=%s AND project_id=%s AND region_code=%s
                        AND object_key=%s AND expected_size=%s AND expected_sha256=%s
                        AND status='COMMITTED' AND multipart_upload_id='platform-derived'
                        ON CONFLICT (organization_id,project_id,region_code,object_key)
                        DO UPDATE SET receipt=EXCLUDED.receipt, retired_at=now()
                        RETURNING object_key""",
                        (json.dumps(receipt), *scope, item["key"], item["size"], item["sha256"]),
                    ).fetchone()
                    if updated is None:
                        raise ValueError("cache asset changed during verified retirement")
                    db.commit()
                storage.delete_object(item["key"])
                if storage.head(item["key"]) is not None:
                    raise RuntimeError("verified cache deletion was not completed")
                deleted.append(item)
                on_deleted(item)
            return deleted
        finally:
            lock.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (token,))
