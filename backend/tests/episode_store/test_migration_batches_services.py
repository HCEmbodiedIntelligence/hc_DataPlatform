"""Two actual historical Lance Episodes, RGB-D objects and atomic product cutover.

Inputs are synthetic. This is software rehearsal, not real-recording acceptance.
"""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from hc_data_platform.alignment.models import AlignmentInputV1
from hc_data_platform.dataset_registry.ingest_projection import (
    PostgresDatasetIngestProjector,
    _ProjectionFacts,
    _stable_id,
    _target,
)
from hc_data_platform.dataset_registry.migration_projection import read_projection
from hc_data_platform.dataset_registry.models import (
    DatasetPageEpisodeAlignedMediaBinding,
    DatasetPageEpisodeDataBinding,
    DatasetPageEpisodeStream,
    DatasetPageScope,
)
from hc_data_platform.dataset_registry.repository import PostgresDatasetPageRepository
from hc_data_platform.episode_store.catalog import PostgresLegacyEntries
from hc_data_platform.episode_store.collections import EpisodeCollections
from hc_data_platform.episode_store.commit import EpisodePackageCommitter
from hc_data_platform.episode_store.migration_batches import MigrationBatches
from hc_data_platform.episode_store.migration_conversion import LegacyEpisodeConverter, freeze_plan
from hc_data_platform.episode_store.migration_inventory import capture_inventory
from hc_data_platform.episode_store.models import canonical, digest
from hc_data_platform.episode_store.postgres import PackageConflict
from hc_data_platform.lance_catalog.adapters import (
    LanceAdapter,
    PostgresAdvisoryDatasetLock,
    PostgresCatalogAdapter,
)
from hc_data_platform.lance_catalog.models import (
    AlignedFragmentManifestV1,
    DatasetSchemaSnapshot,
    StepRecord,
)
from hc_data_platform.lance_catalog.service import LanceCatalogService, compute_fragment_hash
from hc_data_platform.publishing.depth_evidence import DepthEvidenceVideo
from hc_data_platform.workflow.models import ContinuousEpisodeProjectionSourceV1
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_writer import build_rgbd_package, identity

pytestmark = pytest.mark.integration


@pytest.fixture
def batch_source(services, tmp_path, request, monkeypatch):
    packages, storage = services
    run = uuid4().hex
    logical_media = getattr(request, "param", {}).get("logical_media", False)
    persistent = logical_media or request.node.name in {
        "test_batch_publish_rollback_roundtrip",
        "test_batch_cli_and_retained_downloads",
        "test_real_logical_http_reads_native_and_legacy_with_identical_contract",
    }
    root = Path("/app/.g1-artifacts/migration-batches") / run if persistent else tmp_path
    root.mkdir(parents=True, exist_ok=True)
    base = identity().model_copy(
        update={
            "organization_id": "batch-" + run,
            "project_id": "batch-project-" + run,
            "dataset_id": "dataset_batch_" + run,
        }
    )
    adapter = LanceAdapter(root / "legacy")
    legacy = LanceCatalogService(
        adapter,
        PostgresCatalogAdapter(packages.connections),
        PostgresAdvisoryDatasetLock(packages.connections),
    )
    projector = PostgresDatasetIngestProjector(packages.connections)
    sources, assets_by_id = [], {}
    now = datetime.now(timezone.utc)
    schema = DatasetSchemaSnapshot.create(
        project_id=base.project_id,
        dataset_id=base.dataset_id,
        schema_snapshot_id="schema-1",
        frequency_hz=30,
        fields={
            "/observation/state": "list<float64>",
            "action": "list<float64>",
            "observation.images.head_rgb": "json",
            "observation.images.head_depth": "json",
        },
    )
    for index in range(2):
        rollout = "batch-rollout-" + str(index)
        ident = base.model_copy(
            update={"rollout_id": rollout, "episode_id": _stable_id("episode", rollout)}
        )
        input_dir = root / f"source-{index}"
        input_dir.mkdir()
        _, source, _, assets = build_rgbd_package(input_dir, ident)
        rows = tuple(
            StepRecord(
                rollout_id=rollout,
                step_index=i,
                timestamp_ns=i * 33_333_333,
                modalities={
                    "/observation/state": [index + 0.1 + i * 1e-12],
                    "action": [float(i)],
                    **{name: {"frame_index": i} for name in assets.videos},
                },
                valid={name: True for name in schema.fields},
                source_timestamps_ns={"observation.images.head_depth": (i * 33_333_333,)},
                time_error_ns={"observation.images.head_depth": 0},
            )
            for i in range(4)
        )
        rows = tuple(
            row.model_copy(
                update={
                    key: {name: getattr(row, key).get(name, default) for name in row.modalities}
                    for key, default in (
                        ("valid", False),
                        ("repeated", False),
                        ("time_error_ns", None),
                        ("source_timestamps_ns", ()),
                    )
                }
            )
            for row in rows
        )
        sources.append(source)
        assets_by_id[ident.episode_id] = assets
        with scoped(source):
            if index == 0:
                with packages.transaction() as cursor:
                    cursor.execute(
                        """INSERT INTO registry.organization_projects
                        (organization_id,project_id,display_name)
                        VALUES (%s,%s,'G1 batch fixture')""",
                        (ident.organization_id, ident.project_id),
                    )
                legacy.register_schema(schema)
            version, ready = legacy.commit_fragment(
                AlignedFragmentManifestV1(
                    project_id=ident.project_id,
                    dataset_id=ident.dataset_id,
                    schema_snapshot_id=schema.schema_snapshot_id,
                    schema_fingerprint=schema.fingerprint,
                    frequency_hz=30,
                    rollout_id=rollout,
                    source_sha256=source.source_sha256,
                    converter_version="old-batch-fixture/1",
                    attempt_id="old",
                    fragment_uri="file:///isolated-batch-fixture",
                    step_count=len(rows),
                    content_hash=compute_fragment_hash(rows),
                ),
                rows,
            )
            projection_source = ContinuousEpisodeProjectionSourceV1(
                organization_id=ident.organization_id,
                project_id=ident.project_id,
                region_code=ident.region_code,
                recording_id="recording-" + run,
                recording_upload_id="upload-" + run,
                episode_id=ident.episode_id,
                rollout_id=rollout,
                data_package_id="raw-" + rollout,
                collection_task_id="task-batch",
                robot_id="fixture-robot",
                source_sha256=source.source_sha256,
                manifest_fingerprint="7" * 64,
                source_size_bytes=1234,
                started_at=now,
                ended_at=now,
                camera_modality_keys=tuple(assets.videos),
            )
            media_ids = {}
            if logical_media:
                from tests.episode_store.logical_media_fixture import publish_media

                media_ids = publish_media(packages, storage, source, version, assets)
            streams = []
            for name in schema.fields:
                camera = name in assets.videos
                streams.append(
                    DatasetPageEpisodeStream(
                        episode_stream_id=_stable_id("stream", ident.episode_id, name),
                        channel_path=name,
                        kind="DEPTH"
                        if name.endswith("depth")
                        else "RGB"
                        if camera
                        else "ACTION"
                        if name == "action"
                        else "JOINT_STATE",
                        t_start_ns="0",
                        t_end_ns="133333333",
                        aligned_media_binding=DatasetPageEpisodeAlignedMediaBinding(
                            rollout_id=rollout,
                            dataset_version=version.version,
                            artifact_id=media_ids.get(name, "fixture-" + name),
                            camera_id=name,
                            start_step=0,
                            end_step=4,
                        )
                        if camera
                        else None,
                        data_binding=None
                        if camera
                        else DatasetPageEpisodeDataBinding(
                            rollout_id=rollout,
                            lance_version=version.version,
                            modality_key=name,
                            value_kind="VECTOR",
                            start_step=0,
                            end_step=4,
                        ),
                    )
                )
            with packages.transaction() as cursor:
                projector._insert_projection(
                    cursor,
                    source=projection_source,
                    alignment=AlignmentInputV1(
                        rollout_id=rollout,
                        source_sha256=source.source_sha256,
                        attempt_id="old",
                        start_ns=0,
                        end_ns=133333333,
                        streams={
                            name: {
                                "kind": "image" if name in assets.videos else "continuous",
                                "samples": [],
                            }
                            for name in schema.fields
                        },
                    ),
                    schema_snapshot_id=schema.schema_snapshot_id,
                    frequency_hz=30,
                    version=version,
                    ready=ready,
                    target=_target(projection_source, version, ready),
                    facts=_ProjectionFacts(
                        "task-batch",
                        "fixture-robot",
                        now,
                        frozenset(assets.videos),
                        1234,
                        "upload-" + run,
                    ),
                    media_artifacts=(),
                    derived_streams=tuple(streams),
                )
    collections = EpisodeCollections(
        packages, storage, legacy=legacy, legacy_entries=PostgresLegacyEntries(packages, legacy)
    )
    with scoped(sources[0]):
        inventory = capture_inventory(packages.connections, base.dataset_id)
        references = collections.legacy_entries(version)
        plans = tuple(
            freeze_plan(
                inventory,
                ref,
                legacy,
                schema,
                assets_by_id[ref.identity.episode_id],
                metadata={"task": "isolated batch fixture"},
            )
            for ref in references
        )

        def forbid(*args, **kwargs):
            raise AssertionError("batch migration cannot write Lance or encode depth again")

        monkeypatch.setattr(adapter, "stage_attempt", forbid)
        monkeypatch.setattr(adapter, "commit_staged", forbid)
        monkeypatch.setattr(DepthEvidenceVideo, "write", forbid)
        converter = LegacyEpisodeConverter(
            legacy, EpisodePackageCommitter(packages, storage), root / "spool"
        )
        staged = [
            converter.stage(plan, assets_by_id[plan.legacy.identity.episode_id]) for plan in plans
        ]
        roots = {plan.sha256: item[2] for plan, item in zip(plans, staged, strict=True)}
    return MigrationBatches(collections), plans, roots, root, inventory


def working(batches, plan):
    ident = plan.legacy.identity
    return PostgresDatasetPageRepository(batches.packages.connections).working_version_id(
        scope=DatasetPageScope(
            organization_id=ident.organization_id,
            project_id=ident.project_id,
            region_code=ident.region_code,
        ),
        dataset_id=ident.dataset_id,
    )


def assert_baseline(batches, plans):
    assert batches.collections.current(plans[0].legacy.identity.dataset_id) is None
    assert working(batches, plans[0]) == "version_lance_2"
    assert all(batches.packages.current(p.legacy.identity) is None for p in plans)


def test_batch_publish_rollback_roundtrip(batch_source):
    batches, plans, roots, root, inventory = batch_source
    dataset = plans[0].legacy.identity.dataset_id
    with scoped(plans[0].source):
        prepared = batches.prepare(dataset, "roundtrip", plans, roots)
        assert prepared["status"] == "PREPARED"
        assert len(prepared["plan"]["previews"]) == 4
        assert (
            sum(
                p["kind"] == "depth-preview" and p["colorizer"] is not None
                for p in prepared["plan"]["previews"]
            )
            == 2
        )
        assert batches.prepare(dataset, "roundtrip", plans, roots) == prepared
        assert_baseline(batches, plans)
        published = batches.publish(dataset, prepared["batch_id"], roots)
        assert published["status"] == "PUBLISHED"
        version = batches.collections.current(dataset)
        assert version.version == 3 and len(version.episodes) == 2
        assert working(batches, plans[0]) == version.version_id
        assert all(
            batches.packages.current(p.legacy.identity).data.data_version_id
            == p.source.data_version_id
            for p in plans
        )
        with batches.packages.transaction() as cursor:
            projected = read_projection(
                cursor, (*plans[0].legacy.identity.scope, dataset), version.version_id
            )
        for row in projected["dataset_version_episode_revisions"]:
            revision = row["revision_document"]
            assert all(
                s["package_binding"] and not s["data_binding"] and not s["aligned_media_binding"]
                for s in revision["streams"]
            )
            assert revision["started_at_ns"] == "0" and revision["duration_ns"] == "133333333"
        assert all(
            r["episode_document"]["review_status"] is None
            for r in projected["dataset_version_episodes"]
        )
        assert batches.publish(dataset, prepared["batch_id"], {}) == published
        rolled = batches.rollback(dataset, prepared["batch_id"])
        assert rolled["status"] == "ROLLED_BACK"
        assert_baseline(batches, plans)
        assert batches.rollback(dataset, prepared["batch_id"]) == rolled
        assert batches.publish(dataset, prepared["batch_id"], roots) == rolled
        assert batches.prepare(dataset, "roundtrip", plans, roots) == rolled
        assert batches.collections.get(dataset, version=3) == version
        assert all(batches.packages.get(p.legacy.identity, p.source.data_version_id) for p in plans)
        after = capture_inventory(batches.packages.connections, dataset)
        for table, original in inventory["inventory"]["tables"].items():
            current_rows = after["inventory"]["tables"][table]
            assert all(row in current_rows for row in original), table
        report = {
            "synthetic_inputs": True,
            "real_recording_acceptance": False,
            "batch": rolled,
            "legacy_retained": True,
            "original_inventory_rows_retained": True,
            "roots": {k: str(v) for k, v in roots.items()},
            "projected_rows_sha256": digest(projected),
        }
        (root / "result.json").write_bytes(canonical(report))
        Path("/app/.g1-artifacts/migration-batch-latest.txt").write_text(str(root))


@pytest.mark.parametrize(
    "fault",
    [
        "batch_previews_prepared",
        "batch_prepared",
        "batch_collection_object_verified",
        "batch_pointers_written",
        "batch_product_written",
        "batch_database_committed",
    ],
)
def test_batch_interruption_retry_is_atomic(batch_source, fault):
    batches, plans, roots, _, _ = batch_source
    dataset = plans[0].legacy.identity.dataset_id

    def interrupt(stage):
        if stage == fault:
            raise InterruptedError("isolated migration checkpoint fault")

    failing = MigrationBatches(batches.collections, checkpoint=interrupt)
    with scoped(plans[0].source):
        if fault in {"batch_prepared", "batch_previews_prepared"}:
            with pytest.raises(InterruptedError):
                failing.prepare(dataset, "fault", plans, roots)
            prepared = batches.prepare(dataset, "fault", plans, roots)
        else:
            prepared = batches.prepare(dataset, "fault", plans, roots)
            with pytest.raises(InterruptedError):
                failing.publish(dataset, prepared["batch_id"], roots)
        if fault != "batch_database_committed":
            assert_baseline(batches, plans)
        result = batches.publish(dataset, prepared["batch_id"], roots)
        assert result["status"] == "PUBLISHED"
        assert len(batches.collections.list_versions(dataset)) == 1
        assert batches.publish(dataset, prepared["batch_id"], roots) == result


@pytest.mark.parametrize(
    "fault", ["batch_rollback_pointers_written", "batch_rollback_database_committed"]
)
def test_batch_rollback_interruption_retry(batch_source, fault):
    batches, plans, roots, _, _ = batch_source
    dataset = plans[0].legacy.identity.dataset_id

    def interrupt(stage):
        if stage == fault:
            raise InterruptedError("isolated rollback checkpoint fault")

    with scoped(plans[0].source):
        prepared = batches.prepare(dataset, "rollback", plans, roots)
        batches.publish(dataset, prepared["batch_id"], roots)
        with pytest.raises(InterruptedError):
            MigrationBatches(batches.collections, checkpoint=interrupt).rollback(
                dataset, prepared["batch_id"]
            )
        assert batches.get(dataset, prepared["batch_id"])["status"] == (
            "PUBLISHED" if fault == "batch_rollback_pointers_written" else "ROLLED_BACK"
        )
        assert batches.rollback(dataset, prepared["batch_id"])["status"] == "ROLLED_BACK"
        assert_baseline(batches, plans)


def test_concurrent_batch_preparation_and_publication(batch_source):
    batches, plans, roots, _, _ = batch_source
    dataset = plans[0].legacy.identity.dataset_id

    def run(_):
        with scoped(plans[0].source):
            prepared = batches.prepare(dataset, "concurrent", plans, roots)
            return batches.publish(dataset, prepared["batch_id"], roots)

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(run, range(3)))
    assert all(result == results[0] for result in results)
    with scoped(plans[0].source):
        assert len(batches.collections.list_versions(dataset)) == 1


@pytest.mark.parametrize("field", ["organization_id", "project_id", "region_code"])
def test_batch_scope_is_exact(batch_source, field):
    batches, plans, roots, _, _ = batch_source
    dataset = plans[0].legacy.identity.dataset_id
    with scoped(plans[0].source):
        prepared = batches.prepare(dataset, "scope", plans, roots)
    foreign = plans[0].source.model_copy(
        update={"identity": plans[0].source.identity.model_copy(update={field: "foreign"})}
    )
    with scoped(foreign):
        with pytest.raises(KeyError):
            batches.get(dataset, prepared["batch_id"])
        with pytest.raises(PermissionError):
            batches.prepare(dataset, "scope", plans, roots)


def test_changed_member_pointer_refuses_publish_and_rollback(batch_source):
    batches, plans, roots, _, _ = batch_source
    dataset = plans[0].legacy.identity.dataset_id
    with scoped(plans[0].source):
        prepared = batches.prepare(dataset, "cas", plans, roots)
        with batches.packages.transaction() as cursor:
            cursor.execute(
                """UPDATE episode_store.bindings SET current_data_version_id=%s
                WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s
                  AND episode_id=%s""",
                (plans[0].source.data_version_id, *plans[0].legacy.identity.key),
            )
        with pytest.raises(PackageConflict, match="baseline changed"):
            batches.publish(dataset, prepared["batch_id"], roots)
        assert batches.collections.current(dataset) is None
        with batches.packages.transaction() as cursor:
            cursor.execute(
                """UPDATE episode_store.bindings SET current_data_version_id=NULL
                WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s
                  AND episode_id=%s""",
                plans[0].legacy.identity.key,
            )
        batches.publish(dataset, prepared["batch_id"], roots)
        with batches.packages.transaction() as cursor:
            cursor.execute(
                """UPDATE episode_store.bindings SET current_data_version_id=NULL
                WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s
                  AND episode_id=%s""",
                plans[0].legacy.identity.key,
            )
        with pytest.raises(PackageConflict, match="pointer changed"):
            batches.rollback(dataset, prepared["batch_id"])


def test_mixed_batches_preserve_previous_head_and_reject_later_work(batch_source):
    batches, plans, roots, _, _ = batch_source
    dataset = plans[0].legacy.identity.dataset_id
    with scoped(plans[0].source):
        first = batches.prepare(
            dataset, "first", plans[:1], {plans[0].sha256: roots[plans[0].sha256]}
        )
        first = batches.publish(
            dataset, first["batch_id"], {plans[0].sha256: roots[plans[0].sha256]}
        )
        mixed = batches.collections.current(dataset)
        assert {e.storage_format for e in mixed.episodes} == {"lance", "lerobot_v3"}
        second = batches.prepare(
            dataset, "second", plans[1:], {plans[1].sha256: roots[plans[1].sha256]}
        )
        batches.publish(dataset, second["batch_id"], {plans[1].sha256: roots[plans[1].sha256]})
        with pytest.raises(PackageConflict, match="hide later work"):
            batches.rollback(dataset, first["batch_id"])
        batches.rollback(dataset, second["batch_id"])
        assert batches.collections.current(dataset) == mixed
        assert working(batches, plans[0]) == mixed.version_id
        assert batches.packages.current(plans[0].legacy.identity) is not None
        assert batches.packages.current(plans[1].legacy.identity) is None
        batches.rollback(dataset, first["batch_id"])
        assert_baseline(batches, plans)


def test_competing_batch_freeze_cannot_reuse_archived_number(batch_source):
    batches, plans, roots, _, _ = batch_source
    dataset = plans[0].legacy.identity.dataset_id
    with scoped(plans[0].source):
        first = batches.prepare(dataset, "one", plans, roots)
        competing = batches.prepare(dataset, "two", plans, roots)
        batches.publish(dataset, first["batch_id"], roots)
        with pytest.raises(PackageConflict, match="baseline head changed"):
            batches.publish(dataset, competing["batch_id"], roots)
        batches.rollback(dataset, first["batch_id"])
        with pytest.raises(PackageConflict, match="version allocation changed"):
            batches.publish(dataset, competing["batch_id"], roots)
        assert_baseline(batches, plans)


def test_object_corruption_prevents_batch_visibility(batch_source):
    batches, plans, roots, _, _ = batch_source
    dataset = plans[0].legacy.identity.dataset_id
    with scoped(plans[0].source):
        prepared = batches.prepare(dataset, "corrupt", plans, roots)
        # Own test object only; retain original bytes and restore before returning.
        key = plans[0].source.package_prefix + "/hc/quality.json"
        original = b"".join(batches.storage.read_chunks(key))
        try:
            batches.storage.put_json(key, {"status": "PASS", "rules": []}, if_none_match=False)
            with pytest.raises(PackageConflict, match="size|hash"):
                batches.publish(dataset, prepared["batch_id"], roots)
            assert_baseline(batches, plans)
        finally:
            batches.storage.put_json(key, json.loads(original), if_none_match=False)
        assert batches.publish(dataset, prepared["batch_id"], roots)["status"] == "PUBLISHED"


def test_batch_cli_and_retained_downloads(batch_source):
    batches, plans, roots, root, _ = batch_source
    ident = plans[0].legacy.identity
    environment = dict(os.environ) | {
        "HC_EPISODE_MIGRATION_DSN": os.environ["HC_G1_TEST_POSTGRES_DSN"],
        "HC_EPISODE_MIGRATION_OBJECT_ENDPOINT": "http://minio:9000",
        "HC_EPISODE_MIGRATION_OBJECT_ACCESS_KEY": "minio",
        "HC_EPISODE_MIGRATION_OBJECT_SECRET_KEY": "minio-local-only",
        "HC_EPISODE_MIGRATION_OBJECT_BUCKET": "hc-data-local",
        "HC_EPISODE_MIGRATION_LANCE_ROOT": str(root / "legacy"),
    }
    common = [
        "--organization",
        ident.organization_id,
        "--project",
        ident.project_id,
        "--region",
        ident.region_code,
        "--dataset",
        ident.dataset_id,
    ]
    spools = [
        item for p in plans for item in ("--plan-dir", str(root / "spool" / ("plan-" + p.sha256)))
    ]
    outputs = []

    def run(command, name, extra):
        target = root / (name + ".json")
        result = subprocess.run(
            [
                sys.executable,
                "/app/tools/episode_migration_batch.py",
                command,
                *common,
                "--output",
                str(target),
                *extra,
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        outputs.append(json.loads(result.stdout))
        assert target.stat().st_mode & 0o777 == 0o600
        return json.loads(target.read_bytes())

    prepared = run("prepare", "cli-prepared", ["--batch-key", "actual-cli", *spools])
    selected = ["--batch-id", prepared["batch_id"]]
    assert prepared["status"] == "PREPARED"
    assert run("publish", "cli-published", [*selected, *spools])["status"] == "PUBLISHED"
    assert run("status", "cli-status", selected)["status"] == "PUBLISHED"
    assert run("rollback", "cli-rollback", selected)["status"] == "ROLLED_BACK"
    assert run("publish", "cli-retry-after-rollback", selected)["status"] == "ROLLED_BACK"
    downloads = []
    with scoped(plans[0].source):
        assert_baseline(batches, plans)
        for p in plans:
            receipt = batches.packages.get(p.legacy.identity, p.source.data_version_id)
            prefix = receipt.data.package_prefix
            manifest = json.loads(
                b"".join(batches.storage.read_chunks(prefix + "/hc/manifest.json"))
            )
            target = root / "downloaded" / p.legacy.identity.episode_id
            for relative in [
                *(e["path"] for e in manifest["files"]),
                "hc/manifest.json",
                "hc/commit.json",
            ]:
                path = target / "episode" / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"".join(batches.storage.read_chunks(prefix + "/" + relative)))
            report = json.loads((target / "episode/hc/migration.json").read_bytes())
            (target / "result.json").write_bytes(
                canonical(
                    {
                        "plan": p.model_dump(mode="json"),
                        "receipt": receipt.model_dump(mode="json"),
                        "report": report,
                    }
                )
            )
            downloads.append(str(target))
    summary = {
        "scope": "G1 isolated CLI over synthetic two-Episode Lance/RGB-D fixture",
        "commands": outputs,
        "downloaded_after_rollback": downloads,
        "real_recording_acceptance": False,
        "browser_flow": False,
    }
    (root / "cli-results.json").write_bytes(canonical(summary))
    Path("/app/.g1-artifacts/migration-batch-cli-latest.txt").write_text(str(root))


def test_batch_checkpoint_guards_and_source_revision_change(batch_source):
    import psycopg

    batches, plans, roots, _, _ = batch_source
    identity = plans[0].legacy.identity
    scope = (*identity.scope, identity.dataset_id)
    with scoped(plans[0].source):
        prepared = batches.prepare(identity.dataset_id, "guard", plans, roots)
        with pytest.raises(PackageConflict, match="different frozen plan"):
            batches.prepare(
                identity.dataset_id, "guard", plans[:1], {plans[0].sha256: roots[plans[0].sha256]}
            )
        with pytest.raises(PackageConflict, match="only a published"):
            batches.rollback(identity.dataset_id, prepared["batch_id"])
        for mutation in ("plan='{}'::jsonb", "status='PUBLISHED',publication='{}'::jsonb"):
            with pytest.raises(psycopg.Error), batches.packages.transaction() as cursor:
                cursor.execute(
                    "UPDATE episode_store.migration_batches SET "
                    + mutation
                    + " WHERE organization_id=%s AND project_id=%s AND region_code=%s"
                    " AND dataset_id=%s AND batch_id=%s",
                    (*scope, prepared["batch_id"]),
                )
        with pytest.raises(psycopg.Error), batches.packages.transaction() as cursor:
            cursor.execute(
                """DELETE FROM episode_store.migration_batches
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                  AND dataset_id=%s AND batch_id=%s""",
                (*scope, prepared["batch_id"]),
            )
        with batches.packages.transaction() as cursor:
            cursor.execute(
                """UPDATE dataset_registry.dataset_version_episode_revisions
                SET revision_document=jsonb_set(revision_document,'{duration_ns}','\"1\"')
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                  AND dataset_id=%s AND episode_id=%s AND version_id='version_lance_2'""",
                identity.key,
            )
        with pytest.raises(PackageConflict, match="revision differs from the inventory"):
            batches.publish(identity.dataset_id, prepared["batch_id"], roots)
        assert_baseline(batches, plans)
