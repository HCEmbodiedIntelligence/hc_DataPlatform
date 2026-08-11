"""Access policy, audit validation, projection and integrity services."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    ForbiddenError,
    GoneError,
    NotFoundError,
    PreconditionFailedError,
    ValidationError,
    VersionConflictError,
)
from app.core.etag import compute_etag
from app.core.ids import new_id

from .models import (
    AccessPreflightModel,
    AuditEventModel,
    AuditExportJobModel,
    AuditOutboxModel,
    InvitationModel,
    ScopeGrantModel,
)
from .repository import AccessRepository, AuditRepository
from .schemas import (
    AccessChangePreflightRequest,
    AddScopeGrantOperation,
    AuditEventInput,
    AuditExportCreateCommand,
    AuditExportPreflightRequest,
    AuditScope,
    InvitationPreflightRequest,
    ScopeGrantInput,
)

CATALOG_VERSION = "frontend-shared-contracts-v0.8-review"
ROLE_VERSION = "roles-v2-conditional"
EVENT_CATALOG_VERSION = "p19-v1-142"
POLICY_VERSION = "access-policy-v1-conditional"
RETENTION_POLICY_VERSION = "audit-retention-v1-conditional"
REDACTION_POLICY_VERSION = "audit-redaction-v1"
INTEGRITY_STRATEGY_VERSION = "sha256-chain-v1-conditional"
AUDIT_SORT = "occurred_at:desc,event_id:desc"


def _domain_error(
    error_type: type[Exception], code: str, message: str, **details: Any
) -> Exception:
    """Construct a frozen core error while tolerating its supported call styles."""

    attempts = (
        {"code": code, "message": message, **details},
        {"message": message, "code": code, "details": details or None},
        {"message": message, **details},
    )
    for kwargs in attempts:
        try:
            return error_type(**kwargs)
        except TypeError:
            continue
    return error_type(message)  # type: ignore[call-arg]


def validation_error(code: str, message: str, path: str | None = None) -> Exception:
    field_errors = [] if path is None else [{"path": path, "code": code, "message": message}]
    return _domain_error(ValidationError, code, message, field_errors=field_errors)


def canonical_json(value: Any) -> bytes:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def utc_now() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def project_scope_key(organization_id: str, project_id: str, region_code: str | None = None) -> str:
    region = region_code if region_code is not None else "*"
    return f"organization:{organization_id}:project:{project_id}:region:{region}"


def subject_key(subject: Mapping[str, Any]) -> str:
    if subject.get("type") == "PROJECT_MEMBERSHIP":
        return f"project-membership:{subject['project_membership_id']}"
    return f"organization-membership:{subject['organization_membership_id']}"


def structured_scope_key(scope: Mapping[str, Any]) -> str:
    values = [str(scope.get("organization_id", ""))]
    for key in ("project_id", "region_code", "resource_set_id", "resource_type", "resource_id"):
        if scope.get(key) is not None:
            values.append(str(scope[key]))
    return f"{scope.get('type', 'UNKNOWN').lower()}:" + ":".join(values)


@dataclass(frozen=True)
class RegistrySnapshot:
    catalog_version: str
    canonical_capabilities: frozenset[str]
    reserved_capabilities: frozenset[str]
    canonical_events: frozenset[str]
    role_capabilities: Mapping[str, frozenset[str]]
    missing_event_producers: tuple[str, ...]

    @classmethod
    def load(cls, path: Path | None = None) -> RegistrySnapshot:
        source = path or Path(__file__).with_name("capability-event-registry.json")
        raw = json.loads(source.read_text(encoding="utf-8"))
        capabilities = raw["capabilities"]
        canonical = frozenset(
            row["capability"] for row in capabilities if row["classification"] == "canonical"
        )
        reserved = frozenset(
            row["capability"]
            for row in capabilities
            if row["classification"] == "reserved-denylist"
        )
        events = frozenset(row["event"] for row in raw["audit_events"] if row["in_canonical_142"])
        roles = {
            "PROJECT_ADMIN": canonical,
            "PROJECT_DEVELOPER": frozenset(
                row["capability"] for row in capabilities if row.get("in_developer_34")
            ),
            "PROJECT_DATA_PROCESSOR": frozenset(
                row["capability"] for row in capabilities if row.get("in_data_processor_19")
            ),
        }
        missing = tuple(
            sorted(
                row["event"] for row in raw["audit_events"] if not row.get("declared_by_domains")
            )
        )
        totals = raw["capability_totals"]
        audit_totals = raw["audit_totals"]
        if len(canonical) != totals["canonical"] or len(reserved) != totals["reserved_denylist"]:
            raise RuntimeError("capability registry totals do not match parsed sets")
        if canonical & reserved:
            raise RuntimeError("canonical and reserved capability sets overlap")
        if len(events) != audit_totals["distinct_canonical"]:
            raise RuntimeError("event registry totals do not match parsed set")
        if {len(roles["PROJECT_DEVELOPER"]), len(roles["PROJECT_DATA_PROCESSOR"])} != {
            totals["developer"],
            totals["data_processor"],
        }:
            raise RuntimeError("role capability totals do not match parsed sets")
        return cls(
            catalog_version=CATALOG_VERSION,
            canonical_capabilities=canonical,
            reserved_capabilities=reserved,
            canonical_events=events,
            role_capabilities=roles,
            missing_event_producers=missing,
        )


REGISTRY = RegistrySnapshot.load()


def validate_capability(capability: str) -> None:
    if capability not in REGISTRY.canonical_capabilities:
        raise validation_error(
            "CAPABILITY_UNKNOWN", "Capability is unknown or reserved", "capability"
        )


def validate_event_name(event_name: str) -> None:
    if event_name not in REGISTRY.canonical_events:
        raise validation_error(
            "AUDIT_EVENT_NAME_UNREGISTERED",
            "Audit event name is not present in the canonical registry",
            "event_name",
        )


def validate_scope_grant(grant: ScopeGrantInput, role_id: str) -> None:
    ceiling = REGISTRY.role_capabilities.get(role_id)
    if ceiling is None:
        raise validation_error("ROLE_UNKNOWN", "Unknown fixed project role", "role_id")
    unknown = sorted(set(grant.capability_keys) - REGISTRY.canonical_capabilities)
    if unknown:
        raise validation_error(
            "CAPABILITY_UNKNOWN",
            f"Unknown or reserved capabilities: {', '.join(unknown)}",
            "grant.capability_keys",
        )
    overflow = sorted(set(grant.capability_keys) - ceiling)
    if overflow:
        raise validation_error(
            "ROLE_CAPABILITY_CEILING_EXCEEDED",
            f"Grant exceeds the role ceiling: {', '.join(overflow)}",
            "grant.capability_keys",
        )
    if grant.effect == "ALLOW":
        # In V1 base == ceiling; ALLOW therefore cannot narrow and is rejected.
        raise validation_error(
            "SCOPE_GRANT_NO_EFFECT",
            "ALLOW has no narrowing effect while role base equals its ceiling",
            "grant.effect",
        )


FORBIDDEN_AUDIT_KEY = re.compile(
    r"(?:^|[_-])(token|cookie|session|sts|secret|password|credential|authorization|"
    r"access[_-]?key|signed[_-]?url|object[_-]?key|sql|stack|host[_-]?path)(?:$|[_-])",
    re.IGNORECASE,
)
FORBIDDEN_AUDIT_VALUE = re.compile(
    r"(?:https?://\S+[?&](?:x-amz-|signature|token|key)=|bearer\s+[a-z0-9._~-]+|"
    r"-----BEGIN [A-Z ]+PRIVATE KEY-----)",
    re.IGNORECASE,
)


def validate_safe_audit_value(value: Any, path: str = "change") -> None:
    """Reject secret-bearing or unrestricted audit data before it reaches storage."""

    if isinstance(value, Mapping):
        if len(value) > 100:
            raise validation_error(
                "AUDIT_VALUE_TOO_LARGE", "Audit object exceeds 100 properties", path
            )
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if len(str(key)) > 128:
                raise validation_error(
                    "AUDIT_KEY_TOO_LONG", "Audit key exceeds 128 characters", child_path
                )
            if FORBIDDEN_AUDIT_KEY.search(str(key)):
                raise validation_error(
                    "AUDIT_FORBIDDEN_CONTENT", "Sensitive audit field is forbidden", child_path
                )
            validate_safe_audit_value(child, child_path)
        return
    if isinstance(value, list):
        if len(value) > 100:
            raise validation_error("AUDIT_VALUE_TOO_LARGE", "Audit array exceeds 100 items", path)
        for index, child in enumerate(value):
            if isinstance(child, (Mapping, list)):
                raise validation_error(
                    "AUDIT_VALUE_NESTING_FORBIDDEN",
                    "Nested audit arrays/objects are not safe scalars",
                    path,
                )
            validate_safe_audit_value(child, f"{path}[{index}]")
        return
    if isinstance(value, str):
        if len(value) > 512:
            raise validation_error(
                "AUDIT_VALUE_TOO_LONG", "Audit value exceeds 512 characters", path
            )
        if FORBIDDEN_AUDIT_VALUE.search(value):
            raise validation_error(
                "AUDIT_FORBIDDEN_CONTENT", "Sensitive audit value is forbidden", path
            )


class IntegrityStrategy(Protocol):
    version: str

    def digest(self, payload: Mapping[str, Any], previous_digest: str | None) -> str: ...

    def verify(
        self, payload: Mapping[str, Any], previous_digest: str | None, expected_digest: str
    ) -> bool: ...


class Sha256ChainIntegrityStrategy:
    version = INTEGRITY_STRATEGY_VERSION

    def digest(self, payload: Mapping[str, Any], previous_digest: str | None) -> str:
        chain_input = {"previous_digest": previous_digest, "record": payload}
        return digest_json(chain_input)

    def verify(
        self, payload: Mapping[str, Any], previous_digest: str | None, expected_digest: str
    ) -> bool:
        return hmac.compare_digest(self.digest(payload, previous_digest), expected_digest)


def project_audit_record(row: AuditEventModel, capabilities: set[str]) -> dict[str, Any]:
    """Create a field-level projection; absent permission means absent key, never null."""

    if "audit.read" not in capabilities:
        raise _domain_error(ForbiddenError, "FORBIDDEN", "audit.read is required")
    source = row.input_payload
    projection: dict[str, Any] = {
        "schema_version": 1,
        "event_id": row.event_id,
        "event_name": row.event_name,
        "occurred_at": row.occurred_at.isoformat(),
        "recorded_at": row.recorded_at.isoformat(),
        "actor": {
            key: value
            for key, value in source["actor"].items()
            if key in {"type", "principal_id", "display_name", "role_ids"}
        },
        "scope": source["scope"],
        "resource": source["resource"],
        "request": {
            key: value
            for key, value in source["request"].items()
            if key in {"request_id", "job_id", "client_type"}
        },
        "outcome": source["outcome"],
        "risk": {"level": row.risk_level, "signal_codes": row.risk_payload["signal_codes"]},
        "relationships": source["relationships"],
        "retention": {
            "class": row.retention_class,
            "policy_version": row.retention_policy_version,
            "retain_until": row.retain_until.isoformat() if row.retain_until else None,
            "legal_hold": row.legal_hold,
        },
        "integrity": {
            "status": "UNKNOWN" if row.checkpoint_id is None else "PASSED",
            "version": row.integrity_version,
            "checkpoint_id": row.checkpoint_id,
        },
        "allowed_actions": ["VIEW", "VIEW_RESOURCE"],
        "blocked_reasons": [],
    }
    # Export-capable readers receive the safe change summary and producer identity.
    if "audit.export" in capabilities:
        projection["change"] = source.get("change")
        projection["producer"] = source["producer"]
        projection["integrity"]["record_digest"] = row.record_digest
        projection["allowed_actions"].extend(["EXPORT", "EXPORT_EVENT"])
    return projection


class AccessService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = AccessRepository(session)

    def capability_catalog(self) -> dict[str, Any]:
        return {
            "catalog_version": REGISTRY.catalog_version,
            "contract_status": "CONDITIONAL",
            "capability_keys": sorted(REGISTRY.canonical_capabilities),
        }

    def roles(self) -> list[dict[str, Any]]:
        names = {
            "PROJECT_ADMIN": "管理员",
            "PROJECT_DEVELOPER": "开发者",
            "PROJECT_DATA_PROCESSOR": "数据处理员",
        }
        return [
            {
                "role_id": role_id,
                "display_name": names[role_id],
                "role_version": ROLE_VERSION,
                "catalog_version": REGISTRY.catalog_version,
                "base_capability_keys": sorted(capabilities),
                "capability_ceiling_keys": sorted(capabilities),
                "status": "ACTIVE",
            }
            for role_id, capabilities in REGISTRY.role_capabilities.items()
        ]

    async def preflight_access_change(
        self,
        project_id: str,
        actor_principal_id: str,
        request: AccessChangePreflightRequest,
    ) -> dict[str, Any]:
        affected: set[str] = set()
        changes: list[dict[str, Any]] = []
        for index, operation in enumerate(request.operations):
            member = await self.repo.get_member(project_id, operation.membership_id)
            if member is None:
                raise _domain_error(NotFoundError, "MEMBER_NOT_FOUND", "Member not found")
            affected.add(member.member_id)
            if isinstance(operation, AddScopeGrantOperation):
                validate_scope_grant(operation.grant, member.role_id)
            changes.append(
                {"ordinal": index, "type": operation.type, "membership_id": member.member_id}
            )
        payload = request.model_dump(mode="json")
        operation_hash = digest_json(payload["operations"])
        token = secrets.token_urlsafe(48)
        now = utc_now()
        preflight_id = new_id("event")
        self.repo.add_preflight(
            AccessPreflightModel(
                preflight_id=preflight_id,
                project_id=project_id,
                actor_principal_id=actor_principal_id,
                operation_hash=operation_hash,
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                payload=payload,
                policy_revision=request.project_policy_revision,
                policy_etag=request.project_policy_etag,
                expires_at=now + timedelta(minutes=5),
            )
        )
        return {
            "preflight_id": preflight_id,
            "preflight_token": token,
            "preflight_expires_at": (now + timedelta(minutes=5)).isoformat(),
            "project_policy_revision": request.project_policy_revision,
            "project_policy_etag": request.project_policy_etag,
            "normalized_operation_hash": operation_hash,
            "reason_hash": hashlib.sha256(request.reason.encode()).hexdigest(),
            "risk": "HIGH",
            "warnings": [],
            "effective_access_diff": {
                "membership_id": sorted(affected)[0],
                "role_change": None,
                "revoked_incompatible_grant_ids": [],
                "changes": changes,
                "last_admin_impact": {"before_count": "0", "after_count": "0", "blocked": False},
                "affected_resource_count": str(len(affected)),
            },
        }

    async def commit_access_change(self, project_id: str, token: str) -> dict[str, Any]:
        now = utc_now()
        preflight = await self.repo.consume_preflight(
            hashlib.sha256(token.encode()).hexdigest(), now
        )
        if preflight is None or preflight.project_id != project_id:
            raise _domain_error(
                GoneError, "PREFLIGHT_INVALID", "Preflight is expired, used, or out of scope"
            )
        request = AccessChangePreflightRequest.model_validate(preflight.payload)
        affected: list[str] = []
        for operation in request.operations:
            member = await self.repo.get_member(project_id, operation.membership_id)
            if member is None:
                raise _domain_error(NotFoundError, "MEMBER_NOT_FOUND", "Member not found")
            if member.etag != operation.membership_etag:
                raise _domain_error(PreconditionFailedError, "ETAG_MISMATCH", "Member ETag changed")
            affected.append(member.member_id)
            if operation.type == "CHANGE_ROLE" or operation.type == "ENABLE_MEMBERSHIP":
                member.role_id = operation.role_id
                member.role_version = operation.role_version
                member.status = "ACTIVE"
            elif operation.type == "DISABLE_MEMBERSHIP":
                member.status = "DISABLED"
            elif operation.type == "ADD_SCOPE_GRANT":
                validate_scope_grant(operation.grant, member.role_id)
                grant_payload = operation.grant.model_dump(mode="json")
                self.repo.add_scope_grant(
                    ScopeGrantModel(
                        scope_grant_id=new_id("event"),
                        subject_key=f"project-membership:{member.member_id}",
                        subject={
                            "type": "PROJECT_MEMBERSHIP",
                            "principal_id": member.principal_id,
                            "project_membership_id": member.member_id,
                            "organization_id": member.organization_id,
                            "project_id": member.project_id,
                        },
                        scope_key=structured_scope_key(grant_payload["scope"]),
                        scope=grant_payload["scope"],
                        effect=grant_payload["effect"],
                        capability_keys=grant_payload["capability_keys"],
                        capability_digest=digest_json(sorted(grant_payload["capability_keys"])),
                        ceiling_version=member.role_version,
                        inherit=grant_payload["inherit"],
                        valid_from=now,
                        valid_to=None,
                        status="ACTIVE",
                        etag=compute_etag(digest_json(grant_payload)),
                    )
                )
            elif operation.type == "REVOKE_SCOPE_GRANT":
                grant = await self.repo.get_scope_grant(operation.grant_id)
                if grant is None:
                    raise _domain_error(
                        NotFoundError, "SCOPE_GRANT_NOT_FOUND", "Scope grant not found"
                    )
                if grant.etag != operation.grant_etag:
                    raise _domain_error(
                        PreconditionFailedError, "ETAG_MISMATCH", "Grant ETag changed"
                    )
                grant.status = "REVOKED"
                grant.valid_to = now
            member.resource_version += 1
            member.etag = compute_etag(member.resource_version)
        # The final-state invariant is checked after all operations, inside this transaction.
        admin_count = await self.repo.active_admin_count(project_id)
        if admin_count == 0 and any(
            operation.type in {"CHANGE_ROLE", "DISABLE_MEMBERSHIP"}
            for operation in request.operations
        ):
            raise _domain_error(
                VersionConflictError, "LAST_ADMIN_REQUIRED", "Project must retain an active admin"
            )
        return {
            "result_id": new_id("event"),
            "request_id": new_id("event"),
            "committed_at": now.isoformat(),
            "project_policy_revision": preflight.policy_revision,
            "project_policy_etag": compute_etag(
                digest_json(
                    {
                        "project_id": project_id,
                        "operation_hash": preflight.operation_hash,
                        "at": now.isoformat(),
                    }
                )
            ),
            "affected_membership_ids": sorted(set(affected)),
            "_audit_events": [
                {
                    "CHANGE_ROLE": "access.membership.role_changed",
                    "ADD_SCOPE_GRANT": "access.scope_grant.added",
                    "REVOKE_SCOPE_GRANT": "access.scope_grant.revoked",
                    "DISABLE_MEMBERSHIP": "access.membership.disabled",
                    "ENABLE_MEMBERSHIP": "access.membership.enabled",
                }[operation.type]
                for operation in request.operations
            ],
        }

    async def preflight_invitation(
        self, project_id: str, actor_principal_id: str, request: InvitationPreflightRequest
    ) -> dict[str, Any]:
        payload = request.model_dump(mode="json")
        token = secrets.token_urlsafe(48)
        now = utc_now()
        preflight_id = new_id("event")
        operation_hash = digest_json(payload["operation"])
        self.repo.add_preflight(
            AccessPreflightModel(
                preflight_id=preflight_id,
                project_id=project_id,
                actor_principal_id=actor_principal_id,
                operation_hash=operation_hash,
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                payload={"kind": "INVITATION", **payload},
                policy_revision=request.project_policy_revision,
                policy_etag=request.project_policy_etag,
                expires_at=now + timedelta(minutes=5),
            )
        )
        operation = payload["operation"]
        target_display = "existing invitation"
        if operation["type"] == "CREATE_INVITATION":
            target = operation["target"]
            target_display = target.get("value") or target.get("subject")
        return {
            "preflight_id": preflight_id,
            "preflight_token": token,
            "preflight_expires_at": (now + timedelta(minutes=5)).isoformat(),
            "actor_principal_id": actor_principal_id,
            "project_scope": operation.get(
                "intended_scope",
                {"type": "PROJECT", "organization_id": "unknown", "project_id": project_id},
            ),
            "project_policy_revision": request.project_policy_revision,
            "project_policy_etag": request.project_policy_etag,
            "normalized_operation_hash": operation_hash,
            "reason_hash": hashlib.sha256(request.reason.encode()).hexdigest(),
            "idempotency_key_hash": "0" * 64,
            "step_up_result_id": None,
            "risk": "HIGH",
            "warnings": [],
            "operation": operation["type"],
            "review": {
                "operation": operation["type"],
                "target_hash": hashlib.sha256(target_display.encode()).hexdigest(),
                "target_display": target_display,
                "reason_display": request.reason,
                **{key: value for key, value in operation.items() if key != "target"},
            },
        }

    async def commit_invitation(self, project_id: str, token: str) -> InvitationModel:
        now = utc_now()
        preflight = await self.repo.consume_preflight(
            hashlib.sha256(token.encode()).hexdigest(), now
        )
        if (
            preflight is None
            or preflight.project_id != project_id
            or preflight.payload.get("kind") != "INVITATION"
        ):
            raise _domain_error(GoneError, "PREFLIGHT_INVALID", "Invitation preflight is invalid")
        operation = preflight.payload["operation"]
        if operation["type"] == "CREATE_INVITATION":
            target = operation["target"]
            target_display = target.get("value") or target.get("subject")
            invitation = InvitationModel(
                invitation_id=new_id("event"),
                organization_id=operation["intended_scope"]["organization_id"],
                project_id=project_id,
                target_hash=hashlib.sha256(target_display.encode()).hexdigest(),
                target_display=target_display,
                status="PENDING",
                intended_role_id=operation["role_id"],
                intended_role_version=operation["role_version"],
                intended_scope=operation["intended_scope"],
                policy_revision=preflight.policy_revision,
                expires_at=datetime.fromisoformat(
                    operation["invitation_expires_at"].replace("Z", "+00:00")
                ),
                superseded_by_invitation_id=None,
                etag=compute_etag(
                    digest_json({"operation_hash": preflight.operation_hash, "at": now.isoformat()})
                ),
                created_at=now,
            )
            self.repo.add_invitation(invitation)
            return invitation
        invitation = await self.repo.get_invitation(project_id, operation["invitation_id"])
        if invitation is None:
            raise _domain_error(NotFoundError, "INVITATION_NOT_FOUND", "Invitation not found")
        if invitation.etag != operation["invitation_etag"]:
            raise _domain_error(PreconditionFailedError, "ETAG_MISMATCH", "Invitation ETag changed")
        if operation["type"] == "REVOKE_INVITATION":
            invitation.status = "REVOKED"
            invitation.etag = compute_etag(
                digest_json({"invitation_id": invitation.invitation_id, "status": "REVOKED"})
            )
            return invitation
        replacement = InvitationModel(
            invitation_id=new_id("event"),
            organization_id=invitation.organization_id,
            project_id=invitation.project_id,
            target_hash=invitation.target_hash,
            target_display=invitation.target_display,
            status="PENDING",
            intended_role_id=invitation.intended_role_id,
            intended_role_version=invitation.intended_role_version,
            intended_scope=invitation.intended_scope,
            policy_revision=preflight.policy_revision,
            expires_at=now + timedelta(days=7),
            superseded_by_invitation_id=None,
            etag=compute_etag(
                digest_json(
                    {"invitation_id": invitation.invitation_id, "replacement": now.isoformat()}
                )
            ),
            created_at=now,
        )
        invitation.status = "REVOKED"
        invitation.superseded_by_invitation_id = replacement.invitation_id
        self.repo.add_invitation(replacement)
        return replacement


class AuditService:
    def __init__(
        self, session: AsyncSession, integrity_strategy: IntegrityStrategy | None = None
    ) -> None:
        self.session = session
        self.repo = AuditRepository(session)
        self.integrity = integrity_strategy or Sha256ChainIntegrityStrategy()

    async def append_event(
        self,
        event_input: AuditEventInput,
        *,
        aggregate_type: str,
        aggregate_id: str,
        retention_class: str = "STANDARD",
        retain_until: datetime | None = None,
        previous_digest: str | None = None,
    ) -> AuditEventModel:
        """Trusted internal append path; intentionally not exposed by ``router``."""

        validate_event_name(event_input.event_name)
        payload = event_input.model_dump(mode="json")
        validate_safe_audit_value(payload.get("change"), "change")
        event_id = new_id("event")
        now = utc_now()
        digest = self.integrity.digest(payload, previous_digest)
        sequence = await self.repo.next_ingest_sequence()
        scope = event_input.scope
        scope_key = project_scope_key(scope.organization_id, scope.project_id, scope.region_code)
        row = AuditEventModel(
            event_id=event_id,
            scope_key=scope_key,
            organization_id=scope.organization_id,
            project_id=scope.project_id,
            region_code=scope.region_code,
            event_name=event_input.event_name,
            occurred_at=event_input.occurred_at,
            recorded_at=now,
            ingest_sequence=sequence,
            actor_principal_id=event_input.actor.principal_id,
            resource_type=event_input.resource.type,
            resource_id=event_input.resource.id,
            request_id=event_input.request.request_id,
            producer_service=event_input.producer.service,
            producer_event_id=event_input.producer.producer_event_id,
            input_payload=payload,
            catalog_version=EVENT_CATALOG_VERSION,
            outcome_status=event_input.outcome.status,
            risk_level=self._risk_level(event_input.risk_signal_codes),
            risk_payload={
                "signal_codes": event_input.risk_signal_codes,
                "policy_version": POLICY_VERSION,
            },
            retention_class=retention_class,
            retention_policy_version=RETENTION_POLICY_VERSION,
            retain_until=retain_until,
            legal_hold=retention_class == "LEGAL_HOLD",
            integrity_version=self.integrity.version,
            record_digest=digest,
            checkpoint_id=None,
        )
        outbox = AuditOutboxModel(
            outbox_id=new_id("outbox"),
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            producer_service=event_input.producer.service,
            producer_event_id=event_input.producer.producer_event_id,
            payload=payload,
            payload_digest=digest_json(payload),
            created_at=now,
            relay_status="PENDING",
            attempt_count=0,
            next_attempt_at=now,
            last_error_code=None,
        )
        self.repo.add_event(row)
        self.repo.add_outbox(outbox)
        return row

    @staticmethod
    def _risk_level(signals: Sequence[str]) -> str:
        values = {signal.upper() for signal in signals}
        if values & {"BREAK_GLASS", "CROSS_SCOPE", "INTEGRITY_FAILURE"}:
            return "CRITICAL"
        if values & {"PRIVILEGE_CHANGE", "EXPORT", "LEGAL_HOLD"}:
            return "HIGH"
        if values:
            return "MEDIUM"
        return "LOW"

    async def get_projected_event(
        self, scope: AuditScope, event_id: str, capabilities: set[str]
    ) -> dict[str, Any]:
        row = await self.repo.get_event(
            project_scope_key(scope.organization_id, scope.project_id, scope.region_code), event_id
        )
        if row is None:
            raise _domain_error(NotFoundError, "AUDIT_EVENT_NOT_FOUND", "Audit event not found")
        return project_audit_record(row, capabilities)

    async def preflight_export(
        self, scope: AuditScope, request: AuditExportPreflightRequest
    ) -> dict[str, Any]:
        normalized = request.filters.model_dump(mode="json")
        if normalized["sort"] != AUDIT_SORT:
            raise validation_error(
                "AUDIT_SORT_UNSUPPORTED", "Unsupported audit ordering", "filters.sort"
            )
        now = utc_now()
        token = secrets.token_urlsafe(48)
        return {
            "preflight_id": new_id("event"),
            "preflight_token": token,
            "preflight_expires_at": (now + timedelta(minutes=5)).isoformat(),
            "snapshot_at": now.isoformat(),
            "normalized_filters": normalized,
            "normalized_filter_hash": digest_json(normalized),
            "estimated_event_count": "0",
            "allowed_formats": ["CSV", "JSONL"],
            "allowed_field_profiles": ["STANDARD", "SECURITY_REVIEW"],
            "selected_format": request.format,
            "selected_field_profile": request.field_profile,
            "redaction_policy_version": REDACTION_POLICY_VERSION,
            "omitted_field_classes": ["credentials", "network_exact", "raw_payload"],
            "integrity": {"status": "UNKNOWN", "checkpoint_id": None},
            "reason_display": request.reason,
            "warnings": ["Integrity strategy awaits joint security/legal approval"],
            "blocked_reasons": [],
        }

    async def create_export(
        self,
        scope: AuditScope,
        requester_principal_id: str,
        command: AuditExportCreateCommand,
    ) -> AuditExportJobModel:
        normalized = command.normalized_filters.model_dump(mode="json")
        now = utc_now()
        export_id = new_id("event")
        row = AuditExportJobModel(
            export_id=export_id,
            scope_key=project_scope_key(scope.organization_id, scope.project_id, scope.region_code),
            scope=scope.model_dump(mode="json"),
            requester_principal_id=requester_principal_id,
            snapshot_at=command.snapshot_at,
            normalized_filters=normalized,
            normalized_filter_hash=digest_json(normalized),
            format=command.format,
            field_profile=command.field_profile,
            job_id=new_id("job"),
            status="QUEUED",
            succeeded_count=0,
            failed_count=0,
            omitted_count=0,
            artifact_ref=None,
            artifact_expires_at=None,
            artifact_sha256=None,
            create_authorization_policy_version=POLICY_VERSION,
            created_at=now,
        )
        self.repo.add_export(row)
        return row

    async def authorize_export_download(
        self, scope: AuditScope, export_id: str, *, can_download: bool
    ) -> dict[str, Any]:
        if not can_download:
            raise _domain_error(
                ForbiddenError, "DOWNLOAD_FORBIDDEN", "Current policy denies download"
            )
        row = await self.repo.get_export(
            project_scope_key(scope.organization_id, scope.project_id, scope.region_code), export_id
        )
        if row is None:
            raise _domain_error(NotFoundError, "AUDIT_EXPORT_NOT_FOUND", "Audit export not found")
        if row.status != "SUCCEEDED" or row.artifact_ref is None:
            raise _domain_error(
                VersionConflictError, "AUDIT_EXPORT_NOT_READY", "Export is not ready"
            )
        if row.artifact_expires_at is not None and as_utc(row.artifact_expires_at) <= utc_now():
            raise _domain_error(GoneError, "AUDIT_EXPORT_EXPIRED", "Export artifact expired")
        return {
            "grant_id": new_id("event"),
            "export_id": export_id,
            "expires_at": (utc_now() + timedelta(minutes=5)).isoformat(),
            "transport": "STREAM",
            "allowed_actions": ["DOWNLOAD"],
            "blocked_reasons": [],
        }
