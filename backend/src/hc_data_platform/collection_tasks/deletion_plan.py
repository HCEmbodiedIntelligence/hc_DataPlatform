"""Explicit ownership graph for permanent collection-task deletion.

Only task data participates. Robot credentials, models, schemas/profiles and the
security audit are deliberately outside this graph. Dataset-wide deletion is
allowed only for a task's exclusive, automatically created Dataset.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit

from psycopg import sql

from hc_data_platform.core.errors import problem

# This allowlist is also installed by the deletion migration. Adding a domain
# requires reviewing ownership, object locators, and its immutable-row guards.
TABLES = tuple(
    [
        "collection_tasks.collection_tasks",
        "ingest.collection_jobs",
        "ingest.rollouts",
        "ingest.upload_sessions",
        "ingest.upload_objects",
        "ingest.upload_parts",
        "ingest.rollout_objects",
        "ingest.manifest_discoveries",
        "ingest.workflow_triggers",
        "ingest.device_capture_facts",
        "ingest.robot_ingest_uploads",
        "ingest.robot_ingest_assets",
        "ingest.robot_ingest_camera_metrics",
        "ingest.robot_ingest_attempts",
        "ingest.raw_sources",
        "ingest.raw_ingest_jobs",
        "ingest.raw_source_episodes",
        "ingest.episode_resolutions",
        "ingest.robot_processing",
        "ingest.robot_processing_requests",
        "ingest.mcap_processing_files",
        "ingest.continuous_recordings",
        "ingest.recording_uploads",
        "ingest.recording_upload_assets",
        "ingest.recording_slice_revisions",
        "ingest.recording_episode_slices",
        "ingest.recording_episode_asset_windows",
        "ingest.recording_episode_processing",
        "ingest.recording_episode_qc_reports",
        "ingest.recording_episode_dataset_version_reservations",
        "dataset_registry.datasets",
        "dataset_registry.dataset_detail_facts",
        "dataset_registry.dataset_versions",
        "dataset_registry.dataset_version_async_jobs",
        "dataset_registry.dataset_version_capacity_facts",
        "dataset_registry.dataset_version_content_projections",
        "dataset_registry.dataset_version_episode_revisions",
        "dataset_registry.dataset_version_episodes",
        "dataset_registry.dataset_version_manifest_entries",
        "dataset_registry.dataset_version_operational_inventory",
        "dataset_registry.dataset_version_required_storage",
        "dataset_registry.dataset_version_review_decisions",
        "dataset_registry.dataset_version_review_findings",
        "dataset_registry.dataset_version_schema_details",
        "dataset_registry.dataset_version_schema_summaries",
        "dataset_registry.dataset_version_source_provenance",
        "dataset_registry.dataset_version_successor_drafts",
        "episode_store.bindings",
        "episode_store.versions",
        "episode_store.attempts",
        "episode_store.events",
        "episode_store.collections",
        "episode_store.collection_events",
        "episode_store.collection_heads",
        "episode_store.collection_publications",
        "episode_store.dataset_schemas",
        "episode_store.media_previews",
        "episode_store.data_reviews",
        "episode_store.qc_evidence",
        "episode_store.delivery_jobs",
        "episode_store.delivery_requests",
        "episode_store.tag_derivation_jobs",
        "episode_store.tag_derivation_requests",
        "episode_store.migration_batches",
        "episode_store.annotation_migrations",
        "episode_store.collection_releases",
        "annotation.annotation_tasks",
        "annotation.annotation_revisions",
        "annotation.annotation_operations",
        "annotation.annotation_reviews",
        "annotation.annotation_mutations",
        "annotation.annotation_submissions",
        "annotation.annotation_submission_mutations",
        "annotation.annotation_task_triggers",
        "annotation.auto_annotation_jobs",
        "annotation.revision_files",
        "annotation.delivery_jobs",
        "annotation.delivery_requests",
        "annotation.frame_selection_manifests",
        "annotation.legacy_cleaning_migrations",
        "annotation.tag_schema_bindings",
        "manual_cleaning.manual_issues",
        "manual_cleaning.manual_issue_draft_links",
        "manual_cleaning.cleaning_workbench_drafts",
        "manual_cleaning.cleaning_draft_ancestry",
        "manual_cleaning.cleaning_draft_edl_revisions",
        "manual_cleaning.cleaning_draft_previews",
        "manual_cleaning.cleaning_workbench_commits",
        "manual_cleaning.cleaning_workbench_jobs",
        "manual_cleaning.cleaning_commit_output_revisions",
        "publishing.dataset_versions",
        "publishing.publication_assets",
        "publishing.export_attempts",
        "publishing.published_exports",
        "publishing.rollout_publication_lineage",
        "publishing.episode_version_finalizations",
        "public.aligned_fragment_attempts",
        "public.qc_reports",
        "public.quality_rollout_summaries",
        "public.raw_verification_reports",
        "public.lance_datasets",
        "public.lance_dataset_versions",
        "public.lance_rollout_lineage",
        "public.lance_pending_reconciliation",
        "public.lance_schema_snapshots",
        "preview.artifacts",
        "preview.jobs",
        "preview.sessions",
        "aligned_media.artifacts",
        "aligned_media.jobs",
        "aligned_media.dataset_version_retirement_requests",
        "workflow.jobs",
        "workflow.reconciliation_items",
        "core.outbox_events",
        "robotics.data_scene_snapshots",
        "storage.managed_objects",
        "storage.managed_multipart_uploads",
        "storage.object_operations",
    ]
)

# Do not propagate episode_id (often episode_0001), asset_id (asset-000), schema
# IDs, or robot IDs: those names are intentionally reused across tasks.
IDENTITIES = (
    "dataset_id",
    "collection_task_id",
    "collection_job_id",
    "rollout_id",
    "recording_id",
    "raw_source_id",
    "upload_id",
    "session_id",
    "workflow_id",
    "artifact_id",
    "job_id",
    "draft_id",
    "commit_id",
    "manual_issue_id",
)
KEY_FIELDS = {
    "object_key",
    "manifest_key",
    "media_object_key",
    "playlist_key",
    "artifact_key",
    "original_object_key",
    "document_key",
}
PREFIX_FIELDS = {"storage_prefix", "object_prefix", "package_prefix"}
URI_FIELDS = {"artifact_uri", "staging_uri", "dataset_uri", "fragment_uri"}


def conflict(code, detail):
    return problem(status=409, code=code, title="Task deletion blocked", detail=detail)


def contains(value, selector):
    return isinstance(value, dict) and all(
        key in value
        and (contains(value[key], item) if isinstance(item, dict) else value[key] == item)
        for key, item in selector.items()
    )


def locators(document, bucket):
    """Use typed, persisted locator fields; never treat arbitrary strings as keys."""
    keys, prefixes = set(), set()
    if isinstance(document, list):
        children = document
    elif isinstance(document, dict):
        children = document.values()
        for name, value in document.items():
            if not isinstance(value, str) or not value:
                continue
            if name in KEY_FIELDS:
                # artifact_key can also be a logical hash, not a physical locator.
                if "/" in value and "://" not in value:
                    keys.add(value)
            elif name in PREFIX_FIELDS:
                prefixes.add(value.rstrip("/") + "/")
            elif name in URI_FIELDS and "://" in value:
                uri = urlsplit(value)
                if uri.scheme not in {"s3", "oss"} or uri.netloc != bucket:
                    raise conflict(
                        "TASK_DELETE_EXTERNAL_STORAGE", "数据包含外部存储引用，请先处理这些引用。"
                    )
                key = unquote(uri.path.lstrip("/"))
                if name in {"dataset_uri", "fragment_uri"}:
                    prefixes.add(key.rstrip("/") + "/")
                else:
                    keys.add(key)
    else:
        return keys, prefixes
    for child in children:
        child_keys, child_prefixes = locators(child, bucket)
        keys.update(child_keys)
        prefixes.update(child_prefixes)
    if any(not k or k.startswith("/") or ".." in k.split("/") for k in keys | prefixes):
        raise conflict("TASK_DELETE_INVALID_LOCATOR", "数据的存储路径不完整，无法安全删除。")
    return keys, prefixes


def registered_locators(table, row, bucket):
    # Do not walk arbitrary upload commands, annotation plans, provenance or
    # Dataset metadata. Those documents can contain caller-supplied strings.
    direct = {
        "ingest.raw_sources": ("storage_prefix", "manifest_key"),
        "ingest.robot_ingest_assets": ("object_key",),
        "ingest.recording_upload_assets": ("object_key",),
        "ingest.upload_sessions": ("object_key",),
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
        "publishing.publication_assets": ("artifact_uri",),
        "publishing.published_exports": ("artifact_uri",),
        "publishing.export_attempts": ("staging_uri",),
        "public.aligned_fragment_attempts": ("staging_uri",),
        "public.lance_datasets": ("dataset_uri",),
        "public.lance_rollout_lineage": ("fragment_uri",),
        "storage.managed_objects": ("object_key", "original_object_key"),
    }
    document = {k: row[k] for k in direct.get(table, ()) if row.get(k)}
    if table == "episode_store.versions" and row.get("receipt_document"):
        document["package_prefix"] = row["receipt_document"]["data"]["package_prefix"]
    if table == "episode_store.media_previews" and row.get("receipt"):
        document["object_key"] = row["receipt"].get("object_key")
    return locators(document, bucket)


def metadata(cursor):
    cursor.execute("""SELECT n.nspname || '.' || c.relname, a.attname
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_attribute a ON a.attrelid=c.oid
        WHERE a.attnum>0 AND NOT a.attisdropped AND c.relkind='r'
        ORDER BY a.attnum""")
    columns = defaultdict(list)
    for table, column in cursor.fetchall():
        if table in TABLES:
            columns[table].append(column)
    cursor.execute("""SELECT n.nspname || '.' || c.relname, a.attname
        FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        CROSS JOIN LATERAL unnest(k.conkey) WITH ORDINALITY AS x(num,ord)
        JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=x.num
        WHERE k.contype='p' ORDER BY x.ord""")
    primary = defaultdict(list)
    for table, column in cursor.fetchall():
        primary[table].append(column)
    return columns, primary


@dataclass
class DeletionPlan:
    selectors: dict = field(default_factory=lambda: defaultdict(list))
    rows: dict = field(default_factory=lambda: defaultdict(list))
    objects: dict = field(default_factory=dict)
    multiparts: list = field(default_factory=list)
    present_objects: int = 0

    @property
    def counts(self):
        return {table: len(rows) for table, rows in self.rows.items() if rows}


def build_plan(cursor, task, scope, bucket, objects):
    org, project, region = scope
    columns, primary = metadata(cursor)
    dataset = task["dataset_id"]
    cursor.execute(
        """SELECT collection_task_id FROM collection_tasks.collection_tasks
        WHERE organization_id=%s AND project_id=%s AND dataset_id=%s
          AND collection_task_id<>%s""",
        (org, project, dataset, task["collection_task_id"]),
    )
    if cursor.fetchone():
        raise conflict(
            "TASK_DELETE_SHARED_DATASET", "该数据集还关联其他采集任务，请先拆分数据集后删除。"
        )
    from .models import dataset_id_for_collection_task

    if dataset != dataset_id_for_collection_task(org, project, task["collection_task_id"]):
        raise conflict(
            "TASK_DELETE_SHARED_DATASET", "该任务使用独立创建的数据集，请先拆分数据集后删除。"
        )

    all_rows = {}
    for table in TABLES:
        names = columns[table]
        if "project_id" in names:
            conditions = [sql.SQL("project_id=%s")]
            params = [project]
            if "organization_id" in names:
                conditions.append(sql.SQL("organization_id=%s"))
                params.append(org)
            if "region_code" in names:
                conditions.append(sql.SQL("region_code=%s"))
                params.append(region)
            query = sql.SQL("SELECT to_jsonb(t) FROM {} t WHERE {}").format(
                sql.Identifier(*table.split(".")), sql.SQL(" AND ").join(conditions)
            )
        else:
            # The legacy annotation revisions use globally unique annotation task IDs.
            query = sql.SQL("""SELECT to_jsonb(t) FROM {} t WHERE task_id IN
                (SELECT task_id FROM annotation.annotation_tasks
                 WHERE organization_id=%s AND project_id=%s AND region_code=%s)""").format(
                sql.Identifier(*table.split("."))
            )
            params = [org, project, region]
        cursor.execute(query, params)
        all_rows[table] = [row[0] for row in cursor.fetchall()]

    ids = defaultdict(set)
    ids["dataset_id"].add(dataset)
    ids["collection_task_id"].add(task["collection_task_id"])
    selected = defaultdict(set)
    changed = True
    while changed:
        changed = False
        for table, rows in all_rows.items():
            for index, row in enumerate(rows):
                if index in selected[table]:
                    continue
                match = any(row.get(key) in ids[key] for key in IDENTITIES if row.get(key))
                if table == "ingest.collection_jobs":
                    match |= row.get("task_id") in ids["collection_task_id"]
                elif table.startswith("annotation."):
                    match |= row.get("task_id") in ids["annotation_task_id"]
                if table == "workflow.jobs":
                    match |= row.get("resource_id") in ids["resource_id"]
                if table == "core.outbox_events":
                    envelope = row["envelope"]
                    match |= envelope.get("aggregate_id") in ids["aggregate_id"]
                    match |= any(
                        envelope.get("payload", {}).get(k) in ids[k]
                        for k in IDENTITIES
                        if envelope.get("payload", {}).get(k)
                    )
                if table in {"episode_store.delivery_jobs", "episode_store.tag_derivation_jobs"}:
                    match |= _nested_dataset(row.get("plan", {}), dataset)
                if table == "robotics.data_scene_snapshots":
                    match |= row["source_key"] in ids["source_key"]
                if not match:
                    continue
                selected[table].add(index)
                changed = True
                for key in IDENTITIES:
                    if row.get(key):
                        ids[key].add(row[key])
                if table == "annotation.annotation_tasks":
                    ids["annotation_task_id"].add(row["task_id"])
                if table == "ingest.recording_episode_processing":
                    ids["resource_id"].add(row["recording_id"] + "/" + row["episode_id"])
                    ids["rollout_id"].add(row["recording_id"] + ":" + row["episode_id"])
                if table == "episode_store.versions":
                    _scene_keys(row["input_document"], ids["source_key"])
                ids["aggregate_id"].update(v for k in IDENTITIES for v in ids[k])

    plan = DeletionPlan()
    for table, rows in all_rows.items():
        names = columns[table]
        scope_selector = {
            k: v
            for k, v in zip(("organization_id", "project_id", "region_code"), scope, strict=True)
            if k in names
        }
        for key in IDENTITIES:
            if key in names:
                for value in ids[key]:
                    plan.selectors[table].append({**scope_selector, key: value})
        if "task_id" in names:
            values = (
                ids["collection_task_id"]
                if table == "ingest.collection_jobs"
                else ids["annotation_task_id"]
            )
            for value in values:
                plan.selectors[table].append({**scope_selector, "task_id": value})
        if table == "workflow.jobs":
            for value in ids["resource_id"]:
                plan.selectors[table].append({**scope_selector, "resource_id": value})
        if table == "core.outbox_events":
            for value in ids["aggregate_id"]:
                plan.selectors[table].append(
                    {**scope_selector, "envelope": {"aggregate_id": value}}
                )
            for key in IDENTITIES:
                for value in ids[key]:
                    plan.selectors[table].append(
                        {**scope_selector, "envelope": {"payload": {key: value}}}
                    )
        for index in selected[table]:
            row = rows[index]
            _assert_idle_unprotected(table, row)
            plan.rows[table].append(row)
            if not primary[table]:
                raise RuntimeError(f"deletion table lacks primary key: {table}")
            plan.selectors[table].append({k: row[k] for k in primary[table]})
            if table != "robotics.data_scene_snapshots":
                keys, prefixes = registered_locators(table, row, bucket)
                for key in keys:
                    plan.objects.setdefault(key, 0)
                for prefix in prefixes:
                    for key, size in objects.list(prefix):
                        plan.objects[key] = size
            upload = row.get("multipart_upload_id")
            if upload and upload != "platform-derived" and row.get("object_key"):
                plan.multiparts.append((row["object_key"], upload))
    # Include failed/unregistered attempts in task-owned namespaces, not just
    # the latest successful derivation receipt.
    prefixes = [f"raw/{org}/{project}/{task['collection_task_id']}/"]
    prefixes += [
        f"derived-recordings/{org}/{project}/{region}/{raw}/" for raw in ids["raw_source_id"]
    ]
    prefixes += [
        f"derived-mcap/{org}/{project}/{region}/{r['processing_id']}/"
        for r in plan.rows["ingest.mcap_processing_files"]
    ]
    from hc_data_platform.continuous_recordings.asset_models import RecordingAsset, RecordingUpload
    from hc_data_platform.continuous_recordings.previews import preview_key
    from hc_data_platform.episode_store.models import EpisodeIdentity, digest

    for row in plan.rows["ingest.recording_uploads"]:
        upload = RecordingUpload.model_validate(row["upload_document"])
        assets = [
            RecordingAsset.model_validate(a["asset_document"])
            for a in plan.rows["ingest.recording_upload_assets"]
            if a["upload_id"] == row["upload_id"]
        ]
        for asset in assets:
            key = preview_key(upload, asset, assets)
            prefixes.append(key.rsplit("/", 1)[0] + "/")
    for row in plan.rows["episode_store.versions"]:
        identity = EpisodeIdentity.model_validate(row["input_document"]["identity"])
        prefixes.append(f"episode-previews/{digest(identity.key)}/{row['data_version_id']}/")
    for prefix in prefixes:
        for key, size in objects.list(prefix):
            plan.objects[key] = size

    # The storage catalog is indexed by physical key, independently of task IDs.
    managed_ids = set()
    for index, row in enumerate(all_rows["storage.managed_objects"]):
        if row["object_key"] in plan.objects or row.get("original_object_key") in plan.objects:
            _assert_idle_unprotected("storage.managed_objects", row)
            selected["storage.managed_objects"].add(index)
            plan.rows["storage.managed_objects"].append(row)
            plan.selectors["storage.managed_objects"].append(
                {k: row[k] for k in primary["storage.managed_objects"]}
            )
            managed_ids.add(row["object_id"])
            plan.objects.setdefault(row["object_key"], 0)
    for table in ("storage.managed_multipart_uploads", "storage.object_operations"):
        for index, row in enumerate(all_rows[table]):
            if row.get("object_id") in managed_ids or row.get("object_key") in plan.objects:
                selected[table].add(index)
                plan.rows[table].append(row)
                plan.selectors[table].append({k: row[k] for k in primary[table]})
                if table.endswith("managed_multipart_uploads"):
                    plan.multiparts.append((row["object_key"], row["upload_id"]))

    # A retained record referencing the same physical object makes deletion unsafe.
    for table, rows in all_rows.items():
        if table == "robotics.data_scene_snapshots":
            continue
        for index, row in enumerate(rows):
            if index in selected[table]:
                continue
            keys, prefixes = registered_locators(table, row, bucket)
            if keys & plan.objects.keys() or any(
                k.startswith(p) for p in prefixes for k in plan.objects
            ):
                raise conflict(
                    "TASK_DELETE_SHARED_OBJECT", "任务文件仍被其他数据引用，无法永久删除。"
                )
    for key in list(plan.objects):
        size = objects.size(key)
        if size is not None:
            plan.present_objects += 1
            plan.objects[key] = size
    return plan


def _nested_dataset(value, dataset):
    if isinstance(value, dict):
        return value.get("dataset_id") == dataset or any(
            _nested_dataset(v, dataset) for v in value.values()
        )
    if isinstance(value, list):
        return any(_nested_dataset(v, dataset) for v in value)
    return False


def _scene_keys(value, keys):
    if isinstance(value, dict):
        if all(k in value for k in ("identity", "source_sha256", "robot_id")):
            from hc_data_platform.robotics.scene_models import scene_hash

            keys.add(scene_hash({k: value[k] for k in ("identity", "source_sha256", "robot_id")}))
        for v in value.values():
            _scene_keys(v, keys)
    elif isinstance(value, list):
        for v in value:
            _scene_keys(v, keys)


def _assert_idle_unprotected(table, row):
    now = datetime.now(timezone.utc)
    for flag in ("legal_hold", "governance_hold"):
        if row.get(flag):
            raise conflict("TASK_DELETE_PROTECTED", "关联数据仍处于保护状态。")
    for flag in ("retention_until", "lease_expires_at", "claimed_until"):
        if row.get(flag) and datetime.fromisoformat(row[flag]) > now:
            raise conflict("TASK_DELETE_BUSY", "关联数据正在处理或处于保留期，请稍后重试。")
    if row.get("status") in {"RUNNING", "PREPARING", "COMMITTING"}:
        raise conflict("TASK_DELETE_BUSY", "任务仍在后台处理中，请等待处理结束或取消处理后重试。")
    if table == "workflow.jobs" and row.get("status") in {"PENDING", "RUNNING"}:
        raise conflict("TASK_DELETE_BUSY", "任务仍有未结束的处理流程，请先取消处理后重试。")
