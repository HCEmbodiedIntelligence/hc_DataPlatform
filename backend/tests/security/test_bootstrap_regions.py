from datetime import datetime, timezone
from unittest.mock import Mock

from hc_data_platform.security.access_models import (
    AccountPrincipal,
    AvailableScope,
    ResolvedSession,
)
from hc_data_platform.security.access_service import AccessService
from hc_data_platform.security.passwords import PasswordHasher, ScryptParameters


def test_bootstrap_discovers_regions_without_changing_permissions_or_each_request_auth() -> None:
    wide = AvailableScope(
        organization_id="org",
        project_id="wide",
        region_codes=(),
        project_wide=True,
        capabilities=("dataset.read",),
    )
    restricted = AvailableScope(
        organization_id="org",
        project_id="restricted",
        region_codes=("eu-only",),
        project_wide=False,
        capabilities=("upload.read",),
    )
    repository = Mock()
    repository.resolve_session.return_value = ResolvedSession(
        session_id="session",
        principal=AccountPrincipal(
            principal_id="principal",
            username="operator",
            status="ACTIVE",
            display_name="operator",
            created_at=datetime.now(timezone.utc),
        ),
        capability_revision=1,
        scopes=(wide, restricted),
    )
    repository.list_organization_memberships.return_value = ()
    resolver = Mock(return_value=("cn-beijing", "global"))
    service = AccessService(
        repository,
        scope_region_resolver=resolver,
        password_hasher=PasswordHasher(ScryptParameters(n=2**10)),
    )
    result = service.bootstrap("hcs_test")
    assert result.available_scopes[0].region_codes == ("cn-beijing", "global")
    assert result.available_scopes[0].capabilities == ("dataset.read",)
    assert result.available_scopes[1] == restricted
    assert result.platform_capabilities == ()
    auth = service.authenticate_access_token("hcs_test")
    assert auth is not None
    assert auth.organization_scope_triples == frozenset(
        {("org", "wide", None), ("org", "restricted", "eu-only")}
    )
    resolver.assert_called_once_with(wide)


def test_empty_personal_account_does_not_get_a_project_or_region() -> None:
    repository = Mock()
    repository.resolve_session.return_value = ResolvedSession(
        session_id="session",
        principal=AccountPrincipal(
            principal_id="principal",
            username="operator",
            status="ACTIVE",
            display_name="operator",
            created_at=datetime.now(timezone.utc),
        ),
        capability_revision=1,
        scopes=(),
    )
    repository.list_organization_memberships.return_value = ()
    resolver = Mock()
    service = AccessService(
        repository,
        scope_region_resolver=resolver,
        password_hasher=PasswordHasher(ScryptParameters(n=2**10)),
    )
    assert service.bootstrap("hcs_test").available_scopes == ()
    resolver.assert_not_called()
