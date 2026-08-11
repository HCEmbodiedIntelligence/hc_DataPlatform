from __future__ import annotations

import asyncio

import pytest
from conftest import TEST_SESSIONS
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, select
from starlette.requests import Request

from app.core.audit import AuditRecord, redact_detail, write_audit
from app.core.context import RequestContext, require
from app.core.errors import (
    DomainError,
    ForbiddenError,
    PreconditionFailedError,
    ServerError,
    ValidationError,
    VersionConflictError,
    error_envelope,
)
from app.core.etag import check_if_match
from app.core.idempotency import IdempotencyRecord, with_idempotency
from app.core.int64 import ByteCount, Int64
from app.core.outbox import OutboxRecord, emit_event
from app.core.pagination import CursorParams, build_page, decode_cursor, encode_cursor

CTX = RequestContext(
    request_id="req_fx_01",
    actor_id="user_fx_01",
    organization_id="org_fx_01",
    project_id="prj_fx_01",
    region_code="cn-shanghai",
    capabilities=frozenset({"upload.manage"}),
)


def test_domain_error_envelope_is_exact() -> None:
    error = DomainError("BROKEN", 409, "No", retryable=True)
    assert error_envelope(error, "req_1") == {
        "error": {
            "code": "BROKEN",
            "message": "No",
            "field_errors": [],
            "operation_errors": [],
            "blocked_reasons": [],
            "request_id": "req_1",
            "retryable": True,
        }
    }
    assert ForbiddenError.http_status == 403
    statuses = {
        "ValidationError": 422,
        "NotFoundError": 404,
        "ForbiddenError": 403,
        "UnauthenticatedError": 401,
        "VersionConflictError": 409,
        "PreconditionFailedError": 412,
        "GoneError": 410,
        "RateLimitedError": 429,
        "ServerError": 500,
    }
    import app.core.errors as errors

    assert {name: getattr(errors, name).http_status for name in statuses} == statuses


def test_cursor_round_trip_and_page_boundaries() -> None:
    cursor = encode_cursor(updated_at="2026-08-05T08:00:00Z", source_id="source_fx_01")
    assert decode_cursor(cursor)["source_id"] == "source_fx_01"
    page = build_page(
        [{"updated_at": "a", "source_id": "1"}, {"updated_at": "b", "source_id": "2"}],
        CursorParams(limit=1),
        ("updated_at", "source_id"),
    )
    assert page["page_info"]["has_next_page"] is True
    with pytest.raises(ValidationError):
        CursorParams(after="a", before="b")


def test_int64_is_int_inside_and_decimal_on_wire() -> None:
    class Values(BaseModel):
        count: Int64
        size: ByteCount

    value = Values.model_validate({"count": "9223372036854775807", "size": "42"})
    assert value.count == 9223372036854775807
    assert value.model_dump(mode="json") == {"count": "9223372036854775807", "size": "42"}
    with pytest.raises(PydanticValidationError):
        Values.model_validate({"count": "01", "size": "1"})


@pytest.mark.asyncio
async def test_idempotency_replays_the_exact_persisted_result(db_session) -> None:
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        return {"resource_id": "upload_fx_01", "status": "CREATED"}

    first = await with_idempotency(db_session, "idem-key-00000001", {"scope": "fx"}, operation)
    second = await with_idempotency(db_session, "idem-key-00000001", {"scope": "fx"}, operation)
    record = await db_session.scalar(select(IdempotencyRecord))
    assert first == second
    assert calls == 1
    assert record.response_body_or_resource_ref == first


@pytest.mark.asyncio
async def test_concurrent_idempotency_conflicts() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_operation():
        entered.set()
        await release.wait()
        return {"ok": True}

    async with TEST_SESSIONS() as first, TEST_SESSIONS() as second:
        async with first.begin(), second.begin():
            task = asyncio.create_task(
                with_idempotency(
                    first, "idem-key-concurrent", {"scope": "concurrent"}, slow_operation
                )
            )
            await entered.wait()
            with pytest.raises(VersionConflictError):
                await with_idempotency(
                    second, "idem-key-concurrent", {"scope": "concurrent"}, slow_operation
                )
            release.set()
            assert await task == {"ok": True}


@pytest.mark.asyncio
async def test_audit_and_outbox_share_transaction_and_redact(db_session) -> None:
    assert redact_detail({"token": "raw", "object_key": "private/a"})["token"]["redacted"]
    await write_audit(
        db_session,
        "upload.session.created",
        "ingest.upload_session",
        "upload_fx_01",
        "SUCCEEDED",
        CTX,
        {"access_key_secret": "raw"},
    )
    await emit_event(
        db_session,
        "upload.session.created",
        "ingest.upload_session",
        "upload_fx_01",
        {"upload_id": "upload_fx_01"},
        CTX,
    )
    assert await db_session.scalar(select(func.count()).select_from(AuditRecord)) == 1
    assert await db_session.scalar(select(func.count()).select_from(OutboxRecord)) == 1


@pytest.mark.asyncio
async def test_outbox_rolls_back_and_unknown_audit_is_rejected() -> None:
    async with TEST_SESSIONS() as session:
        with pytest.raises(RuntimeError):
            async with session.begin():
                await emit_event(
                    session,
                    "upload.session.created",
                    "ingest.upload_session",
                    "upload_fx_rollback",
                    {"upload_id": "upload_fx_rollback"},
                    CTX,
                )
                raise RuntimeError("rollback")
        assert await session.scalar(select(func.count()).select_from(OutboxRecord)) == 0
        with pytest.raises(ServerError):
            await write_audit(session, "not.registered", "test", "test_fx", "SUCCEEDED", CTX)


@pytest.mark.asyncio
async def test_require_fails_closed_and_etag_is_enforced() -> None:
    dependency = require("upload.manage")
    missing = RequestContext(
        request_id="req",
        actor_id="actor",
        organization_id="org",
        project_id="prj",
        region_code="cn-shanghai",
        capabilities=frozenset(),
    )
    with pytest.raises(ForbiddenError):
        await dependency(missing)
    with pytest.raises(ForbiddenError):
        await require("capability.unknown")(CTX)

    matching = Request(
        {
            "type": "http",
            "method": "PATCH",
            "path": "/",
            "headers": [(b"if-match", b'"rv-1"')],
        }
    )
    check_if_match(matching, '"rv-1"')
    stale = Request(
        {
            "type": "http",
            "method": "PATCH",
            "path": "/",
            "headers": [(b"if-match", b'"rv-0"')],
        }
    )
    with pytest.raises(PreconditionFailedError):
        check_if_match(stale, '"rv-1"')


@pytest.mark.asyncio
async def test_main_exposes_exact_ingest_operations(client) -> None:
    schema = (await client.get("/openapi.json")).json()
    actual = {
        operation["operationId"]
        for path in schema["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict)
        and "operationId" in operation
        and operation["operationId"]
        in {
            "getDataSourcesPage",
            "listDataSources",
            "getDataSource",
            "createDataSource",
            "updateDataSource",
            "rotateDataSourceCredential",
            "testDataSourceConnection",
            "enableDataSource",
            "disableDataSource",
            "listUploadSessions",
            "summarizeUploadSessions",
            "getUploadCreationOptions",
            "createUploadSession",
            "getUploadSessionBootstrap",
            "renewUploadAuthorization",
            "pauseUploadSession",
            "resumeUploadSession",
            "retryUploadParts",
            "submitUploadManifest",
            "retryUploadVerification",
            "createReplacementUpload",
            "cancelUploadSession",
            "listUploadObjects",
            "listUploadParts",
            "getSourceUploadManifest",
            "listSourceManifestNodes",
            "listVerificationRuns",
            "listVerificationFindings",
            "listUploadEvents",
        }
    }
    assert len(actual) == 29
