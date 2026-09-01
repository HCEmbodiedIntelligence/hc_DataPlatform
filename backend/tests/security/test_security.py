from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Lock

import jwt
import pytest

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.security.audit import AuditRecord, canonical_hash
from hc_data_platform.security.auth import AuthContext, JwtVerifier
from hc_data_platform.security.capabilities import (
    CAPABILITY_PLATFORM_ADMIN,
    CAPABILITY_PLATFORM_BREAK_GLASS,
    CAPABILITY_PLATFORM_MAINTENANCE_OPERATE,
)
from hc_data_platform.security.idempotency import InMemoryIdempotencyStore
from hc_data_platform.security.postgres import RlsSessionContext
from hc_data_platform.security.scope import (
    InMemoryScopedRepository,
    ScopedResource,
    ScopeGuard,
    ScopeSelection,
)
from hc_data_platform.security.uow import InMemoryAtomicDatabase, InMemoryScopedUnitOfWork
from hc_data_platform.security.versioning import ResourceVersion

TEST_KEY = "test-signing-key-that-is-longer-than-32-bytes"
WRONG_TEST_KEY = "wrong-signing-key-that-is-also-32-bytes-long"


def _claims(**overrides: object) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    claims: dict[str, object] = {
        "sub": "user-1",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "iss": "issuer",
        "aud": "backend",
        "capabilities": [],
        "organization_scopes": [
            {
                "organization_id": "org-1",
                "project_id": "p1",
                "region_code": "cn-hz",
                "capabilities": ["upload.manage"],
            }
        ],
    }
    claims.update(overrides)
    return claims


def _token(*, key: str = TEST_KEY, **claims: object) -> str:
    return jwt.encode(_claims(**claims), key, algorithm="HS256")


def _verifier(**overrides: object) -> JwtVerifier:
    arguments: dict[str, object] = {
        "key": TEST_KEY,
        "issuer": "issuer",
        "audience": "backend",
        "algorithms": ["HS256"],
    }
    arguments.update(overrides)
    return JwtVerifier(**arguments)  # type: ignore[arg-type]


def _assert_problem(code: str, callable_: object) -> None:
    assert callable(callable_)
    with pytest.raises(ProblemException) as captured:
        callable_()
    assert captured.value.problem.code == code


def test_jwt_verifier_accepts_all_required_claims() -> None:
    context = _verifier().verify(_token())
    assert context.subject_id == "user-1"
    assert context.project_ids == frozenset({"p1"})
    assert context.region_codes == frozenset({"cn-hz"})
    context.require_capability("upload.manage", "p1", "org-1")


def test_jwt_verifier_accepts_exact_organization_scopes_without_cross_organization_bleed() -> None:
    context = _verifier().verify(
        _token(
            organization_scopes=[
                {
                    "organization_id": "org-a",
                    "project_id": "project-a",
                    "region_code": None,
                    "capabilities": ["access.manage"],
                },
                {
                    "organization_id": "org-b",
                    "project_id": "project-a",
                    "region_code": "cn-shanghai",
                    "capabilities": ["dataset.read"],
                },
            ],
        )
    )

    ScopeGuard.require(context, "project-a", organization_id="org-a")
    ScopeGuard.require(
        context,
        "project-a",
        "cn-shanghai",
        organization_id="org-b",
    )
    context.require_capability("access.manage", "project-a", organization_id="org-a")
    context.require_capability("dataset.read", "project-a", organization_id="org-b")
    _assert_problem(
        "CAPABILITY_REQUIRED",
        lambda: context.require_capability("access.manage", "project-a", organization_id="org-b"),
    )
    _assert_problem(
        "CAPABILITY_REQUIRED",
        lambda: context.require_capability("dataset.read", "project-a", organization_id="org-a"),
    )


@pytest.mark.parametrize(
    "organization_scopes",
    [
        "org-a/project-a",
        [{"organization_id": "org-a", "project_id": "project-a"}],
        [
            {
                "organization_id": "org-a",
                "project_id": "project-a",
                "region_code": None,
                "capabilities": [""],
            }
        ],
    ],
)
def test_jwt_verifier_rejects_malformed_organization_scopes(
    organization_scopes: object,
) -> None:
    _assert_problem(
        "INVALID_ACCESS_TOKEN",
        lambda: _verifier().verify(_token(organization_scopes=organization_scopes)),
    )


@pytest.mark.parametrize(
    ("token", "verifier"),
    [
        (_token(exp=datetime.now(timezone.utc) - timedelta(seconds=1)), _verifier()),
        (_token(key=WRONG_TEST_KEY), _verifier()),
        (_token(iss="other-issuer"), _verifier()),
        (_token(aud="other-audience"), _verifier()),
        (_token(iat=None), _verifier()),
        (_token(sub=None), _verifier()),
    ],
)
def test_jwt_verifier_rejects_expired_bad_signature_or_invalid_required_claims(
    token: str, verifier: JwtVerifier
) -> None:
    _assert_problem("INVALID_ACCESS_TOKEN", lambda: verifier.verify(token))


def test_jwt_verifier_rejects_missing_and_unsupported_claims() -> None:
    claims = _claims()
    del claims["iat"]
    missing = jwt.encode(claims, TEST_KEY, algorithm="HS256")
    _assert_problem("INVALID_ACCESS_TOKEN", lambda: _verifier().verify(missing))
    _assert_problem(
        "INVALID_ACCESS_TOKEN",
        lambda: _verifier().verify(_token(custom_authority=["admin"])),
    )


def test_jwt_verifier_forbids_none_and_algorithm_confusion() -> None:
    with pytest.raises(ValueError, match="unsigned"):
        _verifier(algorithms=["none"])
    unsigned = jwt.encode(_claims(), key="", algorithm="none")
    _assert_problem("INVALID_ACCESS_TOKEN", lambda: _verifier().verify(unsigned))


def test_approved_capability_is_authoritative_and_never_bleeds_across_projects() -> None:
    auth = AuthContext(
        subject_id="platform-session",
        project_ids=frozenset({"p1", "p2"}),
        region_codes=frozenset({"cn"}),
        scope_pairs=frozenset({("p1", "cn"), ("p2", "cn")}),
        scoped_capabilities=frozenset({("p1", "upload.manage")}),
        capability_revision=7,
    )
    ScopeGuard.require(auth, "p1", "cn")
    auth.require_capability("upload.manage", project_id="p1")
    _assert_problem(
        "CAPABILITY_REQUIRED",
        lambda: auth.require_capability("upload.manage", project_id="p2"),
    )


def test_upload_read_does_not_activate_upload_write_authority() -> None:
    reader = AuthContext(
        "upload-reader",
        frozenset({"p1"}),
        frozenset({"cn"}),
        scope_pairs=frozenset({("p1", "cn")}),
        scoped_capabilities=frozenset({("p1", "upload.read")}),
    )
    reader.require_capability("upload.read", "p1")
    _assert_problem("CAPABILITY_REQUIRED", lambda: reader.require_capability("upload.manage", "p1"))


def test_platform_admin_has_every_business_operation_across_real_projects() -> None:
    platform_admin = AuthContext(
        subject_id="platform-admin",
        organization_ids=frozenset({"org-a", "org-b"}),
        project_ids=frozenset({"project-a", "project-b"}),
        region_codes=frozenset(),
        capabilities=frozenset({CAPABILITY_PLATFORM_ADMIN}),
        organization_scope_triples=frozenset(
            {("org-a", "project-a", None), ("org-b", "project-b", None)}
        ),
    )

    for organization_id, project_id, region_code in (
        ("org-a", "project-a", "cn-hz"),
        ("org-b", "project-b", "eu-central"),
    ):
        ScopeGuard.require(platform_admin, project_id, region_code, organization_id)
        for capability in (
            "upload.manage",
            "annotation.edit",
            "annotation.review",
            "dataset_version.publish",
            "access.manage",
            "storage.lifecycle.execute",
            "robot.update",
            "data_schema.publish",
            "audit.export",
            "future.business.operation",
        ):
            assert platform_admin.has_capability(capability, project_id, organization_id)

    effective = platform_admin.effective_capabilities("project-a", "org-a")
    assert {"upload.manage", "annotation.review", "dataset_version.publish", "audit.read"} <= set(
        effective
    )
    _assert_problem(
        "PROJECT_NOT_FOUND",
        lambda: ScopeGuard.require(platform_admin, "missing-project", "cn-hz", "org-a"),
    )

    unverified_directory_claim = AuthContext(
        subject_id="platform-admin",
        project_ids=frozenset({"invented-project"}),
        region_codes=frozenset(),
        capabilities=frozenset({CAPABILITY_PLATFORM_ADMIN}),
    )
    _assert_problem(
        "PROJECT_NOT_FOUND",
        lambda: ScopeGuard.require(unverified_directory_claim, "invented-project"),
    )


def test_project_rls_context_installs_verified_platform_admin_bypass() -> None:
    class RecordingSession:
        def __init__(self) -> None:
            self.statement = ""
            self.parameters: dict[str, str] = {}

        async def execute(self, statement: object, parameters: dict[str, str]) -> None:
            self.statement = str(statement)
            self.parameters = parameters

    auth = AuthContext(
        subject_id="platform-admin",
        organization_ids=frozenset({"org-a"}),
        project_ids=frozenset({"project-a"}),
        region_codes=frozenset(),
        capabilities=frozenset({CAPABILITY_PLATFORM_ADMIN}),
        organization_scope_triples=frozenset({("org-a", "project-a", None)}),
    )
    session = RecordingSession()

    asyncio.run(
        RlsSessionContext.apply(  # type: ignore[arg-type]
            session,
            auth=auth,
            scope=ScopeSelection(organization_id="org-a", project_id="project-a"),
            request_id="request-a",
        )
    )

    assert "set_config('app.platform_admin', :platform_admin, true)" in session.statement
    assert "set_config('app.is_admin', :platform_admin, true)" in session.statement
    assert session.parameters["platform_admin"] == "true"


def test_platform_operation_capabilities_do_not_inherit_the_admin_wildcard() -> None:
    platform_admin = AuthContext(
        subject_id="platform-admin",
        project_ids=frozenset(),
        region_codes=frozenset(),
        capabilities=frozenset({CAPABILITY_PLATFORM_ADMIN}),
    )
    operator = AuthContext(
        subject_id="maintenance-operator",
        project_ids=frozenset(),
        region_codes=frozenset(),
        capabilities=frozenset({CAPABILITY_PLATFORM_MAINTENANCE_OPERATE}),
    )

    assert platform_admin.has_capability(CAPABILITY_PLATFORM_BREAK_GLASS)
    assert not platform_admin.has_exact_platform_capability(CAPABILITY_PLATFORM_BREAK_GLASS)
    _assert_problem(
        "PLATFORM_CAPABILITY_REQUIRED",
        lambda: platform_admin.require_exact_platform_capability(CAPABILITY_PLATFORM_BREAK_GLASS),
    )
    operator.require_exact_platform_capability(CAPABILITY_PLATFORM_MAINTENANCE_OPERATE)
    _assert_problem(
        "PLATFORM_CAPABILITY_REQUIRED",
        lambda: operator.require_exact_platform_capability(CAPABILITY_PLATFORM_BREAK_GLASS),
    )


def test_scope_repository_denies_cross_project_and_region_reads_and_writes() -> None:
    repository = InMemoryScopedRepository[ScopedResource]()
    p1_cn = AuthContext(
        "u1", frozenset({"p1"}), frozenset({"cn"}), scope_pairs=frozenset({("p1", "cn")})
    )
    p2_cn = AuthContext(
        "u2", frozenset({"p2"}), frozenset({"cn"}), scope_pairs=frozenset({("p2", "cn")})
    )
    p1_us = AuthContext(
        "u3", frozenset({"p1"}), frozenset({"us"}), scope_pairs=frozenset({("p1", "us")})
    )
    repository.add(p1_cn, "resource", ScopedResource("p1", "cn"))
    _assert_problem("PROJECT_SCOPE_DENIED", lambda: repository.get(p2_cn, "resource"))
    _assert_problem("REGION_SCOPE_DENIED", lambda: repository.get(p1_us, "resource"))
    _assert_problem(
        "PROJECT_SCOPE_DENIED",
        lambda: repository.add(p1_cn, "cross-project", ScopedResource("p2", "cn")),
    )
    _assert_problem(
        "REGION_SCOPE_DENIED",
        lambda: repository.add(p1_cn, "cross-region", ScopedResource("p1", "us")),
    )
    unscoped = AuthContext("unscoped", frozenset(), frozenset())
    _assert_problem("PROJECT_SCOPE_DENIED", lambda: repository.get(unscoped, "resource"))


def test_service_identity_requires_issued_explicit_scope() -> None:
    unscoped = AuthContext.service(subject_id="worker", capabilities=["upload.manage"])
    _assert_problem("SERVICE_SCOPE_REQUIRED", lambda: ScopeGuard.require(unscoped, "p1", "cn"))
    project_only = AuthContext.service(
        subject_id="worker", capabilities=["upload.manage"], project_ids=["p1"]
    )
    _assert_problem("SERVICE_SCOPE_REQUIRED", lambda: ScopeGuard.require(project_only, "p1", "cn"))
    scoped = AuthContext.service(
        subject_id="worker",
        capabilities=["upload.manage"],
        project_ids=["p1"],
        region_codes=["cn"],
    )
    ScopeGuard.require(scoped, "p1", "cn")


def test_idempotency_is_single_effect_under_100_way_concurrency() -> None:
    store = InMemoryIdempotencyStore()
    calls = 0
    calls_lock = Lock()

    def action() -> dict[str, str]:
        nonlocal calls
        with calls_lock:
            calls += 1
        return {"status": "created"}

    def execute() -> dict[str, str]:
        return store.execute(scope="p1/create", key="same", payload={"x": 1}, action=action).value

    with ThreadPoolExecutor(max_workers=20) as pool:
        assert list(pool.map(lambda _: execute(), range(100))) == [{"status": "created"}] * 100
    assert calls == 1

    conflict = lambda: store.execute(  # noqa: E731
        scope="p1/create", key="same", payload={"x": 2}, action=action
    )
    _assert_problem("IDEMPOTENCY_KEY_REUSED", conflict)


def test_resource_version_etag_precondition() -> None:
    version = ResourceVersion()
    assert ResourceVersion.from_etag('"v12"').value == 12
    assert version.next('"v1"').etag == '"v2"'
    with pytest.raises(ProblemException) as captured:
        version.next('"v0"')
    assert captured.value.problem.status == 412
    assert captured.value.problem.code == "ETAG_MISMATCH"


def test_fake_unit_of_work_commits_or_rolls_back_business_audit_and_outbox_atomically() -> None:
    async def scenario() -> None:
        database = InMemoryAtomicDatabase()
        auth = AuthContext(
            "u1",
            frozenset({"p1"}),
            frozenset({"cn"}),
            scope_pairs=frozenset({("p1", "cn")}),
        )

        async with InMemoryScopedUnitOfWork(
            database, auth=auth, project_id="p1", region_code="cn"
        ) as rolled_back:
            rolled_back.repository("resources").add("r1", {"value": "discard"})
            rolled_back.audit.append(_audit_record("r1"))
            rolled_back.outbox.stage(_event("r1"))

        assert database.tables == {}
        assert database.audit_records == []
        assert database.outbox_events == []

        async with InMemoryScopedUnitOfWork(
            database, auth=auth, project_id="p1", region_code="cn"
        ) as committed:
            committed.repository("resources").add("r1", {"value": "keep"})
            committed.audit.append(_audit_record("r1"))
            committed.outbox.stage(_event("r1"))
            await committed.commit()

        assert database.tables["resources"]["r1"]["value"] == "keep"
        assert [record.resource_id for record in database.audit_records] == ["r1"]
        assert [event.aggregate_id for event in database.outbox_events] == ["r1"]

    asyncio.run(scenario())


def test_canonical_audit_hash_is_stable_for_mapping_order() -> None:
    assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})


def _audit_record(resource_id: str) -> AuditRecord:
    return AuditRecord(
        actor_id="u1",
        action="resource.created",
        resource_type="resource",
        resource_id=resource_id,
        project_id="p1",
        region_code="cn",
        request_id="request-1",
        after_hash=canonical_hash({"resource_id": resource_id}),
    )


def _event(resource_id: str) -> DomainEventEnvelope:
    return DomainEventEnvelope(
        event_type="ResourceCreatedV1",
        aggregate_type="resource",
        aggregate_id=resource_id,
        project_id="p1",
        region_code="cn",
    )
