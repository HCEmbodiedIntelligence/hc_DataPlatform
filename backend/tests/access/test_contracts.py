from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.errors import ValidationError
from app.domains.access import models
from app.domains.access.router import router
from app.domains.access.schemas import (
    ProjectScope,
    ScopeGrantInput,
)
from app.domains.access.service import (
    REGISTRY,
    Sha256ChainIntegrityStrategy,
    project_audit_record,
    validate_event_name,
    validate_safe_audit_value,
    validate_scope_grant,
)


def test_registry_is_parsed_and_roles_are_exact() -> None:
    assert len(REGISTRY.canonical_capabilities) == 76
    assert len(REGISTRY.reserved_capabilities) == 18
    assert len(REGISTRY.canonical_events) == 142
    assert REGISTRY.canonical_capabilities.isdisjoint(REGISTRY.reserved_capabilities)
    assert set(REGISTRY.role_capabilities) == {
        "PROJECT_ADMIN",
        "PROJECT_DEVELOPER",
        "PROJECT_DATA_PROCESSOR",
    }
    assert len(REGISTRY.role_capabilities["PROJECT_DEVELOPER"]) == 34
    assert len(REGISTRY.role_capabilities["PROJECT_DATA_PROCESSOR"]) == 19


def test_scope_grant_cannot_expand_role_ceiling() -> None:
    grant = ScopeGrantInput(
        scope=ProjectScope(type="PROJECT", organization_id="org_fx_01", project_id="prj_fx_01"),
        effect="DENY",
        capability_keys=["audit.export"],
        inherit=True,
    )
    with pytest.raises(ValidationError) as caught:
        validate_scope_grant(grant, "PROJECT_DATA_PROCESSOR")
    assert caught.value.http_status == 422
    assert caught.value.code == "ROLE_CAPABILITY_CEILING_EXCEEDED"
    assert caught.value.field_errors[0]["path"] == "grant.capability_keys"


def test_allow_with_no_narrowing_effect_is_rejected() -> None:
    grant = ScopeGrantInput(
        scope=ProjectScope(type="PROJECT", organization_id="org_fx_01", project_id="prj_fx_01"),
        effect="ALLOW",
        capability_keys=["dataset.read"],
        inherit=True,
    )
    with pytest.raises(ValidationError) as caught:
        validate_scope_grant(grant, "PROJECT_DEVELOPER")
    assert caught.value.code == "SCOPE_GRANT_NO_EFFECT"


def test_unregistered_audit_event_is_rejected() -> None:
    with pytest.raises(ValidationError) as caught:
        validate_event_name("audit.event.made_up")
    assert caught.value.code == "AUDIT_EVENT_NAME_UNREGISTERED"


@pytest.mark.parametrize(
    ("value", "path"),
    [
        ({"access_token": "secret"}, "change.access_token"),
        ({"object_key": "tenant/project/full/path"}, "change.object_key"),
        ({"url": "https://x.test/file?signature=abc"}, "change.url"),
    ],
)
def test_sensitive_audit_content_never_reaches_storage(value, path) -> None:
    with pytest.raises(ValidationError) as caught:
        validate_safe_audit_value(value)
    assert caught.value.code == "AUDIT_FORBIDDEN_CONTENT"
    assert caught.value.field_errors[0]["path"] == path


def _event_row() -> models.AuditEventModel:
    now = datetime.now(UTC)
    return models.AuditEventModel(
        event_id="event_fx_01",
        scope_key="scope_fx",
        organization_id="org_fx_01",
        project_id="prj_fx_01",
        region_code="cn-shanghai",
        event_name="access.invitation.created",
        occurred_at=now,
        recorded_at=now,
        ingest_sequence=1,
        actor_principal_id="principal_fx",
        resource_type="access.invitation",
        resource_id="invitation_fx",
        request_id="request_fx",
        producer_service="access",
        producer_event_id="producer_fx",
        input_payload={
            "actor": {
                "type": "HUMAN",
                "principal_id": "principal_fx",
                "display_name": "Admin",
                "role_ids": ["PROJECT_ADMIN"],
                "delegated_by_principal_id": "sensitive-delegator",
            },
            "scope": {
                "organization_id": "org_fx_01",
                "project_id": "prj_fx_01",
                "region_code": "cn-shanghai",
            },
            "resource": {
                "type": "access.invitation",
                "id": "invitation_fx",
                "display_name": "safe",
                "parent_refs": [],
            },
            "request": {
                "request_id": "request_fx",
                "job_id": None,
                "client_type": "WEB",
                "ip_address": "192.0.2.1",
                "device_summary": "exact-device",
            },
            "outcome": {"status": "SUCCEEDED", "reason_code": None, "http_status": 201},
            "change": {
                "summary_code": "CREATED",
                "changed_fields": [],
                "before": {},
                "after": {},
                "omitted_field_classes": [],
            },
            "relationships": {
                "parent_event_id": None,
                "related_event_ids": [],
                "resource_refs": [],
            },
            "producer": {
                "service": "access",
                "producer_event_id": "producer_fx",
                "contract_version": "1",
            },
        },
        catalog_version="p19-v1-142",
        outcome_status="SUCCEEDED",
        risk_level="LOW",
        risk_payload={"signal_codes": [], "policy_version": "v1"},
        retention_class="STANDARD",
        retention_policy_version="v1",
        retain_until=None,
        legal_hold=False,
        integrity_version="sha256-chain-v1-conditional",
        record_digest="a" * 64,
        checkpoint_id=None,
    )


def test_field_projection_omits_sensitive_keys_instead_of_nulling() -> None:
    standard = project_audit_record(_event_row(), {"audit.read"})
    assert "change" not in standard
    assert "producer" not in standard
    assert "record_digest" not in standard["integrity"]
    assert "ip_address" not in standard["request"]
    assert "device_summary" not in standard["request"]
    assert "delegated_by_principal_id" not in standard["actor"]
    elevated = project_audit_record(_event_row(), {"audit.read", "audit.export"})
    assert elevated["change"]["summary_code"] == "CREATED"
    assert elevated["producer"]["service"] == "access"
    assert elevated["integrity"]["record_digest"] == "a" * 64


def test_integrity_strategy_is_deterministic_and_detects_tampering() -> None:
    strategy = Sha256ChainIntegrityStrategy()
    payload = {"event_name": "access.invitation.created", "value": 1}
    digest = strategy.digest(payload, "previous")
    assert strategy.verify(payload, "previous", digest)
    assert not strategy.verify({**payload, "value": 2}, "previous", digest)


def test_audit_model_has_required_constraints_and_stable_index() -> None:
    names = {constraint.name for constraint in models.AuditEventModel.__table__.constraints}
    indexes = {index.name for index in models.AuditEventModel.__table__.indexes}
    assert "uq_audit_event_id" in names
    assert "uq_audit_event_producer_event" in names
    assert "ix_audit_event_scope_occurred_event" in indexes
    audit_event_mutations = [
        route
        for route in router.routes
        if "/audit/events" in route.path and ({"POST", "PATCH", "PUT", "DELETE"} & route.methods)
    ]
    assert audit_event_mutations == []
