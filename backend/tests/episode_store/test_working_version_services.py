"""Actual storage heads drive product defaults; this is not a batch rollback tool."""

from datetime import timedelta
from uuid import uuid4

import pytest

from hc_data_platform.dataset_registry.models import DatasetPageScope
from hc_data_platform.dataset_registry.projection_writer import DatasetProjectionWriter
from hc_data_platform.dataset_registry.repository import (
    DatasetPageVersionFilters,
    InMemoryDatasetPageRepository,
    PostgresDatasetPageRepository,
)
from hc_data_platform.dataset_registry.service import DatasetPageService
from hc_data_platform.episode_store.collections import EpisodeCollections
from hc_data_platform.episode_store.commit import EpisodePackageCommitter
from tests.dataset_registry.test_dataset_detail_api import (
    DATASET_ID,
    NOW,
    ORGANIZATION_ID,
    PROJECT_ID,
    REGION_CODE,
    _auth,
    _ready_version,
    _record,
    _scope,
)
from tests.episode_store.test_collections_services import numeric, schema
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_migration_conversion_services import historical  # noqa: F401
from tests.episode_store.test_writer import identity


def bootstrap(versions):
    repo = InMemoryDatasetPageRepository(records=(_record(),), versions=versions)
    service = DatasetPageService(
        repo, cursor_secret="working-version-test-secret", clock=lambda: NOW
    )
    result = service.bootstrap(
        auth=_auth(),
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        region_code=REGION_CODE,
        dataset_id=DATASET_ID,
        request_id="working",
    )
    return repo, result.data


def test_episode_collection_is_working_default_without_fabricating_business_release():
    current = _ready_version().model_copy(update={"version_id": "version_cv_" + "a" * 64})
    repo, result = bootstrap((current,))
    assert result.working_version_id == result.suggested_version_id == current.version_id
    assert result.current_ready_version is None
    assert (
        repo.list_versions(
            scope=_scope(), dataset_id=DATASET_ID, filters=DatasetPageVersionFilters()
        )
        == ()
    )
    assert repo.list_versions(
        scope=_scope(),
        dataset_id=DATASET_ID,
        filters=DatasetPageVersionFilters(include_internal=True),
    ) == (current,)


def test_explicit_business_release_remains_separate_from_newer_working_collection():
    released = _ready_version()
    current = released.model_copy(
        update={
            "version_id": "version_cv_" + "b" * 64,
            "created_at": released.created_at + timedelta(days=1),
        }
    )
    _, result = bootstrap((released, current))
    assert result.working_version_id == current.version_id
    assert (
        result.suggested_version_id
        == result.current_ready_version.version_id
        == released.version_id
    )


@pytest.mark.integration
def test_active_collection_ancestry_controls_default_across_projection_delay_and_rollback(
    services, tmp_path
):
    packages, storage = services
    ident = identity().model_copy(
        update={
            "organization_id": "working-" + uuid4().hex,
            "project_id": "working-project-" + uuid4().hex,
            "dataset_id": "dataset_working_" + uuid4().hex,
            "episode_id": "episode_working",
        }
    )
    scope = DatasetPageScope(
        organization_id=ident.organization_id,
        project_id=ident.project_id,
        region_code=ident.region_code,
    )
    repo = PostgresDatasetPageRepository(packages.connections)
    collections = EpisodeCollections(packages, storage)
    source, _ = numeric(tmp_path / "one", ident)
    with scoped(source):
        with packages.transaction() as cursor:
            cursor.execute(
                """INSERT INTO registry.organization_projects
                (organization_id,project_id,display_name)
                VALUES (%s,%s,'Working version fixture')""",
                (ident.organization_id, ident.project_id),
            )
            DatasetProjectionWriter._put_dataset(
                cursor,
                _record().model_copy(
                    update={
                        "scope": scope,
                        "dataset_id": ident.dataset_id,
                    }
                ),
            )
        first_receipt = EpisodePackageCommitter(packages, storage).commit(tmp_path / "one", source)
        first = collections.publish(first_receipt.data, schema(ident))
        assert repo.working_version_id(scope=scope, dataset_id=ident.dataset_id) is None

        def project(version):
            with packages.transaction() as cursor:
                DatasetProjectionWriter._put_version(
                    cursor,
                    _ready_version().model_copy(
                        update={
                            "scope": scope,
                            "dataset_id": ident.dataset_id,
                            "version_id": version.version_id,
                            "created_at": version.created_at,
                            "published_at": version.created_at,
                        }
                    ),
                )

        project(first)
        assert repo.working_version_id(scope=scope, dataset_id=ident.dataset_id) == first.version_id
        second_source, _ = numeric(tmp_path / "two", ident, configuration="2" * 64)
        second_receipt = EpisodePackageCommitter(packages, storage).commit(
            tmp_path / "two", second_source
        )
        second = collections.publish(second_receipt.data, schema(ident))
        assert repo.working_version_id(scope=scope, dataset_id=ident.dataset_id) == first.version_id
        project(second)
        assert (
            repo.working_version_id(scope=scope, dataset_id=ident.dataset_id) == second.version_id
        )
        # Own isolated fixture only. Formal batch rollback is a separate W7 task.
        with packages.transaction() as cursor:
            cursor.execute(
                """UPDATE episode_store.collection_heads SET collection_version_id=%s
                WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s""",
                (first.collection_version_id, *ident.scope, ident.dataset_id),
            )
        assert repo.working_version_id(scope=scope, dataset_id=ident.dataset_id) == first.version_id
        third_source, _ = numeric(tmp_path / "three", ident, configuration="3" * 64)
        third_receipt = EpisodePackageCommitter(packages, storage).commit(
            tmp_path / "three", third_source
        )
        third = collections.publish(third_receipt.data, schema(ident))
        assert (
            third.version == 3
            and third.previous_collection_version_id == first.collection_version_id
        )
        # Archived v2 is newer but not an ancestor of the active unprojected v3.
        assert repo.working_version_id(scope=scope, dataset_id=ident.dataset_id) == first.version_id
        project(third)
        assert repo.working_version_id(scope=scope, dataset_id=ident.dataset_id) == third.version_id
        assert collections.get(ident.dataset_id, version=2) == second
        assert packages.get(ident, second_receipt.data.data_version_id) == second_receipt
        for name in ("organization_id", "project_id", "region_code"):
            foreign = scope.model_copy(update={name: "foreign"})
            assert repo.working_version_id(scope=foreign, dataset_id=ident.dataset_id) is None


@pytest.mark.integration
def test_legacy_working_pointer_remains_available(historical):  # noqa: F811
    plan, _, _, _, _, packages, _ = historical
    ident = plan.legacy.identity
    scope = DatasetPageScope(
        organization_id=ident.organization_id,
        project_id=ident.project_id,
        region_code=ident.region_code,
    )
    with scoped(plan.source):
        repo = PostgresDatasetPageRepository(packages.connections)
        assert (
            repo.working_version_id(scope=scope, dataset_id=ident.dataset_id) == "version_lance_1"
        )
