"""New schema registration is tenant/region scoped and never writes Lance metadata."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest

from hc_data_platform.episode_store.postgres import PackageConflict
from hc_data_platform.episode_store.schemas import EpisodeSchemas
from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot
from tests.episode_store.test_collections_services import schema
from tests.episode_store.test_commit_services import scoped


@pytest.mark.integration
def test_frozen_schema_concurrent_registration_conflict_and_tenant_namespaces(services, package):
    repository, _ = services
    _, source = package
    registry = EpisodeSchemas(repository)
    frozen = schema(source.identity)

    def register(_):
        with scoped(source):
            registry.register_schema(frozen)
            return registry.schema_for(frozen.project_id, frozen.dataset_id)

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(register, range(4))) == [frozen] * 4
    with scoped(source):
        forged = frozen.model_copy(update={"fingerprint": "f" * 64})
        with pytest.raises(PackageConflict, match="fingerprint"):
            registry.register_schema(forged)
        changed = DatasetSchemaSnapshot.create(
            project_id=frozen.project_id,
            dataset_id=frozen.dataset_id,
            schema_snapshot_id="different",
            frequency_hz=60,
            fields={"/observation/state": "list<float32>"},
        )
        with pytest.raises(PackageConflict, match="frozen differently"):
            registry.register_schema(changed)
        with repository.transaction() as cursor:
            cursor.execute(
                "SELECT count(*) FROM lance_schema_snapshots WHERE organization_id=%s",
                (source.identity.organization_id,),
            )
            assert cursor.fetchone()[0] == 0
        with (
            pytest.raises(psycopg.errors.RaiseException, match="immutable"),
            repository.transaction() as cursor,
        ):
            cursor.execute(
                """UPDATE episode_store.dataset_schemas SET schema_snapshot_id='changed'
                WHERE organization_id=%s""",
                (source.identity.organization_id,),
            )
        assert registry.schema_for(frozen.project_id, frozen.dataset_id) == frozen

    # Reused project/dataset names in another organization OR region are independent.
    for change in (
        {"organization_id": "another-" + uuid4().hex},
        {"region_code": "another-region"},
    ):
        foreign = source.model_copy(update={"identity": source.identity.model_copy(update=change)})
        with scoped(foreign):
            assert registry.schema_for(frozen.project_id, frozen.dataset_id) is None
            registry.register_schema(changed)
            assert registry.schema_for(frozen.project_id, frozen.dataset_id) == changed
    with scoped(source):
        assert registry.schema_for(frozen.project_id, frozen.dataset_id) == frozen
        with pytest.raises(PermissionError):
            registry.schema_for("another-project", frozen.dataset_id)
