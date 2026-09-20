import json
import os
from uuid import uuid4

import pytest

from hc_data_platform.security.access_models import AvailableScope
from hc_data_platform.security.access_postgres import PostgresAccessRepository


@pytest.mark.integration
def test_business_regions_are_deduplicated_and_isolated_by_organization_and_project() -> None:
    dsn = os.getenv("HC_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    import psycopg

    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    suffix = uuid4().hex
    organization, other_organization = f"region-org-{suffix}", f"region-other-{suffix}"
    project, other_project = f"region-project-{suffix}", f"region-other-project-{suffix}"
    identities = (
        (organization, project),
        (other_organization, project),
        (organization, other_project),
    )
    repository = PostgresAccessRepository.from_dsn(dsn)
    scope = AvailableScope(
        organization_id=organization,
        project_id=project,
        project_wide=True,
        region_codes=(),
        capabilities=("dataset.read",),
    )
    try:
        with psycopg.connect(dsn) as connection:
            for org, proj in identities:
                connection.execute(
                    "INSERT INTO registry.organization_projects "
                    "(organization_id, project_id, display_name) VALUES (%s,%s,%s)",
                    (org, proj, proj),
                )
            for i, (org, proj, region) in enumerate(
                (
                    (organization, project, "cn-beijing"),
                    (organization, project, "cn-beijing"),
                    (other_organization, project, "other-tenant"),
                    (organization, other_project, "other-project"),
                )
            ):
                source_id = f"source-{suffix}-{i}"
                document = {
                    "id": source_id,
                    "source_type": "ROBOT",
                    "administrative_state": "ENABLED",
                    "binding": {"kind": "ROBOT"},
                    "configuration": {"kind": "ROBOT"},
                }
                connection.execute(
                    """INSERT INTO ingest.data_sources
                    (organization_id,project_id,region_code,source_id,name,source_type,source_format,
                     administrative_state,connectivity_state,credential_state,version,source_document,created_at,updated_at)
                    VALUES (%s,%s,%s,%s,%s,'ROBOT','LEROBOT','ENABLED','UNKNOWN',
                            'NOT_REQUIRED',1,%s::jsonb,now(),now())""",
                    (org, proj, region, source_id, source_id, json.dumps(document)),
                )
        assert repository.available_scope_regions(scope) == ("cn-beijing",)
        assert repository.available_scope_regions(
            scope.model_copy(update={"project_id": f"empty-{suffix}"})
        ) == ("global",)
        assert repository.available_scope_regions(
            scope.model_copy(update={"project_wide": False, "region_codes": ("restricted",)})
        ) == ("restricted",)
    finally:
        with psycopg.connect(dsn) as connection:
            for org, proj in identities:
                connection.execute(
                    "DELETE FROM ingest.data_sources WHERE organization_id=%s AND project_id=%s",
                    (org, proj),
                )
                connection.execute(
                    "DELETE FROM registry.organization_projects "
                    "WHERE organization_id=%s AND project_id=%s",
                    (org, proj),
                )
