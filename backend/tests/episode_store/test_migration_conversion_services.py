"""Real legacy Lance → Parquet/MinIO conversion; inputs remain synthetic fixtures."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from hc_data_platform.episode_store.catalog import PostgresLegacyEntries
from hc_data_platform.episode_store.collections import EpisodeCollections
from hc_data_platform.episode_store.commit import EpisodePackageCommitter
from hc_data_platform.episode_store.files import PackageFiles, VerifiedFileCache
from hc_data_platform.episode_store.migration_conversion import (
    LegacyEpisodeConverter,
    freeze_plan,
    legacy_steps,
    verify_conversion,
)
from hc_data_platform.episode_store.migration_inventory import capture_inventory, save_inventory
from hc_data_platform.episode_store.models import PackageInput, canonical
from hc_data_platform.episode_store.postgres import PackageConflict
from hc_data_platform.episode_store.prepared_assets import PreparedAssets
from hc_data_platform.episode_store.reader import ParquetStepReader, PhysicalDepthReader
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
from hc_data_platform.publishing.depth_evidence import DepthEvidenceVideo, file_hash
from hc_data_platform.publishing.lerobot_export import VIDEO_PATH
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_writer import build_rgbd_package, identity

pytestmark = pytest.mark.integration


@pytest.fixture
def historical(services, tmp_path, request, monkeypatch):
    repository, storage = services
    ident = identity().model_copy(
        update={
            "organization_id": "migration-" + uuid4().hex,
            "project_id": "migration-project-" + uuid4().hex,
            "dataset_id": "dataset_migration_" + uuid4().hex,
            "episode_id": "episode_migration",
        }
    )
    rgbd = getattr(request, "param", "numeric") == "rgbd"
    if rgbd:
        _, source, _, assets = build_rgbd_package(tmp_path, ident)
        rgb, depth = "observation.images.head_rgb", "observation.images.head_depth"
        rows = tuple(
            StepRecord(
                rollout_id=ident.rollout_id,
                step_index=i,
                timestamp_ns=i * 33_333_333,
                modalities={
                    "/observation/state": [0.1 + i * 1e-12],
                    "action": [float(i)],
                    rgb: {"frame_index": i},
                    depth: {"frame_index": i},
                },
                valid={name: True for name in ("/observation/state", "action", rgb, depth)},
                source_timestamps_ns={depth: (i * 33_333_333,)},
                time_error_ns={depth: 0},
            )
            for i in range(4)
        )
        fields = {
            "/observation/state": "list<float64>",
            "action": "list<float64>",
            rgb: "json",
            depth: "json",
        }
    else:
        source = PackageInput(
            identity=ident,
            source_sha256="a" * 64,
            processor_version="fixture/1",
            configuration_sha256="b" * 64,
            schema_snapshot_id="schema-1",
            required_features=("observation.state",),
        )
        assets = PreparedAssets(ident, "fixture", {})
        rows = tuple(
            StepRecord(
                rollout_id=ident.rollout_id,
                step_index=i,
                timestamp_ns=1_790_000_000_000_000_001 + i * 33_333_333,
                modalities={
                    "/observation/state": [0.1 + i * 1e-12],
                    "counter": 9007199254740993 + i,
                    "action": {"values": [float(i)], "names": ["joint"], "units": ["rad"]},
                },
                # Unknown source values remain unknown, including precise integer timestamps.
                source_timestamps_ns={
                    "/observation/state": (1_790_000_000_000_000_001 + i * 33_333_333,)
                },
                valid={"/observation/state": True},
                sample_valid=i != 12,
            )
            for i in range(2053)
        )
        fields = {"/observation/state": "list<float64>", "counter": "int64", "action": "json"}
    rows = tuple(
        row.model_copy(
            update={
                field: {name: getattr(row, field).get(name, default) for name in row.modalities}
                for field, default in (
                    ("source_timestamps_ns", ()),
                    ("time_error_ns", None),
                    ("valid", False),
                    ("repeated", False),
                )
            }
        )
        for row in rows
    )
    schema = DatasetSchemaSnapshot.create(
        project_id=ident.project_id,
        dataset_id=ident.dataset_id,
        schema_snapshot_id="schema-1",
        frequency_hz=30,
        fields=fields,
    )
    adapter = LanceAdapter(tmp_path / "historic-lance")
    legacy = LanceCatalogService(
        adapter,
        PostgresCatalogAdapter(repository.connections),
        PostgresAdvisoryDatasetLock(repository.connections),
    )
    with scoped(source):
        with repository.transaction() as cursor:
            cursor.execute(
                """INSERT INTO registry.organization_projects
                (organization_id,project_id,display_name) VALUES (%s,%s,'Migration fixture')""",
                (ident.organization_id, ident.project_id),
            )
        legacy.register_schema(schema)
        version, _ = legacy.commit_fragment(
            AlignedFragmentManifestV1(
                project_id=ident.project_id,
                dataset_id=ident.dataset_id,
                schema_snapshot_id=schema.schema_snapshot_id,
                schema_fingerprint=schema.fingerprint,
                frequency_hz=30,
                rollout_id=ident.rollout_id,
                source_sha256=source.source_sha256,
                converter_version="legacy-migration-fixture/1",
                attempt_id="historical",
                fragment_uri="file:///migration-fixture",
                step_count=len(rows),
                content_hash=compute_fragment_hash(rows),
            ),
            rows,
        )
        with repository.transaction() as cursor:
            cursor.execute(
                """INSERT INTO dataset_registry.datasets
                (organization_id,project_id,region_code,dataset_id,name,description,availability,
                 owner_id,owner_display_name,asset_state,storage_class,version,dataset_document,
                 created_at,updated_at,activity_at)
                VALUES (%s,%s,%s,%s,'Migration fixture','','ACTIVE','fixture','fixture',
                 'READY','HOT',1,%s::jsonb,now(),now(),now())""",
                (
                    *ident.scope,
                    ident.dataset_id,
                    canonical({"dataset_id": ident.dataset_id}).decode(),
                ),
            )
            cursor.execute(
                """INSERT INTO dataset_registry.dataset_versions
                (organization_id,project_id,region_code,dataset_id,version_id,display_version,
                 version_kind,version_status,created_at,published_at,version_document)
                VALUES (%s,%s,%s,%s,'version_lance_1','v1','RAW','READY',now(),now(),%s::jsonb)""",
                (
                    *ident.scope,
                    ident.dataset_id,
                    canonical(
                        {
                            "dataset_id": ident.dataset_id,
                            "version_id": "version_lance_1",
                            "kind": "RAW",
                            "status": "READY",
                        }
                    ).decode(),
                ),
            )
            cursor.execute(
                """INSERT INTO dataset_registry.dataset_version_episodes
                (organization_id,project_id,region_code,dataset_id,version_id,episode_id,revision_id,
                 ordinal,started_at_ns,included,success_state,episode_document)
                VALUES (%s,%s,%s,%s,'version_lance_1',%s,'revision_old',0,0,true,
                        'SUCCEEDED',%s::jsonb)""",
                (
                    *ident.scope,
                    ident.dataset_id,
                    ident.episode_id,
                    canonical({"episode_id": ident.episode_id}).decode(),
                ),
            )
            cursor.execute(
                """INSERT INTO dataset_registry.dataset_version_episode_revisions
                (organization_id,project_id,region_code,dataset_id,version_id,episode_id,revision_id,
                 ordinal,revision_document)
                VALUES (%s,%s,%s,%s,'version_lance_1',%s,'revision_old',0,%s::jsonb)""",
                (
                    *ident.scope,
                    ident.dataset_id,
                    ident.episode_id,
                    canonical(
                        {"streams": [{"data_binding": {"rollout_id": ident.rollout_id}}]}
                    ).decode(),
                ),
            )
        reference = PostgresLegacyEntries(repository, legacy)(version)[0]
        inventory = capture_inventory(repository.connections, ident.dataset_id)
        plan = freeze_plan(
            inventory,
            reference,
            legacy,
            schema,
            assets,
            metadata={"task": "migration software fixture"},
        )

    def forbidden(*args, **kwargs):
        raise AssertionError("migration must not write Lance or re-encode prepared depth")

    monkeypatch.setattr(adapter, "stage_attempt", forbidden)
    monkeypatch.setattr(adapter, "commit_staged", forbidden)
    monkeypatch.setattr(DepthEvidenceVideo, "write", forbidden)
    return plan, inventory, assets, legacy, rows, repository, storage


def test_numeric_conversion_preserves_all_rows_and_does_not_activate(historical, tmp_path):
    plan, inventory, assets, legacy, rows, repository, storage = historical
    with scoped(plan.source):
        converter = LegacyEpisodeConverter(
            legacy, EpisodePackageCommitter(repository, storage), tmp_path / "spool"
        )
        receipt, report, root = converter.stage(plan, assets)
        assert (
            report["legacy_logical_sha256"]
            == report["converted_logical_sha256"]
            == compute_fragment_hash(rows)
        )
        assert receipt.data.frame_count == 2053 and receipt.data.version == 1
        assert repository.current(plan.legacy.identity) is None
        assert (
            EpisodeCollections(repository, storage).current(plan.legacy.identity.dataset_id) is None
        )
        assert tuple(legacy_steps(legacy, plan.legacy)) == rows
        assert converter.stage(plan, assets) == (receipt, report, root)
        assert len(list((tmp_path / "spool").glob("*/attempt-*/package"))) == 1
        after = capture_inventory(repository.connections, plan.legacy.identity.dataset_id)
        for name, original in inventory["inventory"]["tables"].items():
            if not name.startswith("episode_store."):
                assert after["inventory"]["tables"][name] == original
        # Alter a numeric value while retaining an otherwise valid standard package.
        import pyarrow.parquet as pq

        from hc_data_platform.publishing.lerobot_export import DATA_PATH

        path = root / DATA_PATH
        table = pq.read_table(path)
        values = table["hc.source.counter"].to_pylist()
        values[1025] += 1
        import pyarrow as pa

        pq.write_table(
            table.set_column(
                table.schema.get_field_index("hc.source.counter"),
                "hc.source.counter",
                pa.array(values),
            ),
            path,
        )
        with pytest.raises(PackageConflict, match="equivalence"):
            verify_conversion(root, plan, legacy)


@pytest.mark.parametrize("historical", ["rgbd"], indirect=True)
def test_rgbd_conversion_reuses_media_masks_and_actual_profile(historical, tmp_path):
    plan, inventory, assets, legacy, _, repository, storage = historical
    cache = VerifiedFileCache(tmp_path / "cache")
    try:
        with scoped(plan.source):
            receipt, report, root = LegacyEpisodeConverter(
                legacy, EpisodePackageCommitter(repository, storage), tmp_path / "spool"
            ).stage(plan, assets)
            for feature, video in assets.videos.items():
                assert (
                    file_hash(
                        root / VIDEO_PATH.format(video_key=feature, chunk_index=0, file_index=0)
                    )
                    == video.sha256
                )
                assert report["media"][feature]["requantized"] is False
            depth = plan.required_depth_features[0]
            assert report["media"][depth]["video_encoding"]["codec_profile"] == "Rext"
            files = PackageFiles(repository, storage, cache)
            pixel = (
                PhysicalDepthReader(files)
                .read(plan.legacy.identity, receipt.data.data_version_id, depth, 2, unit="m")
                .pixel(4, 4)
            )
            assert pixel.valid and pixel.value == pytest.approx(1.5, abs=0.003)
            assert ParquetStepReader(files).read_steps(
                plan.legacy.identity, receipt.data.data_version_id, 0, 4
            ).steps[2].modalities[depth]["source"] == {"frame_index": 2}
            assert not report["review_automatically_transferred"]
            evidence = (
                Path(os.getenv("HC_G1_WRITER_EVIDENCE_ROOT", tmp_path))
                / "migration-conversion"
                / plan.sha256
            )
            evidence.mkdir(parents=True)
            # Preserve a complete independent reader input and the frozen scope/hash evidence.
            import shutil

            offline = evidence / "episode"
            package = files.open(plan.legacy.identity, receipt.data.data_version_id)
            for entry in package.manifest.files:
                target = offline / entry.path
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.file(entry.path) as origin:
                    shutil.copyfile(origin, target)
            (offline / "hc/manifest.json").write_bytes(
                canonical(package.manifest.model_dump(mode="json"))
            )
            (offline / "hc/commit.json").write_bytes(canonical(receipt.model_dump(mode="json")))
            save_inventory(evidence / "inventory.json", inventory)
            (evidence / "result.json").write_bytes(
                canonical(
                    {
                        "plan": plan.model_dump(mode="json"),
                        "receipt": receipt.model_dump(mode="json"),
                        "report": report,
                        "sample_kind": "synthetic_legacy_rgbd_service_fixture",
                    }
                )
            )
    finally:
        cache.close()


@pytest.mark.parametrize(
    "fault",
    [
        "migration_source_window_read",
        "migration_converted",
        "reserved",
        "objects_verified",
        "database_committed",
        "migration_staged",
    ],
)
def test_interruption_resumes_without_duplicates_or_pointer_changes(historical, tmp_path, fault):
    plan, _, assets, legacy, _, repository, storage = historical

    def interrupt(stage):
        if stage == fault:
            raise InterruptedError("migration fault injection")

    spool = tmp_path / "spool"
    with scoped(plan.source):
        failing = LegacyEpisodeConverter(
            legacy,
            EpisodePackageCommitter(repository, storage, checkpoint=interrupt),
            spool,
            checkpoint=interrupt,
        )
        with pytest.raises(InterruptedError):
            failing.stage(plan, assets)
        assert repository.current(plan.legacy.identity) is None
        resumed = LegacyEpisodeConverter(
            legacy, EpisodePackageCommitter(repository, storage), spool
        )
        receipt, report, _ = resumed.stage(plan, assets)
        assert (
            receipt.data.version == 1
            and report["numeric_time_source_comparison"] == "all_rows_exact"
        )
        assert repository.list_versions(plan.legacy.identity) == (receipt.data,)
        assert repository.current(plan.legacy.identity) is None
        assert resumed.stage(plan, assets)[0] == receipt


def test_concurrent_conversion_shares_one_immutable_checkpoint(historical, tmp_path):
    plan, _, assets, legacy, _, repository, storage = historical

    def run(_):
        with scoped(plan.source):
            return LegacyEpisodeConverter(
                legacy, EpisodePackageCommitter(repository, storage), tmp_path / "spool"
            ).stage(plan, assets)

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(run, range(3)))
    assert results[0] == results[1] == results[2]
    assert len(list((tmp_path / "spool").glob("*/attempt-*/package"))) == 1


@pytest.mark.parametrize("field", ["organization_id", "project_id", "region_code"])
def test_migration_rejects_cross_scope_before_writes(historical, tmp_path, field):
    plan, _, assets, legacy, _, repository, storage = historical
    foreign = plan.source.model_copy(
        update={"identity": plan.legacy.identity.model_copy(update={field: "foreign"})}
    )
    with scoped(foreign), pytest.raises(PermissionError):
        LegacyEpisodeConverter(
            legacy, EpisodePackageCommitter(repository, storage), tmp_path / "spool"
        ).stage(plan, assets)
    assert not (tmp_path / "spool").exists()


def test_frozen_inventory_reference_and_checkpoint_tampering_rejected(historical, tmp_path):
    plan, inventory, assets, legacy, _, repository, storage = historical
    with scoped(plan.source):
        with pytest.raises(PackageConflict, match="hash/scope"):
            freeze_plan(
                {**inventory, "inventory_sha256": "0" * 64},
                plan.legacy,
                legacy,
                plan.schema_snapshot,
                assets,
                metadata={},
            )
        with pytest.raises(ValueError, match="quality/review"):
            freeze_plan(
                inventory,
                plan.legacy,
                legacy,
                plan.schema_snapshot,
                assets,
                metadata={"quality": {"status": "PASS"}},
            )
        converter = LegacyEpisodeConverter(
            legacy, EpisodePackageCommitter(repository, storage), tmp_path / "spool"
        )
        receipt, _, _ = converter.stage(plan, assets)
        checkpoint = tmp_path / "spool" / ("plan-" + plan.sha256) / "converted.json"
        checkpoint.write_text(json.dumps({"package": "../../outside", "manifest_sha256": "0" * 64}))
        with pytest.raises(ValueError, match="canonical"):
            converter.stage(plan, assets)
        assert repository.current(plan.legacy.identity) is None
        assert repository.get(plan.legacy.identity, receipt.data.data_version_id) == receipt


@pytest.mark.parametrize("historical", ["rgbd"], indirect=True)
def test_missing_camera_or_changed_media_cannot_be_silently_excluded(historical, tmp_path):
    plan, inventory, assets, legacy, _, repository, storage = historical
    incomplete = PreparedAssets(assets.identity, "missing", {})
    with scoped(plan.source):
        with pytest.raises(PackageConflict, match="every legacy camera"):
            freeze_plan(
                inventory, plan.legacy, legacy, plan.schema_snapshot, incomplete, metadata={}
            )
        converter = LegacyEpisodeConverter(
            legacy, EpisodePackageCommitter(repository, storage), tmp_path / "spool"
        )
        first = next(iter(assets.videos.values()))
        with first.path.open("ab") as stream:
            stream.write(b"tamper")
        with pytest.raises(ValueError, match="frozen attempt"):
            converter.stage(plan, assets)
        assert repository.current(plan.legacy.identity) is None
    assert not (tmp_path / "spool").exists()


def test_forged_plan_and_metadata_changes_never_reuse_old_identity(historical, tmp_path):
    plan, inventory, assets, legacy, _, repository, storage = historical
    converter = LegacyEpisodeConverter(
        legacy, EpisodePackageCommitter(repository, storage), tmp_path / "spool"
    )
    with scoped(plan.source):
        for change, error in (
            ({"source_sha256": "f" * 64}, "schema/source"),
            ({"required_features": ("not-the-source",)}, "required features"),
        ):
            with pytest.raises(PackageConflict, match=error):
                converter.stage(plan.model_copy(update=change), assets)
        renamed = freeze_plan(
            inventory,
            plan.legacy,
            legacy,
            plan.schema_snapshot,
            assets,
            metadata={"task": "different frozen task"},
        )
        assert renamed.source.data_version_id != plan.source.data_version_id
        _, _, root = converter.stage(plan, assets)
        (root / "hc/calibration.json").write_bytes(canonical({"status": "invented"}))
        with pytest.raises(PackageConflict, match="calibration"):
            converter.stage(plan, assets)
        assert repository.current(plan.legacy.identity) is None
