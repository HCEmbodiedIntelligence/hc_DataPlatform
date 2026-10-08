"""Publish a committed Tag child with its own product identity and review state."""

from datetime import timedelta

from hc_data_platform.alignment.models import AlignmentInputV1
from hc_data_platform.core.context import current_request_context
from hc_data_platform.episode_store.catalog_models import CollectionVersion, EpisodeDerivedReady
from hc_data_platform.episode_store.derivation_models import TagEpisodePlan
from hc_data_platform.episode_store.scope import assert_scope

from .ingest_projection import (
    DatasetIngestProjectionConflict,
    TagEpisodeProjectionSource,
    _ProjectionFacts,
    _stable_id,
    _target,
)
from .models import (
    DatasetPageEpisodeRecord,
    DatasetPageEpisodeRevision,
    DatasetPageSourceProvenance,
)


def parent_projection(projector, cursor, plan, parent_collection_version_id):
    """The explicit immutable parent collection supplies facts, never a latest pointer."""
    data = plan.parent
    identity = data.identity
    collection = projector._episode_packages.collections.get(
        identity.dataset_id, collection_version_id=parent_collection_version_id
    )
    if data not in collection.episodes:
        raise DatasetIngestProjectionConflict("selected parent collection differs from Tag data")
    key = (*identity.scope, identity.dataset_id, collection.version_id)
    cursor.execute(
        """SELECT e.episode_document,r.revision_document
        FROM dataset_registry.dataset_version_episodes e
        JOIN dataset_registry.dataset_version_episode_revisions r
          USING(organization_id,project_id,region_code,dataset_id,version_id,episode_id)
        WHERE e.organization_id=%s AND e.project_id=%s AND e.region_code=%s
          AND e.dataset_id=%s AND e.version_id=%s AND e.episode_id=%s""",
        (*key, identity.episode_id),
    )
    row = cursor.fetchone()
    if row is None:
        raise DatasetIngestProjectionConflict("parent Episode product projection is unavailable")
    episode = DatasetPageEpisodeRecord.model_validate(row[0])
    revision = DatasetPageEpisodeRevision.model_validate(row[1])
    if (
        episode.selected_revision.revision_id != revision.revision_id
        or episode.selected_revision.content_sha256 != data.content_hash
        or revision.content_sha256 != data.content_hash
        or not revision.streams
        or any(
            s.package_binding is None or s.package_binding.data != data for s in revision.streams
        )
        or int(revision.started_at_ns) != plan.parent_origin_timestamp_ns
    ):
        raise DatasetIngestProjectionConflict(
            "parent projection differs from its exact data binding"
        )
    parent = projector._episode_packages.files.open(identity, data.data_version_id)
    if parent.manifest.input != plan.parent_input or parent.receipt.data != data:
        raise DatasetIngestProjectionConflict("parent package differs from the frozen Tag input")
    origin = parent.document("hc/source.json")
    # Duplicated Raw bytes can have distinct uploads. Use a recorded upload/manifest
    # when present and reject ambiguity rather than attaching the wrong provenance.
    cursor.execute(
        """SELECT provenance_document FROM dataset_registry.dataset_version_source_provenance
        WHERE organization_id=%s AND project_id=%s AND region_code=%s
          AND dataset_id=%s AND version_id=%s
          AND provenance_document->>'verified_object_set_hash'=%s""",
        (*key, plan.parent_input.source_sha256),
    )
    candidates = [DatasetPageSourceProvenance.model_validate(r[0]) for r in cursor.fetchall()]
    original = origin.get("original_manifest", {})
    for name, field in (
        ("upload_id", "upload_id"),
        ("session_id", "upload_id"),
        ("recording_upload_id", "upload_id"),
        ("raw_upload_id", "upload_id"),
        ("data_package_id", "source_manifest_id"),
        ("manifest_fingerprint", "source_manifest_sha256"),
    ):
        if origin.get(name):
            candidates = [p for p in candidates if getattr(p, field) == str(origin[name])]
    if original.get("data_package_id"):
        candidates = [p for p in candidates if p.source_manifest_id == original["data_package_id"]]
    if len(candidates) != 1:
        raise DatasetIngestProjectionConflict("parent Raw provenance is missing or ambiguous")
    return episode, revision, candidates[0]


def derived_streams(packages, package, plan, parent_revision):
    """A selected frame interval keeps its parent's exact nanosecond boundaries.

    Do not infer frame count with ceil(duration * fps): selecting a parent frame
    starting at a rounded nanosecond can make that calculation off by one.
    """
    end = plan.next_parent_timestamp_ns
    if end is None:
        end = int(parent_revision.started_at_ns) + int(parent_revision.duration_ns)
    data = package.receipt.data
    first = packages.steps.read_steps(data.identity, data.data_version_id, 0, 1).steps[0]
    last = packages.steps.read_steps(
        data.identity, data.data_version_id, data.frame_count - 1, data.frame_count
    ).steps[0]
    if (
        first.timestamp_ns != plan.start_timestamp_ns
        or not first.timestamp_ns <= last.timestamp_ns < end
    ):
        raise DatasetIngestProjectionConflict("child time range differs from the parent interval")
    info = package.document("meta/info.json")
    mapping = info["hc.feature_mapping"]
    if info["fps"] != plan.schema_snapshot.frequency_hz or set(mapping) != {
        s.channel_path for s in parent_revision.streams
    }:
        raise DatasetIngestProjectionConflict("child channels differ from its parent projection")
    streams = []
    for stream in parent_revision.streams:
        binding = stream.package_binding
        if (
            binding.feature_key != mapping[stream.channel_path]
            or binding.start_step != 0
            or binding.end_step != plan.parent.frame_count
            or int(stream.t_start_ns) != int(parent_revision.started_at_ns)
            or int(stream.t_end_ns)
            != int(parent_revision.started_at_ns) + int(parent_revision.duration_ns)
        ):
            raise DatasetIngestProjectionConflict("parent stream window is incomplete")
        streams.append(
            stream.model_copy(
                update={
                    "episode_stream_id": _stable_id(
                        "stream", data.identity.episode_id, stream.channel_path
                    ),
                    "t_start_ns": str(first.timestamp_ns),
                    "t_end_ns": str(end),
                    "package_binding": binding.model_copy(
                        update={
                            "data": data,
                            "rollout_id": data.identity.rollout_id,
                            "end_step": data.frame_count,
                        }
                    ),
                }
            )
        )
    return tuple(streams), end


def project_tag_episode(projector, *, plan, parent_collection_version_id, version, ready):
    plan = TagEpisodePlan.model_validate(plan.model_dump(mode="json"))
    assert_scope(plan.parent.identity)
    if not current_request_context().service_identity:
        raise PermissionError("Tag product publication requires the exact Worker scope")
    packages = projector._episode_packages
    if not isinstance(version, CollectionVersion) or not isinstance(ready, EpisodeDerivedReady):
        raise DatasetIngestProjectionConflict(
            "Tag publication requires independent Episode storage"
        )
    package = packages.files.open(plan.input.identity, plan.input.data_version_id)
    if (
        package.manifest.input != plan.input
        or package.receipt.data != ready.data
        or ready.source_sha256 != plan.input.source_sha256
        or ready.data.frame_count != plan.frame_count
        or package.document("hc/derivation/plan.json") != plan.model_dump(mode="json")
        or version.project_id != plan.parent.identity.project_id
        or version.dataset_id != plan.parent.identity.dataset_id
        or version.schema_snapshot_id != plan.schema_snapshot.schema_snapshot_id
        or version.schema_fingerprint != plan.schema_snapshot.fingerprint
        or version.frequency_hz != plan.schema_snapshot.frequency_hz
        or version.version != ready.dataset_version
        or ready.content_hash != version.content_hash
    ):
        raise DatasetIngestProjectionConflict(
            "child product lineage differs from the committed Tag plan"
        )
    identity = plan.input.identity
    target = _target(None, version, ready)
    with packages.repository.transaction() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            ("/".join((*identity.scope, identity.dataset_id)),),
        )
        parent, revision, provenance = parent_projection(
            projector, cursor, plan, parent_collection_version_id
        )
        source = TagEpisodeProjectionSource(
            organization_id=identity.organization_id,
            project_id=identity.project_id,
            region_code=identity.region_code,
            rollout_id=identity.rollout_id,
            source_sha256=plan.input.source_sha256,
            data_package_id=provenance.source_manifest_id,
            manifest_fingerprint=provenance.source_manifest_sha256,
        )
        if not projector._commit_matches(source, version, ready):
            raise DatasetIngestProjectionConflict("Tag collection differs from the stored commit")
        existing = projector._existing_target(cursor, source=source, version=version, target=target)
        if existing is not None:
            return existing
        streams, end = derived_streams(packages, package, plan, revision)
        if not parent.task or not parent.robot_id:
            raise DatasetIngestProjectionConflict("parent task/robot provenance is unavailable")
        projector._assert_task_dataset(
            cursor, source=source, collection_task_id=parent.task, dataset_id=identity.dataset_id
        )
        projector._insert_projection(
            cursor,
            source=source,
            alignment=AlignmentInputV1(
                rollout_id=identity.rollout_id,
                source_sha256=source.source_sha256,
                attempt_id=plan.input.data_version_id,
                start_ns=plan.start_timestamp_ns,
                end_ns=end,
                streams={
                    s.channel_path: {
                        "kind": "image" if s.kind in {"DEPTH", "RGB_VIDEO"} else "continuous",
                        "samples": [],
                    }
                    for s in streams
                },
            ),
            schema_snapshot_id=plan.schema_snapshot.schema_snapshot_id,
            frequency_hz=plan.schema_snapshot.frequency_hz,
            version=version,
            ready=ready,
            target=target,
            facts=_ProjectionFacts(
                collection_task_id=parent.task,
                robot_id=parent.robot_id,
                started_at=None
                if parent.started_at is None
                else parent.started_at
                + timedelta(
                    microseconds=(plan.start_timestamp_ns - plan.parent_origin_timestamp_ns) // 1000
                ),
                camera_topics=frozenset(
                    s.channel_path for s in streams if s.kind in {"DEPTH", "RGB_VIDEO"}
                ),
                # Existing Raw is already counted. This command uploads no new Raw.
                source_size_bytes=0,
                upload_id=provenance.upload_id,
            ),
            media_artifacts=(),
            derived_streams=streams,
            source_provenance=provenance.model_copy(update={"version_id": version.version_id}),
            entry_size_bytes=sum(e.size for e in package.manifest.files),
        )
    return target
