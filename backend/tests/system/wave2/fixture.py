"""Isolation, rendering, evidence, and cleanup contracts for BE22.

The helpers in this module are intentionally independent from production app
composition.  They may only be used by tests and the explicit Wave 2 runner.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, cast

from hc_data_platform.ingest.models import RolloutManifestV1

DATA_ROOT = Path(__file__).resolve().parent / "data"
_RUN_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")
_SECRET_KEYS = (
    "authorization",
    "cookie",
    "password",
    "secret",
    "signed_url",
    "presigned_url",
    "token",
)
_SECRET_VALUES = (
    re.compile(r"(?i)^bearer\s+"),
    re.compile(r"^hcs_"),
    re.compile(r"(?i)(x-amz-signature|x-amz-credential)=", re.ASCII),
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class RunScope:
    run_id: str
    project_id: str
    foreign_project_id: str
    region_code: str
    admin_username: str
    contractor_username: str
    raw_prefix: str
    foreign_raw_prefix: str

    @classmethod
    def create(cls, run_id: str) -> RunScope:
        normalized = run_id.strip().lower()
        if _RUN_ID.fullmatch(normalized) is None:
            raise ValueError("run_id must be 1-32 lowercase letters, digits, or hyphens")
        project_id = f"be22-{normalized}-p1"
        foreign_project_id = f"be22-{normalized}-p2"
        return cls(
            run_id=normalized,
            project_id=project_id,
            foreign_project_id=foreign_project_id,
            region_code=f"be22-{normalized}-cn",
            admin_username=f"be22-{normalized}-admin",
            contractor_username=f"be22-{normalized}-contractor",
            raw_prefix=f"raw/v1/project={project_id}/",
            foreign_raw_prefix=f"raw/v1/project={foreign_project_id}/",
        )

    def assert_cleanup_safe(self) -> None:
        expected = RunScope.create(self.run_id)
        if self != expected:
            raise ValueError("cleanup scope does not match its deterministic BE22 namespace")
        for value in (self.project_id, self.foreign_project_id):
            if not value.startswith(f"be22-{self.run_id}-"):
                raise ValueError("cleanup is restricted to the exact BE22 run namespace")

    @property
    def object_prefixes(self) -> tuple[str, ...]:
        """All object-store namespaces the real Worker can create for this run."""

        projects = (self.project_id, self.foreign_project_id)
        return (
            self.raw_prefix,
            self.foreign_raw_prefix,
            *(f"lance/{project_id}/" for project_id in projects),
            *(f"lance/_attempts/{project_id}/" for project_id in projects),
        )


@dataclass(frozen=True, slots=True)
class PackageScenario:
    name: str
    payload_path: str
    payload_size: int
    payload_sha256: str
    manifest_path: str
    manifest_size: int
    manifest_sha256: str
    expected_preflight: str
    expected_condition: str
    duplicate_of: str | None = None

    @property
    def payload(self) -> bytes:
        return (DATA_ROOT / self.payload_path).read_bytes()

    @property
    def manifest_bytes(self) -> bytes:
        return (DATA_ROOT / self.manifest_path).read_bytes()

    def verify_bytes(self) -> None:
        for label, content, expected_size, expected_hash in (
            ("payload", self.payload, self.payload_size, self.payload_sha256),
            ("manifest", self.manifest_bytes, self.manifest_size, self.manifest_sha256),
        ):
            if len(content) != expected_size:
                raise ValueError(f"{self.name} {label} size drift")
            if hashlib.sha256(content).hexdigest() != expected_hash:
                raise ValueError(f"{self.name} {label} hash drift")


def load_catalog() -> tuple[PackageScenario, ...]:
    raw = cast(
        dict[str, Any],
        json.loads((DATA_ROOT / "catalog.json").read_text(encoding="utf-8")),
    )
    if raw.get("schema_version") != 1:
        raise ValueError("unsupported BE22 fixture catalog")
    return tuple(PackageScenario(**item) for item in raw["scenarios"])


def scenario(name: str) -> PackageScenario:
    try:
        return next(item for item in load_catalog() if item.name == name)
    except StopIteration as exc:
        raise KeyError(name) from exc


def render_manifest(
    package: PackageScenario,
    scope: RunScope,
    *,
    collection_task_id: str,
    sequence_no: int,
) -> dict[str, Any]:
    """Bind a checked-in template to one isolated run without changing payload bytes."""

    raw = cast(dict[str, Any], json.loads(package.manifest_bytes))
    suffix = (package.duplicate_of or package.name).replace("_", "-")
    raw.update(
        {
            "project_id": scope.project_id,
            "task_id": collection_task_id,
            "collection_job_id": f"{scope.run_id}-{suffix}-job",
            "rollout_id": f"{scope.run_id}-{suffix}-rollout",
            "collection_session_id": f"{scope.run_id}-{suffix}-session",
            "recording_request_id": f"{scope.run_id}-{suffix}-request",
            "data_package_id": f"{scope.run_id}-{suffix}-package",
            "sequence_no": sequence_no,
        }
    )
    return raw


def validated_manifest(
    package: PackageScenario,
    scope: RunScope,
    *,
    collection_task_id: str,
    sequence_no: int,
) -> RolloutManifestV1:
    return RolloutManifestV1.model_validate(
        render_manifest(
            package,
            scope,
            collection_task_id=collection_task_id,
            sequence_no=sequence_no,
        )
    )


class StageStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_RUN = "NOT RUN"


@dataclass(slots=True)
class StageEvidence:
    name: str
    status: StageStatus
    started_at: str
    finished_at: str
    resource_ids: dict[str, str] = field(default_factory=dict)
    request_ids: tuple[str, ...] = ()
    audit_event_ids: tuple[str, ...] = ()
    detail: str | None = None


def _redact(value: Any, *, key: str = "") -> Any:
    normalized = key.casefold().replace("-", "_")
    if any(marker in normalized for marker in _SECRET_KEYS):
        return "[REDACTED]"
    if isinstance(value, str):
        if any(pattern.search(value) for pattern in _SECRET_VALUES):
            return "[REDACTED]"
        return value
    if isinstance(value, dict):
        return {
            str(child_key): _redact(child, key=str(child_key)) for child_key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(child, key=key) for child in value]
    return value


@dataclass(slots=True)
class RunArtifact:
    run_id: str
    project_id: str
    foreign_project_id: str
    region_code: str
    browser_e2e_status: StageStatus = StageStatus.NOT_RUN
    stages: list[StageEvidence] = field(default_factory=list)
    cleanup: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: utc_now().isoformat())

    @classmethod
    def for_scope(cls, scope: RunScope) -> RunArtifact:
        return cls(
            run_id=scope.run_id,
            project_id=scope.project_id,
            foreign_project_id=scope.foreign_project_id,
            region_code=scope.region_code,
        )

    def add_stage(
        self,
        name: str,
        status: StageStatus,
        *,
        started_at: datetime,
        resource_ids: dict[str, str] | None = None,
        request_ids: tuple[str, ...] = (),
        audit_event_ids: tuple[str, ...] = (),
        detail: str | None = None,
    ) -> None:
        self.stages.append(
            StageEvidence(
                name=name,
                status=status,
                started_at=started_at.isoformat(),
                finished_at=utc_now().isoformat(),
                resource_ids=resource_ids or {},
                request_ids=request_ids,
                audit_event_ids=audit_event_ids,
                detail=detail,
            )
        )

    def safe_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _redact(asdict(self)))

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.safe_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


class CleanupBackend(Protocol):
    def delete_database_scope(self, scope: RunScope) -> dict[str, int]: ...

    def delete_object_prefix(self, prefix: str) -> int: ...


@dataclass(slots=True)
class CleanupController:
    backend: CleanupBackend

    def execute(self, scope: RunScope) -> list[dict[str, Any]]:
        """Run every cleanup target even after a failure, enabling a later retry."""

        scope.assert_cleanup_safe()
        results: list[dict[str, Any]] = []
        targets = [("database", lambda: self.backend.delete_database_scope(scope))]
        targets.extend(
            (prefix, lambda prefix=prefix: self.backend.delete_object_prefix(prefix))
            for prefix in scope.object_prefixes
        )
        for target, operation in targets:
            try:
                results.append({"target": target, "status": "CLEAN", "deleted": operation()})
            except Exception as exc:  # cleanup must continue and report the exact target
                results.append(
                    {
                        "target": target,
                        "status": "FAILED",
                        "error_type": type(exc).__name__,
                    }
                )
        return results
