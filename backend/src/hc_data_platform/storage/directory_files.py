"""Resolve stored locators from the platform's typed asset records for a retired scope."""

from collections import defaultdict
from psycopg import sql
from hc_data_platform.collection_tasks.deletion_plan import registered_locators

# Select individual locator fields. User-authored descriptions and arbitrary JSON
# are never interpreted as filesystem or object-store paths.
ASSETS = {
    "ingest.raw_sources": ("storage_prefix", "manifest_key"),
    "ingest.robot_ingest_assets": ("object_key",),
    "ingest.recording_upload_assets": ("object_key",),
    "ingest.upload_sessions": ("object_key", "multipart_upload_id"),
    "ingest.upload_objects": ("object_key",),
    "ingest.rollout_objects": ("object_key", "manifest_key"),
    "preview.artifacts": ("object_prefix",),
    "aligned_media.artifacts": ("object_prefix",),
    "annotation.frame_selection_manifests": ("object_key",),
    "annotation.revision_files": ("object_key",),
    "annotation.delivery_jobs": ("artifact_key",),
    "episode_store.delivery_jobs": ("artifact_key",),
    "episode_store.annotation_migrations": ("document_key",),
    "episode_store.collection_releases": ("object_key",),
    "episode_store.versions": ("receipt_document", "input_document", "data_version_id"),
    "episode_store.media_previews": ("receipt",),
    "publishing.publication_assets": ("artifact_uri",),
    "publishing.published_exports": ("artifact_uri",),
    "publishing.export_attempts": ("staging_uri",),
    "public.aligned_fragment_attempts": (
        "staging_uri",
        "rollout_id",
        "attempt_id",
        "content_sha256",
    ),
    "public.lance_datasets": ("dataset_uri",),
    "public.lance_rollout_lineage": ("fragment_uri",),
    "storage.managed_objects": (
        "object_key",
        "original_object_key",
        "legal_hold",
        "governance_hold",
        "retention_until",
    ),
    "registry.robot_model_assets": ("object_key",),
    "registry.robot_model_asset_upload_files": ("object_key", "multipart_upload_id"),
    "storage.cache_test_copies": ("object_key",),
    "storage.managed_multipart_uploads": ("object_key", "upload_id", "status"),
    "public.raw_verification_reports": ("object_key",),
    "ingest.recording_cache_retirements": ("object_key",),
}
EXTRA_KEYS = {
    "registry.robot_model_assets",
    "registry.robot_model_asset_upload_files",
    "storage.cache_test_copies",
    "storage.managed_multipart_uploads",
    "public.raw_verification_reports",
    "ingest.recording_cache_retirements",
}


def scoped_rows(db, org, project, *, outside):
    for table, fields in ASSETS.items():
        condition = sql.SQL("organization_id=%s AND (%s='' OR project_id=%s)")
        if outside:
            condition = sql.SQL("NOT ({})").format(condition)
        query = sql.SQL("SELECT {} FROM {} WHERE {}").format(
            sql.SQL(",").join(map(sql.Identifier, fields)),
            sql.Identifier(*table.split(".")),
            condition,
        )
        with db.cursor(name="directory_file_scan") as c:
            c.execute(query, (org, project, project))
            for result in c:
                yield table, dict(zip(fields, result, strict=True))


def files_for_row(table, row, objects):
    if table in {"registry.robot_model_assets", "registry.robot_model_asset_upload_files"}:
        return set(), set(), "model://" + row["object_key"] if row.get("object_key") else None
    local = objects.local_artifacts
    path = local.locator(table, row) if local else None
    keys, prefixes = registered_locators(
        table, row, objects.bucket, ignore_external=True, local_artifacts=local
    )
    if table in EXTRA_KEYS and row.get("object_key"):
        key = row["object_key"]
        if key.startswith("/") or "://" in key or ".." in key.split("/") or "/" not in key:
            raise ValueError("Unscoped storage locator")
        keys.add(key)
    if table == "episode_store.versions" and row.get("input_document", {}).get("identity"):
        from hc_data_platform.episode_store.models import EpisodeIdentity, digest

        identity = EpisodeIdentity.model_validate(row["input_document"]["identity"])
        prefixes.add(
            "episode-previews/" + digest(identity.key) + "/" + row["data_version_id"] + "/"
        )
    return keys, prefixes, path


def collect_files(db, org, project, objects, now):
    result = set()
    for table, row in scoped_rows(db, org, project, outside=False):
        if (
            row.get("legal_hold")
            or row.get("governance_hold")
            or (row.get("retention_until") and row["retention_until"] > now)
        ):
            raise RuntimeError("DIRECTORY_FILE_ON_HOLD")
        keys, prefixes, path = files_for_row(table, row, objects)
        result.update(("object", key, "") for key in keys)
        for prefix in prefixes:
            result.update(("object", key, "") for key, _ in objects.list(prefix))
        if path:
            result.add(("local", path, ""))
        upload = row.get("multipart_upload_id") or (
            row.get("upload_id") if table == "storage.managed_multipart_uploads" else None
        )
        if (
            upload
            and upload != "platform-derived"
            and row.get("object_key")
            and row.get("status") not in {"COMPLETED", "ABORTED", "PUBLISHED"}
        ):
            result.add(
                (
                    "multipart",
                    path if path and path.startswith("model://") else row["object_key"],
                    upload,
                )
            )
    for (project_id,) in db.execute(
        "SELECT project_id FROM registry.organization_projects WHERE organization_id=%s AND (%s='' OR project_id=%s)",
        (org, project, project),
    ):
        if not getattr(objects, "remote_configured", True):
            continue
        for kind in ("raw", "derived-recordings", "derived-mcap", "recording-previews"):
            prefix = f"{kind}/{org}/{project_id}/"
            for key, _ in objects.list(prefix):
                if not key.startswith(prefix):
                    raise ValueError("Object listing returned a key outside the retired scope")
                result.add(("object", key, ""))
    return exclude_shared(db, org, project, objects, result)


def exclude_shared(db, org, project, objects, files):
    if not files:
        return files
    refs = defaultdict(set)
    for item in files:
        refs[item[1]].add(item)
    protected = set()
    for table, row in scoped_rows(db, org, project, outside=True):
        keys, prefixes, path = files_for_row(table, row, objects)
        for key in keys | ({path} if path else set()):
            protected.update(refs.get(key, ()))
        for prefix in prefixes:
            for key in refs:
                if key.startswith(prefix):
                    protected.update(refs[key])
    return files - protected
