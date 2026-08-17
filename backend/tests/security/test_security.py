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
from hc_data_platform.security.auth import AuthContext, JwtVerifier, Permission, Role
from hc_data_platform.security.idempotency import InMemoryIdempotencyStore
from hc_data_platform.security.scope import (
    InMemoryScopedRepository,
    ScopedResource,
    ScopeGuard,
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
        "project_ids": ["p1"],
        "region_codes": ["cn-hz"],
        "roles": ["uploader"],
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
    assert context.roles == frozenset({"uploader"})


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


def test_jwt_verifier_rejects_missing_claim_and_unknown_role() -> None:
    claims = _claims()
    del claims["iat"]
    missing = jwt.encode(claims, TEST_KEY, algorithm="HS256")
    _assert_problem("INVALID_ACCESS_TOKEN", lambda: _verifier().verify(missing))
    _assert_problem("UNKNOWN_ROLE", lambda: _verifier().verify(_token(roles=["owner"])))


def test_jwt_verifier_forbids_none_and_algorithm_confusion() -> None:
    with pytest.raises(ValueError, match="unsigned"):
        _verifier(algorithms=["none"])
    unsigned = jwt.encode(_claims(), key="", algorithm="none")
    _assert_problem("INVALID_ACCESS_TOKEN", lambda: _verifier().verify(unsigned))


@pytest.mark.parametrize(
    ("role", "granted", "denied"),
    [
        (Role.UPLOADER, Permission.UPLOAD, Permission.ANNOTATE),
        (Role.ANNOTATOR, Permission.ANNOTATE, Permission.REVIEW),
        (Role.REVIEWER, Permission.REVIEW, Permission.PUBLISH),
        (Role.PUBLISHER, Permission.PUBLISH, Permission.ADMINISTER),
    ],
)
def test_project_roles_have_explicit_permissions(
    role: Role, granted: Permission, denied: Permission
) -> None:
    auth = AuthContext("u1", frozenset({"p1"}), frozenset({"cn"}), frozenset({role.value}))
    auth.require_permission(granted)
    _assert_problem("PERMISSION_REQUIRED", lambda: auth.require_permission(denied))
    admin = AuthContext("a1", frozenset(), frozenset(), frozenset({Role.ADMIN.value}))
    admin.require_permission(denied)


def test_scope_repository_denies_cross_project_and_region_reads_and_writes() -> None:
    repository = InMemoryScopedRepository[ScopedResource]()
    p1_cn = AuthContext("u1", frozenset({"p1"}), frozenset({"cn"}), frozenset({"uploader"}))
    p2_cn = AuthContext("u2", frozenset({"p2"}), frozenset({"cn"}), frozenset({"uploader"}))
    p1_us = AuthContext("u3", frozenset({"p1"}), frozenset({"us"}), frozenset({"uploader"}))
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


def test_service_identity_requires_issued_explicit_scope_even_when_admin() -> None:
    unscoped = AuthContext.service(subject_id="worker", roles=[Role.ADMIN])
    _assert_problem("SERVICE_SCOPE_REQUIRED", lambda: ScopeGuard.require(unscoped, "p1", "cn"))
    project_only = AuthContext.service(subject_id="worker", roles=[Role.ADMIN], project_ids=["p1"])
    _assert_problem("SERVICE_SCOPE_REQUIRED", lambda: ScopeGuard.require(project_only, "p1", "cn"))
    scoped = AuthContext.service(
        subject_id="worker",
        roles=[Role.ADMIN],
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
        auth = AuthContext("u1", frozenset({"p1"}), frozenset({"cn"}), frozenset({"uploader"}))

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
