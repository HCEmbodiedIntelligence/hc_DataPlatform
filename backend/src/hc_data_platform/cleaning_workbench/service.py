"""Application service for the P11 non-destructive cleaning workbench."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from hc_data_platform.core.errors import problem
from hc_data_platform.security.audit import canonical_hash
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.idempotency import IdempotencyStore, InMemoryIdempotencyStore
from hc_data_platform.security.scope import ScopeGuard

from .calculation import calculate_edl
from .models import (
    CleaningAsyncJob,
    CleaningDraftBootstrapData,
    CleaningDraftBootstrapEnvelope,
    CleaningOperation,
    CleaningOutputRevision,
    CleaningOutputVersion,
    CleaningWorkbenchAuditEvent,
    CleaningWorkbenchScope,
    CleaningWorkbenchState,
    CommitAcceptedEnvelope,
    CommitCleaningDraftCommand,
    CommitSucceeded,
    CreateCleaningPreviewCommand,
    PreviewAcceptedEnvelope,
    PreviewReady,
    ReviewFindingsData,
    ReviewFindingsEnvelope,
    SaveCleaningEdlCommand,
    SaveCleaningEdlData,
    SaveCleaningEdlEnvelope,
    ViewerManifest,
)
from .repository import (
    CleaningWorkbenchMutationConflict,
    CleaningWorkbenchPreconditionError,
    CleaningWorkbenchRepository,
    CleaningWorkbenchSourceIncomplete,
    InMemoryCleaningWorkbenchRepository,
)

Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class SaveCleaningEdlOutcome:
    envelope: SaveCleaningEdlEnvelope
    replayed: bool


@dataclass(frozen=True, slots=True)
class PreviewOutcome:
    envelope: PreviewAcceptedEnvelope
    replayed: bool


@dataclass(frozen=True, slots=True)
class CommitOutcome:
    envelope: CommitAcceptedEnvelope
    replayed: bool


class CleaningWorkbenchService:
    def __init__(
        self,
        repository: CleaningWorkbenchRepository,
        *,
        idempotency: IdempotencyStore | None = None,
        clock: Clock = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._repository = repository
        self._idempotency = idempotency or InMemoryIdempotencyStore()
        self._clock = clock

    @classmethod
    def in_memory(cls) -> CleaningWorkbenchService:
        return cls(InMemoryCleaningWorkbenchRepository())

    def bootstrap(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        draft_id: str,
        request_id: str,
    ) -> CleaningDraftBootstrapEnvelope:
        scope = self._authorize(auth, organization_id, project_id, region_code, "cleaning.read")
        state = self._required_state(scope=scope, draft_id=draft_id)
        presented = self._present(state=state, auth=auth)
        self._audit(
            auth=auth,
            scope=scope,
            action="cleaning.draft.viewed",
            resource_id=draft_id,
            request_id=request_id,
            details={"edl_revision": presented.edl.edl_revision},
        )
        return self._bootstrap_envelope(state=presented, request_id=request_id)

    def save_edl(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        draft_id: str,
        if_match: str,
        command: SaveCleaningEdlCommand,
        request_id: str,
    ) -> SaveCleaningEdlOutcome:
        scope = self._authorize(auth, organization_id, project_id, region_code, "cleaning.edit")
        current = self._required_state(scope=scope, draft_id=draft_id)
        # The repository checks the precondition after looking up the durable
        # client mutation ID, so an exact network replay remains replayable even
        # though its original If-Match is now stale.
        _validate_operation_shape(command.operations)
        now = self._clock()
        from .calculation import operation_hash

        signature = operation_hash(command.operations)
        validation, _summary, _mapping = calculate_edl(
            operations=command.operations,
            streams=current.streams,
            edl_revision=int(current.edl.edl_revision) + 1,
            operation_signature=signature,
            calculated_at=now,
        )
        if validation.status != "PASSED":
            raise problem(
                status=422,
                code="CLEANING_EDL_INVALID",
                title="EDL validation failed",
                detail="Fix all blocking EDL validation issues before saving.",
                details={"issues": [item.model_dump(mode="json") for item in validation.issues]},
            )
        audit = self._audit_event(
            auth=auth,
            scope=scope,
            action="cleaning.draft.updated",
            resource_id=draft_id,
            request_id=request_id,
            occurred_at=now,
            before=current.edl.model_dump(mode="json"),
            after={"operation_hash": signature, "operation_count": len(command.operations)},
            details={"client_mutation_id": command.client_mutation_id},
        )
        try:
            outcome = self._repository.save_edl(
                scope=scope,
                draft_id=draft_id,
                expected_etag=if_match,
                expected_edl_revision=int(command.expected_edl_revision),
                expected_operation_hash=command.expected_operation_hash,
                client_mutation_id=command.client_mutation_id,
                edl_operations=command.operations,
                actor_id=auth.subject_id,
                occurred_at=now,
                audit_event=audit,
            )
        except CleaningWorkbenchPreconditionError as exc:
            raise _precondition_failed() from exc
        except CleaningWorkbenchMutationConflict as exc:
            raise _mutation_conflict() from exc
        state = self._present(state=outcome.state, auth=auth)
        return SaveCleaningEdlOutcome(
            envelope=SaveCleaningEdlEnvelope(
                data=SaveCleaningEdlData(draft=state.draft, edl=state.edl, request_id=request_id),
                scope=scope,
                request_id=request_id,
            ),
            replayed=outcome.replayed,
        )

    def create_preview(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        draft_id: str,
        if_match: str,
        idempotency_key: str,
        command: CreateCleaningPreviewCommand,
        request_id: str,
    ) -> PreviewOutcome:
        scope = self._authorize(auth, organization_id, project_id, region_code, "cleaning.preview")
        payload = {
            "command": command.model_dump(mode="json"),
            "if_match": if_match,
            "draft_id": draft_id,
        }

        def action() -> PreviewAcceptedEnvelope:
            current = self._required_state(scope=scope, draft_id=draft_id)
            self._ensure_editable(current=current, if_match=if_match)
            if (
                command.base_revision_id != current.base.revision_id
                or command.edl_revision != current.edl.edl_revision
                or command.operation_hash != current.edl.operation_hash
                or current.edl.validation.status != "PASSED"
            ):
                raise _precondition_failed()
            now = self._clock()
            preview_id = _stable_id("preview", scope, draft_id, idempotency_key)
            job_id = _stable_id("job", scope, f"preview:{draft_id}", idempotency_key)
            validation, summary, mapping = calculate_edl(
                operations=current.edl.operations,
                streams=current.streams,
                edl_revision=int(current.edl.edl_revision),
                operation_signature=current.edl.operation_hash,
                calculated_at=now,
            )
            if validation.status != "PASSED" or mapping is None:
                raise problem(
                    status=422,
                    code="CLEANING_PREVIEW_BLOCKED",
                    title="Preview is blocked",
                    detail="The saved EDL has blocking validation issues.",
                )
            expires_at = now + timedelta(minutes=30)
            manifest_hash = canonical_hash(
                {"preview_id": preview_id, "operation_hash": current.edl.operation_hash}
            )
            preview = PreviewReady(
                preview_id=preview_id,
                draft_id=draft_id,
                base_revision_id=current.base.revision_id,
                edl_revision=current.edl.edl_revision,
                operation_hash=current.edl.operation_hash,
                job_id=job_id,
                created_at=now,
                viewer_manifest=ViewerManifest(
                    manifest_id=_stable_id("manifest", scope, draft_id, current.edl.operation_hash),
                    manifest_hash=f"sha256:{manifest_hash}",
                    expires_at=expires_at,
                    streams=tuple(stream.stream_id for stream in current.streams),
                ),
                source_to_output_map=mapping,
                validation=validation,
                summary=summary,
                expires_at=expires_at,
            )
            job = _job(
                scope=scope,
                job_id=job_id,
                kind="CLEANING_PREVIEW",
                resource_id=draft_id,
                etag=current.draft.etag,
                now=now,
                stage="READY",
                result_ref={"draft_id": draft_id, "preview_id": preview_id},
            )
            audit = self._audit_event(
                auth=auth,
                scope=scope,
                action="cleaning.preview.completed",
                resource_id=draft_id,
                request_id=request_id,
                occurred_at=now,
                before=None,
                after=preview.model_dump(mode="json"),
                details={"preview_id": preview_id, "edl_revision": current.edl.edl_revision},
            )
            try:
                self._repository.create_preview(
                    scope=scope,
                    draft_id=draft_id,
                    expected_etag=if_match,
                    preview=preview,
                    job=job,
                    audit_event=audit,
                )
            except CleaningWorkbenchPreconditionError as exc:
                raise _precondition_failed() from exc
            return PreviewAcceptedEnvelope(
                preview=preview, job=job, scope=scope, request_id=request_id
            )

        result = self._idempotency.execute(
            scope=project_id,
            key=f"cleaning-preview:{draft_id}:{idempotency_key}",
            payload=payload,
            action=action,
        )
        return PreviewOutcome(envelope=result.value, replayed=result.replayed)

    def commit(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        draft_id: str,
        if_match: str,
        idempotency_key: str,
        command: CommitCleaningDraftCommand,
        request_id: str,
    ) -> CommitOutcome:
        scope = self._authorize(auth, organization_id, project_id, region_code, "cleaning.submit")
        payload = {
            "command": command.model_dump(mode="json"),
            "if_match": if_match,
            "draft_id": draft_id,
        }

        def action() -> CommitAcceptedEnvelope:
            current = self._required_state(scope=scope, draft_id=draft_id)
            self._ensure_editable(current=current, if_match=if_match)
            preview = current.active_preview
            if (
                preview is None
                or preview.status != "READY"
                or preview.preview_id != command.preview_id
                or preview.base_revision_id != command.base_revision_id
                or preview.edl_revision != command.edl_revision
                or preview.operation_hash != command.operation_hash
                or preview.expires_at <= self._clock()
                or current.edl.validation.status != "PASSED"
            ):
                raise _precondition_failed()
            composition = current.successor_composition
            expected_composition = None if composition is None else composition.composition_hash
            if command.successor_composition_hash != expected_composition:
                raise problem(
                    status=409,
                    code="SUCCESSOR_COMPOSITION_STALE",
                    title="Review successor composition changed",
                    detail="Reload the returned Draft before committing its successor composition.",
                )
            now = self._clock()
            commit_id = _stable_id("commit", scope, draft_id, idempotency_key)
            output_version_id = _stable_id("version", scope, f"output:{draft_id}", idempotency_key)
            try:
                members = self._repository.output_members(state=current, commit_id=commit_id)
            except CleaningWorkbenchSourceIncomplete as exc:
                raise _source_incomplete() from exc
            output_revisions = tuple(
                CleaningOutputRevision(
                    revision_id=member.revision_id,
                    ordinal=index,
                    episode_id=member.episode_id,
                    episode_stream_ids=member.episode_stream_ids,
                    source_revision_id=member.source_revision_id,
                    member_mode=member.member_mode,
                )
                for index, member in enumerate(members)
            )
            job_id = _stable_id("job", scope, f"commit:{draft_id}", idempotency_key)
            output_version = CleaningOutputVersion(
                version_id=output_version_id,
                status="REVIEWING",
                draft_id=draft_id,
                commit_id=commit_id,
            )
            commit = CommitSucceeded(
                commit_id=commit_id,
                draft_id=draft_id,
                preview_id=command.preview_id,
                job_id=job_id,
                created_at=now,
                completed_at=now,
                output_revisions=output_revisions,
                output_version=output_version,
                materialization_status="NOT_STARTED",
                operation_hash=current.edl.operation_hash,
                successor_composition_hash=expected_composition,
            )
            job = _job(
                scope=scope,
                job_id=job_id,
                kind="CLEANING_COMMIT",
                resource_id=draft_id,
                etag=current.draft.etag,
                now=now,
                stage="LOGICAL_COMMITTED",
                result_ref={
                    "draft_id": draft_id,
                    "commit_id": commit_id,
                    "output_version_id": output_version_id,
                },
            )
            audit = self._audit_event(
                auth=auth,
                scope=scope,
                action="cleaning.submit.completed",
                resource_id=draft_id,
                request_id=request_id,
                occurred_at=now,
                before=current.draft.model_dump(mode="json"),
                after=commit.model_dump(mode="json"),
                details={"commit_id": commit_id, "output_version_id": output_version_id},
            )
            try:
                self._repository.commit(
                    scope=scope,
                    draft_id=draft_id,
                    expected_etag=if_match,
                    commit=commit,
                    job=job,
                    output_members=members,
                    audit_event=audit,
                )
            except CleaningWorkbenchPreconditionError as exc:
                raise _precondition_failed() from exc
            except CleaningWorkbenchSourceIncomplete as exc:
                raise _source_incomplete() from exc
            return CommitAcceptedEnvelope(
                commit=commit, job=job, scope=scope, request_id=request_id
            )

        result = self._idempotency.execute(
            scope=project_id,
            key=f"cleaning-commit:{draft_id}:{idempotency_key}",
            payload=payload,
            action=action,
        )
        return CommitOutcome(envelope=result.value, replayed=result.replayed)

    def review_findings(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        draft_id: str,
        request_id: str,
    ) -> ReviewFindingsEnvelope:
        scope = self._authorize(auth, organization_id, project_id, region_code, "cleaning.read")
        state = self._required_state(scope=scope, draft_id=draft_id)
        if state.review_feedback is None:
            raise problem(
                status=404,
                code="CLEANING_REVIEW_FEEDBACK_NOT_FOUND",
                title="Review feedback not found",
                detail="This Draft has no immutable P07 Return feedback.",
            )
        self._audit(
            auth=auth,
            scope=scope,
            action="dataset.version_review_findings_viewed",
            resource_id=draft_id,
            request_id=request_id,
            details={"finding_count": len(state.review_feedback.findings)},
        )
        return ReviewFindingsEnvelope(
            data=ReviewFindingsData(feedback=state.review_feedback),
            scope=scope,
            request_id=request_id,
        )

    def _authorize(
        self,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        capability: str,
    ) -> CleaningWorkbenchScope:
        ScopeGuard.require(auth, project_id, region_code)
        auth.require_capability(capability, project_id)
        if not self._repository.has_organization_project(
            organization_id=organization_id, project_id=project_id
        ):
            raise problem(
                status=403,
                code="ORGANIZATION_SCOPE_DENIED",
                title="Organization access denied",
                detail="The selected organization is not bound to this project.",
            )
        return CleaningWorkbenchScope(
            organization_id=organization_id, project_id=project_id, region_code=region_code
        )

    def _required_state(
        self, *, scope: CleaningWorkbenchScope, draft_id: str
    ) -> CleaningWorkbenchState:
        state = self._repository.get_state(
            scope=scope,
            draft_id=draft_id,
            now=self._clock(),
        )
        if state is None:
            raise problem(
                status=404,
                code="CLEANING_DRAFT_NOT_FOUND",
                title="CleaningDraft not found",
                detail="The CleaningDraft does not exist in the selected scope.",
            )
        return state

    @staticmethod
    def _ensure_editable(*, current: CleaningWorkbenchState, if_match: str) -> None:
        if current.draft.status != "EDITING" or current.draft.etag != if_match:
            raise _precondition_failed()

    def _present(
        self, *, state: CleaningWorkbenchState, auth: AuthContext
    ) -> CleaningWorkbenchState:
        actions: list[str] = ["VIEW"]
        project_id = state.scope.project_id
        if state.draft.status == "EDITING":
            if auth.has_capability("cleaning.edit", project_id):
                actions.append("SAVE_EDL")
            if (
                auth.has_capability("cleaning.preview", project_id)
                and state.edl.validation.status == "PASSED"
            ):
                actions.append("CREATE_PREVIEW")
            preview = state.active_preview
            if (
                auth.has_capability("cleaning.submit", project_id)
                and preview is not None
                and preview.status == "READY"
                and preview.edl_revision == state.edl.edl_revision
                and preview.operation_hash == state.edl.operation_hash
                and preview.expires_at > self._clock()
                and state.edl.validation.status == "PASSED"
            ):
                actions.append("COMMIT")
        if state.active_commit is not None and state.active_commit.status == "SUCCEEDED":
            actions.append("OPEN_REVIEW")
        if state.review_feedback is not None:
            actions.append("OPEN_SUCCESSOR")
        draft = state.draft.model_copy(update={"allowed_actions": tuple(actions)})
        return state.model_copy(update={"draft": draft})

    @staticmethod
    def _bootstrap_envelope(
        *, state: CleaningWorkbenchState, request_id: str
    ) -> CleaningDraftBootstrapEnvelope:
        return CleaningDraftBootstrapEnvelope(
            data=CleaningDraftBootstrapData(
                draft=state.draft,
                origin=state.draft.origin,
                base=state.base,
                streams=state.streams,
                edl=state.edl,
                successor_composition=state.successor_composition,
                active_preview=state.active_preview,
                active_commit=state.active_commit,
                review_feedback=state.review_feedback,
                allowed_actions=state.draft.allowed_actions,
            ),
            scope=state.scope,
            request_id=request_id,
        )

    def _audit(
        self,
        *,
        auth: AuthContext,
        scope: CleaningWorkbenchScope,
        action: str,
        resource_id: str,
        request_id: str,
        details: dict[str, object] | None,
    ) -> None:
        self._repository.append_audit(
            self._audit_event(
                auth=auth,
                scope=scope,
                action=action,
                resource_id=resource_id,
                request_id=request_id,
                occurred_at=self._clock(),
                before=None,
                after=None,
                details=details,
            )
        )

    @staticmethod
    def _audit_event(
        *,
        auth: AuthContext,
        scope: CleaningWorkbenchScope,
        action: str,
        resource_id: str,
        request_id: str,
        occurred_at: datetime,
        before: object | None,
        after: object | None,
        details: dict[str, object] | None,
    ) -> CleaningWorkbenchAuditEvent:
        return CleaningWorkbenchAuditEvent(
            project_id=scope.project_id,
            region_code=scope.region_code,
            actor_id=auth.subject_id,
            action=action,
            resource_id=resource_id,
            request_id=request_id,
            occurred_at=occurred_at,
            before_hash=None if before is None else canonical_hash(before),
            after_hash=None if after is None else canonical_hash(after),
            details=details,
        )


def _validate_operation_shape(operations: tuple[CleaningOperation, ...]) -> None:
    ids: set[str] = set()
    for index, operation in enumerate(operations):
        if operation.sequence_no != index or operation.id in ids:
            raise problem(
                status=422,
                code="CLEANING_EDL_SEQUENCE_INVALID",
                title="EDL operation sequence is invalid",
                detail="Operation identifiers must be unique and sequence_no must be contiguous.",
            )
        ids.add(operation.id)
        if (
            hasattr(operation, "start_ns")
            and hasattr(operation, "end_ns")
            and int(operation.start_ns) >= int(operation.end_ns)
        ):
            raise problem(
                status=422,
                code="CLEANING_EDL_RANGE_INVALID",
                title="EDL range is invalid",
                detail="Every range must use non-empty half-open [start,end) semantics.",
            )


def _stable_id(prefix: str, scope: CleaningWorkbenchScope, resource: str, key: str) -> str:
    value = uuid5(
        NAMESPACE_URL,
        f"p11:{prefix}:{scope.organization_id}:{scope.project_id}:{scope.region_code}:{resource}:{key}",
    ).hex
    return f"{prefix}_{value}"


def _job(
    *,
    scope: CleaningWorkbenchScope,
    job_id: str,
    kind: Literal["CLEANING_PREVIEW", "CLEANING_COMMIT"],
    resource_id: str,
    etag: str,
    now: datetime,
    stage: str,
    result_ref: dict[str, str],
) -> CleaningAsyncJob:
    return CleaningAsyncJob(
        job_id=job_id,
        kind=kind,
        status="SUCCEEDED",
        stage=stage,
        progress=None,
        result_ref=result_ref,
        error=None,
        scope=scope,
        resource_ref={"resource_type": "CLEANING_DRAFT", "resource_id": resource_id, "etag": etag},
        created_at=now,
        started_at=now,
        finished_at=now,
        updated_at=now,
        expires_at=None,
        etag=f'"{job_id}:v1"',
        cancellable=False,
        retry_of_job_id=None,
    )


def _precondition_failed() -> Exception:
    return problem(
        status=412,
        code="CLEANING_DRAFT_PRECONDITION_FAILED",
        title="CleaningDraft changed",
        detail="Reload the server Draft before saving, previewing, or committing.",
    )


def _mutation_conflict() -> Exception:
    return problem(
        status=409,
        code="CLEANING_CLIENT_MUTATION_REUSED",
        title="Client mutation ID reused",
        detail="The client mutation ID was already used for different EDL content.",
    )


def _source_incomplete() -> Exception:
    return problem(
        status=422,
        code="CLEANING_SOURCE_INCOMPLETE",
        title="Source version is incomplete",
        detail="The immutable source lacks the durable projections required for a safe commit.",
    )
