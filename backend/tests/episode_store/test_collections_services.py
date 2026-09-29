"""Collection recovery/concurrency and real legacy Lance + new Parquet routing."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from hc_data_platform.episode_store.catalog import EpisodeCatalogReader, PostgresLegacyEntries
from hc_data_platform.episode_store.collections import EpisodeCollections
from hc_data_platform.episode_store.commit import EpisodePackageCommitter
from hc_data_platform.episode_store.files import PackageFiles, VerifiedFileCache
from hc_data_platform.episode_store.models import PackageInput, canonical
from hc_data_platform.episode_store.postgres import PackageConflict
from hc_data_platform.episode_store.prepared_assets import PreparedAssets
from hc_data_platform.episode_store.reader import ParquetStepReader
from hc_data_platform.episode_store.service import EpisodePackageService
from hc_data_platform.episode_store.writer import write_episode
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
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_writer import identity


def numeric(root, ident, *, configuration="4" * 64):
    source = PackageInput(
        identity=ident,
        source_sha256="3" * 64,
        processor_version="collection-test/1",
        configuration_sha256=configuration,
        schema_snapshot_id="schema-1",
        required_features=("observation.state",),
    )
    rows = tuple(
        StepRecord(
            rollout_id=ident.rollout_id,
            step_index=i,
            timestamp_ns=i * 33_333_333,
            modalities={"/observation/state": [0.1 + i * 1e-12]},
            source_timestamps_ns={"/observation/state": (i * 33_333_333,)},
            time_error_ns={"/observation/state": 0},
            valid={"/observation/state": True},
            repeated={"/observation/state": False},
        )
        for i in range(3)
    )
    with scoped(source):
        write_episode(
            root,
            source,
            rows,
            PreparedAssets(ident, "fixture", {}),
            fps=30,
            metadata={"task": "collection integration fixture"},
        )
    return source, rows


def schema(ident):
    return DatasetSchemaSnapshot.create(
        project_id=ident.project_id,
        dataset_id=ident.dataset_id,
        schema_snapshot_id="schema-1",
        frequency_hz=30,
        fields={"/observation/state": "list<float64>"},
    )


@pytest.mark.integration
def test_processing_handoff_writes_package_and_collection_without_lance(
    services, tmp_path, monkeypatch
):
    repository, storage = services
    ident = identity().model_copy(update={"organization_id": "handoff-" + uuid4().hex})
    source, rows = numeric(tmp_path / "input", ident)

    def no_lance(*args, **kwargs):
        raise AssertionError("new processing invoked a Lance write")

    monkeypatch.setattr(LanceAdapter, "stage_attempt", no_lance)
    monkeypatch.setattr(LanceAdapter, "commit_staged", no_lance)
    service = EpisodePackageService(repository.connections, storage, tmp_path / "service")
    try:
        with scoped(source):
            first = service.write_and_publish(
                source,
                iter(rows),
                PreparedAssets(ident, "attempt-a", {}),
                schema=schema(ident),
                metadata={"task": "handoff"},
            )
            retried = service.write_and_publish(
                source,
                iter(rows),
                PreparedAssets(ident, "attempt-b", {}),
                schema=schema(ident),
                metadata={"task": "handoff"},
            )
            assert first == retried
            collection, ready = first
            assert ready.data == collection.episodes[0]
            assert "lance_version" not in ready.model_dump_json()
            assert service.steps.read_steps(ident, ready.data.data_version_id, 0, 3).steps == rows
    finally:
        service.close()


@pytest.mark.integration
@pytest.mark.parametrize("fault", ["collection_object_verified", "collection_database_committed"])
def test_collection_interruption_and_retry_preserve_original_publication(services, tmp_path, fault):
    repository, storage = services
    ident = identity().model_copy(update={"organization_id": "collection-" + uuid4().hex})
    root = tmp_path / "episode"
    source, _ = numeric(root, ident)

    def interrupt(stage):
        if stage == fault:
            raise InterruptedError("synthetic collection checkpoint fault")

    with scoped(source):
        receipt = EpisodePackageCommitter(repository, storage).commit(root, source)
        failing = EpisodeCollections(repository, storage, checkpoint=interrupt)
        with pytest.raises(InterruptedError):
            failing.publish(receipt.data, schema(ident))
        assert (failing.current(ident.dataset_id) is not None) == (
            fault == "collection_database_committed"
        )
        collections = EpisodeCollections(repository, storage)
        published = collections.publish(receipt.data, schema(ident))
        assert published.version == 1 and published.episodes == (receipt.data,)
        assert collections.publish(receipt.data, schema(ident)) == published
        assert "lance_version" not in published.model_dump_json()
        forged = receipt.data.model_copy(update={"content_hash": "e" * 64})
        with pytest.raises(PackageConflict, match="differs"):
            collections.publish(forged, schema(ident))


@pytest.mark.integration
def test_concurrent_episode_publications_and_replacement_do_not_overwrite_history(
    services, tmp_path
):
    repository, storage = services
    organization = "collection-" + uuid4().hex
    prepared = []
    for i in range(4):
        ident = identity().model_copy(
            update={
                "organization_id": organization,
                "episode_id": f"episode-{i}",
                "rollout_id": f"rollout-{i}",
            }
        )
        root = tmp_path / f"episode-{i}"
        source, _ = numeric(root, ident)
        with scoped(source):
            receipt = EpisodePackageCommitter(repository, storage).commit(root, source)
        prepared.append((source, receipt))
    collections = EpisodeCollections(repository, storage)

    def publish(item):
        source, receipt = item
        with scoped(source):
            return collections.publish(receipt.data, schema(source.identity))

    with ThreadPoolExecutor(max_workers=4) as pool:
        published = list(pool.map(publish, prepared))
    assert sorted(v.version for v in published) == [1, 2, 3, 4]
    source, receipt = prepared[0]
    ident = source.identity
    with scoped(source):
        assert len(collections.current(ident.dataset_id).episodes) == 4
        # Retry an old publication returns its own immutable receipt, never the latest head.
        assert collections.publish(receipt.data, schema(ident)) == published[0]
        root = tmp_path / "new-profile"
        changed, _ = numeric(root, ident, configuration="5" * 64)
        new_receipt = EpisodePackageCommitter(repository, storage).commit(root, changed)
        latest = collections.publish(new_receipt.data, schema(ident))
        assert latest.version == 5 and len(latest.episodes) == 4
        assert next(e for e in latest.episodes if e.identity == ident) == new_receipt.data
        old = collections.get(ident.dataset_id, version=published[0].version)
        assert next(e for e in old.episodes if e.identity == ident) == receipt.data
        with repository.transaction() as cursor:
            cursor.execute(
                "SELECT count(*) FROM episode_store.collection_events WHERE organization_id=%s",
                (organization,),
            )
            assert cursor.fetchone()[0] == 5


@pytest.mark.integration
def test_actual_legacy_lance_and_new_parquet_in_one_collection(services, tmp_path, monkeypatch):
    repository, storage = services
    ident = identity().model_copy(
        update={
            "organization_id": "mixed-" + uuid4().hex,
            "project_id": "mixed-project-" + uuid4().hex,
            "dataset_id": "dataset_mixed_" + uuid4().hex,
        }
    )
    old_identity = ident.model_copy(
        update={"episode_id": "episode_explicit_old", "rollout_id": "old::rollout"}
    )
    new_root = tmp_path / "new"
    source, rows = numeric(new_root, ident)
    adapter = LanceAdapter(tmp_path / "existing-lance")
    legacy = LanceCatalogService(
        adapter,
        PostgresCatalogAdapter(repository.connections),
        PostgresAdvisoryDatasetLock(repository.connections),
    )
    frozen_schema = schema(ident)
    with scoped(source):
        with repository.transaction() as cursor:
            cursor.execute(
                """INSERT INTO registry.organization_projects
                (organization_id,project_id,display_name) VALUES (%s,%s,'G1 historical fixture')""",
                (ident.organization_id, ident.project_id),
            )
        legacy.register_schema(frozen_schema)
        old_rows = tuple(
            row.model_copy(update={"rollout_id": old_identity.rollout_id}) for row in rows
        )
        fragment = AlignedFragmentManifestV1(
            project_id=ident.project_id,
            dataset_id=ident.dataset_id,
            schema_snapshot_id=frozen_schema.schema_snapshot_id,
            schema_fingerprint=frozen_schema.fingerprint,
            frequency_hz=30,
            rollout_id=old_identity.rollout_id,
            source_sha256="a" * 64,
            converter_version="historical-fixture/1",
            attempt_id="historical",
            fragment_uri="file:///historical",
            step_count=len(old_rows),
            content_hash=compute_fragment_hash(old_rows),
        )
        old_version, _ = legacy.commit_fragment(fragment, old_rows)

        # Historical physical writes are limited to preparing this explicit fixture.
        def forbidden(*args, **kwargs):
            raise AssertionError("new package/collection attempted a Lance write")

        monkeypatch.setattr(adapter, "stage_attempt", forbidden)
        monkeypatch.setattr(adapter, "commit_staged", forbidden)
        with repository.transaction() as cursor:
            cursor.execute(
                """INSERT INTO dataset_registry.datasets
                (organization_id,project_id,region_code,dataset_id,name,description,availability,
                 owner_id,owner_display_name,asset_state,storage_class,version,dataset_document,
                 created_at,updated_at,activity_at)
                VALUES (%s,%s,%s,%s,'Historical fixture','','ACTIVE','fixture','fixture',
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
                    old_identity.episode_id,
                    canonical(
                        {
                            "dataset_id": ident.dataset_id,
                            "version_id": "version_lance_1",
                            "episode_id": old_identity.episode_id,
                        }
                    ).decode(),
                ),
            )
            cursor.execute(
                """INSERT INTO dataset_registry.dataset_version_episode_revisions
                (organization_id,project_id,region_code,dataset_id,version_id,episode_id,revision_id,ordinal,revision_document)
                VALUES (%s,%s,%s,%s,%s,%s,'revision_old',0,%s::jsonb)""",
                (
                    *ident.scope,
                    ident.dataset_id,
                    f"version_lance_{old_version.version}",
                    old_identity.episode_id,
                    canonical(
                        {"streams": [{"data_binding": {"rollout_id": old_identity.rollout_id}}]}
                    ).decode(),
                ),
            )
        collections = EpisodeCollections(
            repository,
            storage,
            legacy=legacy,
            legacy_entries=PostgresLegacyEntries(repository, legacy),
        )
        receipt = EpisodePackageCommitter(repository, storage).commit(new_root, source)
        mixed = collections.publish(receipt.data, frozen_schema)
        assert mixed.version == 2
        assert {e.storage_format for e in mixed.episodes} == {"lance", "lerobot_v3"}
        cache = VerifiedFileCache(tmp_path / "cache")
        try:
            files = PackageFiles(repository, storage, cache)
            reader = EpisodeCatalogReader(legacy, collections, files, ParquetStepReader(files))
            for rollout, expected in (
                (old_identity.rollout_id, old_rows),
                (ident.rollout_id, rows),
            ):
                selected = reader.read_steps(
                    ident.dataset_id, rollout, 0, 3, project_id=ident.project_id, version=2
                )
                assert [s.modalities for s in selected.steps] == [s.modalities for s in expected]
                assert selected.dataset_version == 2
                assert selected.data.identity.rollout_id == rollout
            assert (
                reader.version_snapshot(ident.dataset_id, version=1, project_id=ident.project_id)
                == old_version
            )
            assert (
                reader.lineage(
                    ident.dataset_id, ident.rollout_id, version=2, project_id=ident.project_id
                ).data
                == receipt.data
            )
            assert (
                legacy.current_version(ident.dataset_id, project_id=ident.project_id) == old_version
            )
            assert reader.current_version(ident.dataset_id) == mixed
            assert reader.list_versions(ident.dataset_id) == (old_version, mixed)
            other_region = source.model_copy(
                update={"identity": ident.model_copy(update={"region_code": "elsewhere"})}
            )
            with scoped(other_region):
                assert reader.current_version(ident.dataset_id) is None
                assert reader.list_versions(ident.dataset_id) == ()
                for version in (1, 2):
                    with pytest.raises(KeyError):
                        reader.version_snapshot(ident.dataset_id, version=version)
                    with pytest.raises(KeyError):
                        reader.read_steps(
                            ident.dataset_id, old_identity.rollout_id, 0, 1, version=version
                        )
        finally:
            cache.close()
    foreign = source.model_copy(
        update={"identity": ident.model_copy(update={"organization_id": "foreign"})}
    )
    with scoped(foreign):
        assert legacy.schema_for(ident.project_id, ident.dataset_id) is None
        assert legacy.current_version(ident.dataset_id, project_id=ident.project_id) is None
        with pytest.raises(KeyError):
            legacy.lineage(
                ident.dataset_id, old_identity.rollout_id, version=1, project_id=ident.project_id
            )
