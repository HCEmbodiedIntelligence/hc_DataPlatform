"""Read-only W7 inventory of one exact dataset scope.

This is an operator tool, not an HTTP authorization bypass or a migration writer.
Database references are frozen in one repeatable-read snapshot. Physical objects,
numerical equivalence and depth source evidence need separate verification before
any visible pointer may move. Historical annotation rows are never rewritten.
"""

import json
import os
import re
import tempfile
from pathlib import Path

from psycopg import sql

from .collections import collection_scope
from .models import EpisodeIdentity, LegacyEpisodeRef, canonical, digest
from .postgres import PackageConflict

FORMAT = "hc-episode-migration-inventory/1"
REGISTRY_TABLES = (
    "datasets",
    "dataset_versions",
    "dataset_version_episodes",
    "dataset_version_episode_revisions",
    "dataset_version_schema_details",
    "dataset_version_content_projections",
    "dataset_version_source_provenance",
    "dataset_version_manifest_entries",
    "dataset_version_review_decisions",
    "dataset_version_review_findings",
)


class InventoryLimit(ValueError):
    pass


def capture_inventory(connections, dataset_id, *, max_rows=10000):
    """Require a verified ambient scope; explicitly refuse a partial/truncated inventory."""
    scope = collection_scope(dataset_id)
    if type(max_rows) is not int or not 1 <= max_rows <= 100000:
        raise ValueError("inventory row limit must be 1..100000")
    connection = connections()
    documents = {}
    try:
        # The production factory binds session-level RLS variables first. End
        # that setup transaction before starting the read-only data snapshot.
        connection.commit()
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        observed = connection.execute(
            "SELECT current_setting('transaction_isolation'), "
            "current_setting('transaction_read_only')"
        ).fetchone()
        if observed != ("repeatable read", "on"):
            raise RuntimeError("inventory requires a repeatable-read read-only transaction")
        cursor = connection.cursor()

        def rows(table, columns, values, *, where=None, params=(), keep=None):
            identifier = sql.Identifier(*table.split("."))
            predicates = [sql.SQL("r.{}=%s").format(sql.Identifier(c)) for c in columns]
            if where is not None:
                predicates.append(sql.SQL(where))  # Only fixed SQL in this module.
            cursor.execute(
                sql.SQL("SELECT to_jsonb(r) FROM {} r WHERE {} LIMIT %s").format(
                    identifier, sql.SQL(" AND ").join(predicates)
                ),
                (*values, *params, max_rows + 1),
            )
            result = []
            while batch := cursor.fetchmany(128):
                for (record,) in batch:
                    if len(result) == max_rows:
                        raise InventoryLimit(
                            f"{table} exceeds inventory row limit; no output saved"
                        )
                    # Hash the complete row even where operational secrets/leases
                    # are omitted from the persisted, reviewable reference.
                    document = record if keep is None else {k: record[k] for k in keep}
                    if keep is not None and "asset_document" in document:
                        # The nested model also carries the operational upload
                        # handle; omitting only its SQL column is insufficient.
                        document["asset_document"] = {
                            k: v
                            for k, v in document["asset_document"].items()
                            if k != "multipart_upload_id"
                        }
                    result.append({"row_sha256": digest(record), "document": document})
            result.sort(key=lambda r: canonical(r["document"]))
            documents[table] = result
            return [r["document"] for r in result]

        exact = ("organization_id", "project_id", "region_code", "dataset_id")
        regional = exact[:3]
        project = ("organization_id", "project_id", "dataset_id")
        project_values = (scope[0], scope[1], scope[3])
        for table in REGISTRY_TABLES:
            rows("dataset_registry." + table, exact, scope)
        if len(documents["dataset_registry.datasets"]) != 1:
            raise KeyError("dataset is absent from the exact organization/project/region")

        versions = [
            int(match[1])
            for row in documents["dataset_registry.dataset_versions"]
            if (
                match := re.fullmatch(r"version_lance_([1-9][0-9]*)", row["document"]["version_id"])
            )
        ]
        revisions = [
            r["document"] for r in documents["dataset_registry.dataset_version_episode_revisions"]
        ]
        rollout_ids = sorted(
            {
                binding["rollout_id"]
                for row in revisions
                for stream in row["revision_document"].get("streams", [])
                for key in ("data_binding", "aligned_media_binding", "package_binding")
                if (binding := stream.get(key)) and binding.get("rollout_id")
            }
        )
        legacy_versions = rows(
            "public.lance_dataset_versions",
            project,
            project_values,
            where="r.version=ANY(%s)",
            params=(versions,),
        )
        rows("public.lance_datasets", project, project_values)
        rows(
            "public.lance_schema_snapshots",
            project,
            project_values,
            where="r.schema_snapshot_id=ANY(%s)",
            params=(sorted({r["schema_snapshot_id"] for r in legacy_versions}),),
        )
        rows(
            "public.lance_rollout_lineage",
            project,
            project_values,
            where="r.rollout_id=ANY(%s)",
            params=(rollout_ids,),
        )
        rows(
            "aligned_media.artifacts",
            exact,
            scope,
            keep=(
                "artifact_id",
                "artifact_key",
                "dataset_id",
                "rollout_id",
                "dataset_version",
                "camera_id",
                "profile_id",
                "profile_version",
                "pipeline_revision",
                "alignment_version",
                "source_sha256",
                "status",
                "media_object_key",
                "object_manifest",
                "total_bytes",
                "frame_count",
                "fps",
                "content_sha256",
                "timeline_json",
                "placeholder_count",
            ),
        )
        tasks = rows("annotation.annotation_tasks", exact, scope)
        task_ids = [r["task_id"] for r in tasks]
        for table in (
            "annotation_revisions",
            "annotation_submissions",
            "annotation_reviews",
            "annotation_operations",
        ):
            rows("annotation." + table, (), (), where="r.task_id=ANY(%s)", params=(task_ids,))

        uploads = sorted(
            {
                r["document"]["upload_id"]
                for r in documents["dataset_registry.dataset_version_source_provenance"]
            }
        )
        raw = rows(
            "ingest.raw_sources",
            regional,
            scope[:3],
            where="(r.dataset_id=%s OR r.upload_id=ANY(%s))",
            params=(dataset_id, uploads),
        )
        rows(
            "ingest.raw_source_episodes",
            regional,
            scope[:3],
            where="r.raw_source_id=ANY(%s)",
            params=([r["raw_source_id"] for r in raw],),
        )
        rows(
            "ingest.rollout_objects",
            regional,
            scope[:3],
            where="r.rollout_id=ANY(%s)",
            params=(rollout_ids,),
        )
        processing = rows("ingest.recording_episode_processing", exact, scope)
        windows = rows(
            "ingest.recording_episode_asset_windows",
            regional,
            scope[:3],
            where="""EXISTS (SELECT 1 FROM ingest.recording_episode_processing p
                WHERE p.organization_id=r.organization_id AND p.project_id=r.project_id
                  AND p.region_code=r.region_code AND p.recording_id=r.recording_id
                  AND p.episode_id=r.episode_id AND p.dataset_id=%s)""",
            params=(dataset_id,),
        )
        # Product Episode IDs can differ from recorder slice IDs. Use persisted
        # processing relations, never split rollout strings or compare display IDs.
        recording_ids = sorted({r["recording_id"] for r in processing})
        for table in (
            "continuous_recordings",
            "recording_slice_revisions",
            "recording_episode_slices",
        ):
            rows(
                "ingest." + table,
                regional,
                scope[:3],
                where="r.recording_id=ANY(%s)",
                params=(recording_ids,),
            )
        rows(
            "ingest.recording_upload_assets",
            regional,
            scope[:3],
            where="r.upload_id=ANY(%s::uuid[])",
            params=(sorted({r["upload_id"] for r in windows}),),
            keep=(
                "upload_id",
                "asset_id",
                "asset_path",
                "role",
                "camera_id",
                "media_type",
                "expected_size",
                "expected_sha256",
                "object_key",
                "status",
                "asset_document",
            ),
        )
        rows(
            "ingest.robot_ingest_assets",
            regional,
            scope[:3],
            where="r.upload_id=ANY(%s)",
            params=(sorted({r["upload_id"] for r in raw}),),
            keep=("upload_id", "asset_id", "object_key", "state", "asset_document"),
        )
        published = rows("publishing.rollout_publication_lineage", exact, scope)
        publications = sorted({r["dataset_version"] for r in published})
        for table in (
            "dataset_versions",
            "publication_assets",
            "published_exports",
            "export_attempts",
        ):
            rows(
                "publishing." + table,
                project,
                project_values,
                where="r.dataset_version=ANY(%s)",
                params=(publications,),
            )
        # Existing standard packages and heads also belong to the rollback baseline.
        for table in (
            "versions",
            "bindings",
            "collections",
            "collection_heads",
            "data_reviews",
            "migration_batches",
        ):
            rows("episode_store." + table, exact, scope)
        rows(
            "episode_store.qc_evidence",
            regional,
            scope[:3],
            where="r.rollout_id=ANY(%s)",
            params=(rollout_ids,),
        )
        cursor.close()
        connection.rollback()
    finally:
        connection.close()
    inventory = {
        "format": FORMAT,
        "scope": dict(zip(exact, scope, strict=True)),
        "database_snapshot": "repeatable_read_read_only",
        "observed_transaction": {"isolation": observed[0], "read_only": observed[1]},
        "tables": documents,
        "legacy_candidates": legacy_candidates(scope, documents),
        "dry_run": {
            "status": "REFERENCES_CAPTURED",
            "physical_objects_verified": False,
            "raw_manifests_expanded": False,
            "numeric_and_time_equivalence_verified": False,
            "depth_source_evidence_verified": False,
            "review_equivalence_verified": False,
            "visible_pointers_changed": False,
        },
    }
    return {"inventory_sha256": digest(inventory), "inventory": inventory}


def legacy_candidates(scope, tables):
    """Explicit mappings to verify later; never infer rollout identity from a name."""
    versions = {
        r["document"]["version"]: r["document"] for r in tables["public.lance_dataset_versions"]
    }
    lineage = {
        r["document"]["rollout_id"]: r["document"] for r in tables["public.lance_rollout_lineage"]
    }
    candidates = []
    for item in tables["dataset_registry.dataset_version_episode_revisions"]:
        revision = item["document"]
        match = re.fullmatch(r"version_lance_([1-9][0-9]*)", revision["version_id"])
        if match is None:
            continue
        bindings = {
            b["rollout_id"]
            for s in revision["revision_document"].get("streams", [])
            for k in ("data_binding", "aligned_media_binding")
            if (b := s.get(k)) and b.get("rollout_id")
        }
        issues = []
        version = versions.get(int(match[1]))
        if version is None:
            issues.append("LEGACY_VERSION_MISSING")
        if len(bindings) != 1:
            issues.append("ROLLOUT_BINDING_AMBIGUOUS")
        origin = lineage.get(next(iter(bindings))) if len(bindings) == 1 else None
        if origin is None:
            issues.append("LEGACY_LINEAGE_MISSING")
        elif version and (
            origin["version_added"] > version["version"]
            or origin["rollout_id"] not in version["committed_rollouts"]
            or origin["schema_snapshot_id"] != version["schema_snapshot_id"]
            or origin["step_count"] < 1
        ):
            issues.append("LEGACY_BINDING_INCONSISTENT")
        reference = None
        if not issues:
            identity = EpisodeIdentity(
                organization_id=scope[0],
                project_id=scope[1],
                region_code=scope[2],
                dataset_id=scope[3],
                episode_id=revision["episode_id"],
                rollout_id=origin["rollout_id"],
            )
            reference = LegacyEpisodeRef(
                identity=identity,
                data_version_id="legacy_"
                + digest(
                    {
                        "identity": identity.model_dump(mode="json"),
                        "dataset_uri": version["dataset_uri"],
                        "lance_version": version["lance_version"],
                        "content_hash": origin["fragment_content_hash"],
                    }
                ),
                content_hash=origin["fragment_content_hash"],
                schema_snapshot_id=origin["schema_snapshot_id"],
                frame_count=origin["step_count"],
                dataset_version=version["version"],
                dataset_uri=version["dataset_uri"],
                lance_version=version["lance_version"],
            ).model_dump(mode="json")
        candidates.append(
            {
                "version_id": revision["version_id"],
                "episode_id": revision["episode_id"],
                "revision_row_sha256": item["row_sha256"],
                "legacy": reference,
                "target_data": None,
                "state": "REFERENCE_BLOCKED" if issues else "AWAITING_VERIFICATION",
                "issues": issues,
                "annotation_hash_rewrite": False,
                "review_transferred": False,
            }
        )
    return candidates


def read_inventory(path):
    document = json.loads(Path(path).read_bytes())
    if (
        set(document) != {"inventory_sha256", "inventory"}
        or document["inventory"].get("format") != FORMAT
        or document["inventory_sha256"] != digest(document["inventory"])
    ):
        raise PackageConflict("migration inventory content hash/format differs")
    return document


def save_inventory(path, document):
    """Crash-safe, owner-only, immutable snapshot; exact retries reuse the same file."""
    path = Path(path)
    content = canonical(document) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".inventory-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # Validate what will actually be installed; link is an atomic no-clobber.
        read_inventory(temporary)
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != content:
                raise PackageConflict(
                    "inventory path already contains a different snapshot"
                ) from None
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        os.unlink(temporary)
