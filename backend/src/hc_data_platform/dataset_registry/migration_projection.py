"""Preserve fixed product facts while replacing verified legacy storage bindings.

Called inside the batch publication transaction. No annotation/review record or
business release is rewritten, and the dataset's business default is untouched.
"""

from psycopg import sql

from hc_data_platform.episode_store.errors import PackageConflict
from hc_data_platform.episode_store.models import canonical, digest
from hc_data_platform.security.audit import canonical_hash
from hc_data_platform.security.versioning import ResourceVersion

from .ingest_projection import PostgresDatasetIngestProjector as Projector
from .ingest_projection import _stable_id
from .models import (
    DatasetPageContentSnapshot,
    DatasetPageEpisodePackageBinding,
    DatasetPageEpisodeRecord,
    DatasetPageEpisodeRevision,
    DatasetPageEpisodeStream,
    DatasetPageManifestEntry,
    DatasetPageManifestSummary,
    DatasetPageReadyVersion,
    DatasetPageSourceProvenance,
    DatasetPageVersionCapacityFacts,
    DatasetPageVersionContentProjection,
    DatasetPageVersionManifestEntryRecord,
    DatasetPageVersionSchemaDetail,
    DatasetPageVersionSchemaSummary,
)

# Fixed identifiers only; full SQL rows bind both indexed and document facts.
TABLES = {
    "dataset_versions": "version_document",
    "dataset_version_content_projections": "content_document",
    "dataset_version_episodes": "episode_document",
    "dataset_version_episode_revisions": "revision_document",
    "dataset_version_manifest_entries": "entry_document",
    "dataset_version_schema_summaries": "schema_document",
    "dataset_version_schema_details": "schema_document",
    "dataset_version_source_provenance": "provenance_document",
    "dataset_version_capacity_facts": "capacity_document",
}


def read_projection(cursor, scope, version_id, *, lock=False):
    result = {}
    for table in TABLES:
        cursor.execute(
            sql.SQL(
                "SELECT to_jsonb(r) FROM dataset_registry.{} r WHERE organization_id=%s "
                "AND project_id=%s AND region_code=%s AND dataset_id=%s AND version_id=%s "
                "LIMIT 10001 {}"
            ).format(sql.Identifier(table), sql.SQL("FOR SHARE" if lock else "")),
            (*scope, version_id),
        )
        rows = [row[0] for row in cursor.fetchall()]
        if len(rows) > 10000:
            raise PackageConflict("migration product snapshot exceeds 10000 rows per table")
        result[table] = sorted(rows, key=canonical)
    return result


def _models(rows, table, model):
    return tuple(model.model_validate(r[TABLES[table]]) for r in rows[table])


def _single(rows, table, model):
    values = _models(rows, table, model)
    if len(values) != 1:
        raise PackageConflict(f"migration requires one complete {table} source projection")
    return values[0]


def transform_projection(rows, collection, members, *, legacy):
    """Build a reviewable projection; members contain plan/data/verified package info."""
    version_id = "version_" + collection.collection_version_id
    scope = (
        collection.organization_id,
        collection.project_id,
        collection.region_code,
        collection.dataset_id,
    )
    old = _single(rows, "dataset_versions", DatasetPageReadyVersion)
    if rows["dataset_versions"][0]["version_scope"] != "INTERNAL":
        raise PackageConflict("migration source must be the internal working version")
    for table_rows in rows.values():
        for row in table_rows:
            if (
                tuple(
                    row[k] for k in ("organization_id", "project_id", "region_code", "dataset_id")
                )
                != scope
                or row["version_id"] != old.version_id
            ):
                raise PackageConflict("migration product snapshot scope/version mismatch")

    def moved(value, **changes):
        return type(value).model_validate(
            value.model_dump(mode="python") | {"version_id": version_id, **changes}
        )

    content = _single(
        rows, "dataset_version_content_projections", DatasetPageVersionContentProjection
    )
    if content.content_snapshot != old.content_snapshot or content.manifest != old.manifest:
        raise PackageConflict("source product content and version disagree")
    episodes = sorted(
        _models(rows, "dataset_version_episodes", DatasetPageEpisodeRecord),
        key=lambda e: e.selected_revision.ordinal,
    )
    revisions = {
        r.revision_id: r
        for r in _models(rows, "dataset_version_episode_revisions", DatasetPageEpisodeRevision)
    }
    expected = [e.identity.episode_id for e in collection.episodes]
    if [e.episode_id for e in episodes] != expected or len(revisions) != len(episodes):
        raise PackageConflict("source product membership differs from migration collection")
    if tuple(e.selected_revision for e in episodes) != content.content_snapshot.revision_refs:
        raise PackageConflict("source product revision selection differs from content snapshot")
    if len({e.selected_revision.ordinal for e in episodes}) != len(episodes):
        raise PackageConflict("source product ordinals are ambiguous")
    converted = {m["data"].identity.episode_id: m for m in members}
    if len(converted) != len(members) or not set(converted) <= set(expected):
        raise PackageConflict("migration product members are duplicate or absent")
    output_episodes, output_revisions, revision_ids = [], [], {}
    for episode in episodes:
        revision = revisions.get(episode.selected_revision.revision_id)
        if (
            revision is None
            or revision.episode_id != episode.episode_id
            or revision.content_sha256 != episode.selected_revision.content_sha256
            or revision.ordinal != episode.selected_revision.ordinal
        ):
            raise PackageConflict("source product Episode/revision selection is inconsistent")
        member = converted.get(episode.episode_id)
        if member is None:
            output_episodes.append(moved(episode))
            output_revisions.append(moved(revision))
            continue
        plan, data, info = member["plan"], member["data"], member["info"]
        if (
            data not in collection.episodes
            or plan.legacy.identity != data.identity
            or data.data_version_id != plan.source.data_version_id
        ):
            raise PackageConflict("migration member differs from the planned replacement")
        mapping = info["hc.feature_mapping"]
        start, end = (
            int(revision.started_at_ns),
            int(revision.started_at_ns) + int(revision.duration_ns),
        )
        if start != member["first_timestamp_ns"] or not start <= member["last_timestamp_ns"] < end:
            raise PackageConflict("migration product time range differs from source timestamps")
        if set(mapping) != {s.channel_path for s in revision.streams}:
            raise PackageConflict("migration package and old product channels differ")
        streams = []
        for stream in revision.streams:
            if int(stream.t_start_ns) != start or int(stream.t_end_ns) != end:
                raise PackageConflict("migration stream time range requires a derived identity")
            binding = stream.data_binding or stream.aligned_media_binding
            if (
                binding is None
                or stream.package_binding is not None
                or binding.rollout_id != data.identity.rollout_id
                or (stream.data_binding and stream.data_binding.modality_key != stream.channel_path)
            ):
                raise PackageConflict("migration requires exact old stream bindings")
            source_version = (
                binding.lance_version if stream.data_binding else binding.dataset_version
            )
            lineage = legacy.lineage(
                data.identity.dataset_id,
                data.identity.rollout_id,
                project_id=data.identity.project_id,
                version=source_version,
            )
            if (
                source_version > plan.legacy.dataset_version
                or lineage.fragment_content_hash != plan.legacy.content_hash
                or lineage.schema_snapshot_id != plan.legacy.schema_snapshot_id
                or binding.start_step != 0
                or binding.end_step != data.frame_count
            ):
                raise PackageConflict(
                    "legacy stream window/version differs; use a derived identity"
                )
            feature = mapping[stream.channel_path]
            value_kind = stream.data_binding.value_kind if stream.data_binding else None
            video = info["features"][feature].get("dtype") == "video"
            if video != (value_kind is None):
                raise PackageConflict("legacy stream modality differs from verified package")
            streams.append(
                DatasetPageEpisodeStream.model_validate(
                    stream.model_dump(mode="python")
                    | {
                        "kind": "DEPTH"
                        if info["features"][feature].get("info", {}).get("is_depth_map")
                        else stream.kind,
                        "data_binding": None,
                        "aligned_media_binding": None,
                        "package_binding": DatasetPageEpisodePackageBinding(
                            data=data,
                            rollout_id=data.identity.rollout_id,
                            feature_key=feature,
                            modality_key=stream.channel_path,
                            value_kind=value_kind,
                            start_step=0,
                            end_step=data.frame_count,
                        ),
                    }
                )
            )
        new_id = _stable_id("revision", data.data_version_id)
        revision_ids[revision.revision_id] = new_id
        selected = episode.selected_revision.model_copy(
            update={"revision_id": new_id, "content_sha256": data.content_hash}
        )
        output_episodes.append(
            moved(
                episode,
                selected_revision=selected,
                review_status=None,
                review_finding_count="0",
                has_finding=False,
                change_type="MIGRATED",
            )
        )
        output_revisions.append(
            moved(
                revision,
                revision_id=new_id,
                content_sha256=data.content_hash,
                streams=tuple(streams),
            )
        )

    # Keep schema, robot models, calibration, source manifests, time and inclusion.
    snapshot_fields = content.content_snapshot.model_dump(mode="python") | {
        "content_snapshot_id": collection.collection_version_id,
        "revision_refs": tuple(e.selected_revision for e in output_episodes),
    }
    snapshot_fields["content_snapshot_hash"] = canonical_hash(
        {
            k: v
            for k, v in DatasetPageContentSnapshot(**snapshot_fields)
            .model_dump(mode="json")
            .items()
            if k not in {"content_snapshot_id", "content_snapshot_hash"}
        }
    )
    snapshot = DatasetPageContentSnapshot(**snapshot_fields)
    entries = []
    for entry in _models(rows, "dataset_version_manifest_entries", DatasetPageManifestEntry):
        if entry.episode_id in converted:
            member = converted[entry.episode_id]
            if entry.role != "REVISION" or entry.revision_id not in revision_ids:
                raise PackageConflict(
                    "migration cannot silently replace unverified product attachments"
                )
            entry = entry.model_copy(
                update={
                    "entry_id": "entry-" + revision_ids[entry.revision_id],
                    "revision_id": revision_ids[entry.revision_id],
                    "sha256": member["data"].content_hash,
                    "size_bytes": str(member["size_bytes"]),
                }
            )
        entries.append(entry)
    if {e.episode_id for e in entries if e.role == "REVISION"} != set(expected):
        raise PackageConflict("migration product manifest is incomplete")
    entries.sort(key=lambda e: e.entry_id)
    manifest = DatasetPageManifestSummary(
        manifest_id="manifest-" + collection.collection_version_id,
        format_version="dataset-manifest/v1",
        canonicalization="rfc8785-json",
        sha256=canonical_hash([e.model_dump(mode="json") for e in entries]),
        entry_count=str(len(entries)),
    )
    token = "episode-collection:" + collection.content_hash
    page = moved(
        old,
        display_version=f"v{collection.version}",
        created_at=collection.created_at,
        published_at=collection.created_at,
        etag=ResourceVersion().etag,
        version_token=token,
        content_snapshot=snapshot,
        manifest=manifest,
        approved_review_decision_id=None,
        allowed_actions=(),
    )
    capacity = _single(rows, "dataset_version_capacity_facts", DatasetPageVersionCapacityFacts)
    schema_detail = _single(rows, "dataset_version_schema_details", DatasetPageVersionSchemaDetail)
    streams_by_channel = {s.channel_path: s for r in output_revisions for s in r.streams}
    if {c.name for c in schema_detail.channels} != set(streams_by_channel):
        raise PackageConflict("source product schema channels differ from Episode streams")
    schema_detail = moved(
        schema_detail,
        channels=tuple(
            c.model_copy(
                update={
                    "data_type": streams_by_channel[c.name].package_binding.value_kind or "video"
                }
            )
            if streams_by_channel[c.name].package_binding
            else c
            for c in schema_detail.channels
        ),
    )
    result = {
        "version": page,
        "content": moved(
            content, content_snapshot=snapshot, manifest=manifest, operational_revision=token
        ),
        "episodes": tuple(output_episodes),
        "revisions": tuple(output_revisions),
        "entries": tuple(
            DatasetPageVersionManifestEntryRecord(
                scope=old.scope, dataset_id=old.dataset_id, version_id=version_id, entry=e
            )
            for e in entries
        ),
        "schema_summary": moved(
            _single(rows, "dataset_version_schema_summaries", DatasetPageVersionSchemaSummary)
        ),
        "schema_detail": schema_detail,
        "provenance": tuple(
            moved(p)
            for p in _models(rows, "dataset_version_source_provenance", DatasetPageSourceProvenance)
        ),
        "capacity": moved(
            capacity,
            state="PARTIAL",
            required_physical_bytes=None,
            actual_oss_bytes=None,
            calculated_at=collection.created_at,
            basis_revision=token,
        ),
    }
    return result


def write_projection(cursor, result):
    Projector._put_version(cursor, result["version"])
    Projector._put_content(cursor, result["content"])
    for name, method in (
        ("episodes", Projector._put_episode),
        ("revisions", Projector._put_revision),
        ("entries", Projector._put_manifest_entry),
        ("provenance", Projector._put_provenance),
    ):
        for value in result[name]:
            method(cursor, value)
    Projector._put_schema(cursor, result["schema_summary"], result["schema_detail"])
    Projector._put_capacity(cursor, result["capacity"])


def projection_hash(result):
    return digest(
        {
            k: [v.model_dump(mode="json") for v in value]
            if isinstance(value, tuple)
            else value.model_dump(mode="json")
            for k, value in result.items()
        }
    )
