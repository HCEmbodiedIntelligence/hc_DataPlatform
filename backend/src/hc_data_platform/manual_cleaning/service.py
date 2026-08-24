"""Application service for the P09 ManualIssue aggregate."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from hc_data_platform.core.errors import problem
from hc_data_platform.core.pagination import CursorCodec
from hc_data_platform.security.audit import canonical_hash
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.idempotency import IdempotencyStore, InMemoryIdempotencyStore
from hc_data_platform.security.scope import ScopeGuard
from hc_data_platform.security.versioning import ResourceVersion

from .models import (
    CleaningDraftId,
    CreateDraftFromIssueCommand,
    CreateManualIssueCommand,
    ManualCleaningDraftRecord,
    ManualIssueAuditEvent,
    ManualIssueBlockedReason,
    ManualIssueCounts,
    ManualIssueCreateDraftEnvelope,
    ManualIssueDetailEnvelope,
    ManualIssueDraftCandidate,
    ManualIssueDraftContext,
    ManualIssueDraftCreated,
    ManualIssueDraftMutationRecord,
    ManualIssueDraftRef,
    ManualIssueDraftResult,
    ManualIssueDraftSelectionRequired,
    ManualIssueFacets,
    ManualIssueListEnvelope,
    ManualIssueListItem,
    ManualIssueMutationRecord,
    ManualIssuePageData,
    ManualIssuePageEnvelope,
    ManualIssuePageInfo,
    ManualIssuePrincipal,
    ManualIssueRecord,
    ManualIssueResolutionVersion,
    ManualIssueScope,
    ManualIssueSourceFacts,
    ResolveManualIssueCommand,
    TriageManualIssueCommand,
)
from .repository import (
    InMemoryManualIssueRepository,
    ManualIssueDuplicateError,
    ManualIssueFilters,
    ManualIssuePreconditionError,
    ManualIssueRepository,
)

Clock = Callable[[], datetime]
ManualIssueSort = Literal[
    "updated_at:desc,id:desc",
    "updated_at:asc,id:asc",
    "severity:desc,updated_at:desc,id:desc",
    "created_at:desc,id:desc",
]


@dataclass(frozen=True, slots=True)
class ManualIssueMutationOutcome:
    record: ManualIssueMutationRecord
    replayed: bool


@dataclass(frozen=True, slots=True)
class ManualIssueDraftOutcome:
    record: ManualIssueDraftMutationRecord
    replayed: bool


class ManualIssueService:
    def __init__(
        self,
        repository: ManualIssueRepository,
        *,
        cursor_secret: str = "manual-issue-local-cursor-secret",
        idempotency: IdempotencyStore | None = None,
        clock: Clock = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._repository = repository
        self._cursor = CursorCodec(cursor_secret)
        self._idempotency = idempotency or InMemoryIdempotencyStore()
        self._clock = clock

    @classmethod
    def in_memory(cls) -> ManualIssueService:
        return cls(InMemoryManualIssueRepository())

    def list_issues(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        filters: ManualIssueFilters,
        sort: ManualIssueSort,
        after: str | None,
        before: str | None,
        limit: int,
        request_id: str,
    ) -> ManualIssueListEnvelope:
        scope = self._authorize_read(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        normalized = _validated_filters(filters)
        if after is not None and before is not None:
            raise problem(
                status=422,
                code="CURSOR_DIRECTION_CONFLICT",
                title="Invalid pagination request",
                detail="Use either after or before, not both.",
            )
        if limit not in {20, 50, 100}:
            raise problem(
                status=422,
                code="PAGE_LIMIT_INVALID",
                title="Invalid page size",
                detail="Page size must be one of 20, 50, or 100.",
            )
        records = self._sort(self._repository.list_issues(scope=scope, filters=normalized), sort)
        visible, page_info = self._page(
            records=records,
            scope=scope,
            filters=normalized,
            sort=sort,
            after=after,
            before=before,
            limit=limit,
        )
        now = self._clock()
        self._audit(
            auth=auth,
            scope=scope,
            action="manual_issue.listed",
            resource_id=project_id,
            request_id=request_id,
            details={"result_count": len(visible)},
        )
        return ManualIssueListEnvelope(
            items=tuple(self._list_item(record, auth=auth) for record in visible),
            page_info=page_info,
            snapshot_at=now,
            scope=scope,
            request_id=request_id,
        )

    def page(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        filters: ManualIssueFilters,
        request_id: str,
    ) -> ManualIssuePageEnvelope:
        scope = self._authorize_read(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        normalized = _validated_filters(filters)
        records = self._repository.list_issues(scope=scope, filters=normalized)
        counts = Counter(record.status for record in records)
        types = tuple(sorted({record.issue_type for record in records}))
        severities = tuple(
            severity
            for severity in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
            if any(record.severity == severity for record in records)
        )
        statuses = tuple(
            status
            for status in ("OPEN", "IN_PROGRESS", "RESOLVED")
            if any(record.status == status for record in records)
        )
        assignees = tuple(
            sorted(
                {record.assignee for record in records if record.assignee is not None},
                key=lambda item: (item.display_name.casefold(), item.id),
            )
        )
        now = self._clock()
        self._audit(
            auth=auth,
            scope=scope,
            action="manual_issue.page_viewed",
            resource_id=project_id,
            request_id=request_id,
            details={"result_count": len(records)},
        )
        return ManualIssuePageEnvelope(
            data=ManualIssuePageData(
                counts=ManualIssueCounts(
                    total=str(len(records)),
                    open=str(counts["OPEN"]),
                    in_progress=str(counts["IN_PROGRESS"]),
                    resolved=str(counts["RESOLVED"]),
                ),
                facets=ManualIssueFacets(
                    issue_types=types,
                    severities=severities,
                    statuses=statuses,
                    assignees=assignees,
                ),
                allowed_actions=self._page_actions(auth=auth, project_id=project_id),
                snapshot_at=now,
            ),
            scope=scope,
            request_id=request_id,
        )

    def get_issue(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        issue_id: str,
        request_id: str,
    ) -> ManualIssueRecord:
        scope = self._authorize_read(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        record = self._required_issue(scope=scope, issue_id=issue_id)
        self._audit(
            auth=auth,
            scope=scope,
            action="manual_issue.viewed",
            resource_id=issue_id,
            request_id=request_id,
        )
        return self._present(record, auth=auth)

    def create_issue(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        command: CreateManualIssueCommand,
        idempotency_key: str,
        request_id: str,
    ) -> ManualIssueMutationOutcome:
        scope = self._authorize_create(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        payload = command.model_dump(mode="json")

        def action() -> ManualIssueMutationRecord:
            source = self._repository.source_facts(
                scope=scope,
                version_id=command.origin_dataset_version_id,
                episode_id=command.episode_id,
                revision_id=command.episode_revision_id,
                stream_id=command.episode_stream_id,
            )
            if source is None or not _range_within_source(command=command, source=source):
                raise _source_version_conflict()
            now = self._clock()
            issue_id = _issue_id(scope=scope, idempotency_key=idempotency_key)
            record = ManualIssueRecord(
                id=issue_id,
                etag=ResourceVersion(1).etag,
                scope=scope,
                dataset_id=source.dataset_id,
                origin_dataset_version_id=source.version_id,
                episode_id=source.episode_id,
                episode_revision_id=source.revision_id,
                episode_stream_id=source.stream_id,
                schema_snapshot_id=source.schema_snapshot_id,
                robot_model_version_id=source.robot_model_version_id,
                calibration_set_id=source.calibration_set_id,
                start_ns=command.start_ns,
                end_ns=command.end_ns,
                issue_type=command.issue_type,
                severity=command.severity,
                status="OPEN",
                note=command.note.strip(),
                created_at=now,
                updated_at=now,
            )
            audit = ManualIssueAuditEvent(
                project_id=scope.project_id,
                region_code=scope.region_code,
                actor_id=auth.subject_id,
                action="manual_issue.created",
                resource_id=record.id,
                request_id=request_id,
                occurred_at=now,
                after_hash=canonical_hash(record.model_dump(mode="json")),
                details={"issue_type": record.issue_type, "severity": record.severity},
            )
            try:
                saved = self._repository.create_issue(record=record, audit_event=audit)
            except ManualIssueDuplicateError as exc:
                raise problem(
                    status=409,
                    code="MANUAL_ISSUE_CONFLICT",
                    title="ManualIssue already exists",
                    detail="The same ManualIssue create intent was already recorded.",
                ) from exc
            return ManualIssueMutationRecord(issue=saved)

        result = self._idempotency.execute(
            scope=project_id,
            key=f"manual-issue:create:{idempotency_key}",
            payload=payload,
            action=action,
        )
        return ManualIssueMutationOutcome(record=result.value, replayed=result.replayed)

    def triage_issue(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        issue_id: str,
        command: TriageManualIssueCommand,
        if_match: str,
        idempotency_key: str,
        request_id: str,
    ) -> ManualIssueMutationOutcome:
        scope = self._authorize_triage(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        payload = {"command": command.model_dump(mode="json"), "if_match": if_match}

        def action() -> ManualIssueMutationRecord:
            previous = self._required_issue(scope=scope, issue_id=issue_id)
            self._require_etag(record=previous, if_match=if_match)
            if previous.status == "RESOLVED":
                raise _terminal_issue()
            assignee = (
                None
                if command.assignee_id is None
                else ManualIssuePrincipal(
                    id=command.assignee_id,
                    display_name=command.assignee_id,
                )
            )
            now = self._clock()
            next_record = previous.model_copy(
                update={
                    "etag": _next_etag(previous.etag),
                    "status": command.target_status,
                    "severity": command.severity,
                    "assignee": assignee,
                    "updated_at": now,
                }
            )
            audit = ManualIssueAuditEvent(
                project_id=scope.project_id,
                region_code=scope.region_code,
                actor_id=auth.subject_id,
                action="manual_issue.triaged",
                resource_id=previous.id,
                request_id=request_id,
                occurred_at=now,
                before_hash=canonical_hash(previous.model_dump(mode="json")),
                after_hash=canonical_hash(next_record.model_dump(mode="json")),
                details={"target_status": command.target_status, "severity": command.severity},
            )
            try:
                self._repository.update_issue(
                    expected_etag=previous.etag,
                    record=next_record,
                    audit_event=audit,
                )
            except ManualIssuePreconditionError as exc:
                raise _etag_conflict() from exc
            return ManualIssueMutationRecord(issue=next_record)

        result = self._idempotency.execute(
            scope=project_id,
            key=f"manual-issue:triage:{issue_id}:{idempotency_key}",
            payload=payload,
            action=action,
        )
        return ManualIssueMutationOutcome(record=result.value, replayed=result.replayed)

    def create_draft_from_issue(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        issue_id: str,
        command: CreateDraftFromIssueCommand,
        if_match: str,
        idempotency_key: str,
        request_id: str,
    ) -> ManualIssueDraftOutcome:
        del command
        scope = self._authorize_cleaning_create(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        payload = {"if_match": if_match, "body": {}}

        def action() -> ManualIssueDraftMutationRecord:
            previous = self._required_issue(scope=scope, issue_id=issue_id)
            self._require_etag(record=previous, if_match=if_match)
            if previous.status == "RESOLVED":
                raise _terminal_issue()
            drafts = self._repository.list_drafts_for_issue(scope=scope, issue_id=issue_id)
            editing = tuple(item for item in drafts if item.status == "EDITING")
            now = self._clock()
            if len(editing) > 1:
                result: ManualIssueDraftResult = ManualIssueDraftSelectionRequired(
                    selection_token=self._selection_token(
                        scope=scope,
                        issue=previous,
                        candidates=editing,
                        expires_at=now + timedelta(minutes=10),
                    ),
                    expires_at=now + timedelta(minutes=10),
                    candidates=tuple(self._draft_candidate(item) for item in editing),
                )
                self._audit(
                    auth=auth,
                    scope=scope,
                    action="manual_issue.draft_selection_required",
                    resource_id=previous.id,
                    request_id=request_id,
                    details={"candidate_count": len(editing)},
                )
                return ManualIssueDraftMutationRecord(
                    scope=scope,
                    issue_etag=previous.etag,
                    result=result,
                )
            if drafts:
                selected = max(drafts, key=lambda item: (item.updated_at, item.draft_id))
                result = ManualIssueDraftCreated(
                    disposition="ALREADY_LINKED",
                    draft_id=selected.draft_id,
                    context=self._draft_context(
                        previous, selected_stream_path=selected.selected_channel_path
                    ),
                )
                self._audit(
                    auth=auth,
                    scope=scope,
                    action="manual_issue.draft_resumed",
                    resource_id=selected.draft_id,
                    request_id=request_id,
                    details={"manual_issue_id": previous.id},
                )
                return ManualIssueDraftMutationRecord(
                    scope=scope,
                    issue_etag=previous.etag,
                    result=result,
                )

            source = self._repository.source_facts(
                scope=scope,
                version_id=previous.origin_dataset_version_id,
                episode_id=previous.episode_id,
                revision_id=previous.episode_revision_id,
                stream_id=previous.episode_stream_id,
            )
            if source is None:
                raise _source_version_conflict()
            draft_id = _draft_id(scope=scope, issue_id=previous.id, idempotency_key=idempotency_key)
            draft = ManualCleaningDraftRecord(
                scope=scope,
                draft_id=draft_id,
                source_issue_id=previous.id,
                dataset_id=previous.dataset_id,
                base_version_id=previous.origin_dataset_version_id,
                episode_id=previous.episode_id,
                base_revision_id=previous.episode_revision_id,
                selected_stream_id=previous.episode_stream_id,
                selected_channel_path=source.stream_channel_path,
                start_ns=previous.start_ns,
                end_ns=previous.end_ns,
                status="EDITING",
                created_at=now,
                updated_at=now,
            )
            next_status = "IN_PROGRESS" if previous.status == "OPEN" else previous.status
            next_assignee = (
                ManualIssuePrincipal(id=auth.subject_id, display_name=auth.subject_id)
                if previous.status == "OPEN"
                else previous.assignee
            )
            next_issue = previous.model_copy(
                update={
                    "etag": _next_etag(previous.etag),
                    "status": next_status,
                    "assignee": next_assignee,
                    "related_drafts": tuple(
                        sorted(
                            (
                                *previous.related_drafts,
                                ManualIssueDraftRef(
                                    draft_id=draft_id,
                                    status="EDITING",
                                    updated_at=now,
                                ),
                            ),
                            key=lambda item: item.draft_id,
                        )
                    ),
                    "updated_at": now,
                }
            )
            audit = ManualIssueAuditEvent(
                project_id=scope.project_id,
                region_code=scope.region_code,
                actor_id=auth.subject_id,
                action="cleaning.draft.created",
                resource_id=draft_id,
                request_id=request_id,
                occurred_at=now,
                before_hash=canonical_hash(previous.model_dump(mode="json")),
                after_hash=canonical_hash(next_issue.model_dump(mode="json")),
                details={"manual_issue_id": previous.id},
            )
            try:
                self._repository.create_draft(
                    expected_etag=previous.etag,
                    next_issue=next_issue,
                    draft=draft,
                    audit_event=audit,
                )
            except ManualIssuePreconditionError as exc:
                raise _etag_conflict() from exc
            return ManualIssueDraftMutationRecord(
                scope=scope,
                issue_etag=next_issue.etag,
                result=ManualIssueDraftCreated(
                    disposition="CREATED",
                    draft_id=draft_id,
                    context=self._draft_context(
                        next_issue,
                        selected_stream_path=source.stream_channel_path,
                    ),
                ),
            )

        result = self._idempotency.execute(
            scope=project_id,
            key=f"manual-issue:draft:{issue_id}:{idempotency_key}",
            payload=payload,
            action=action,
        )
        return ManualIssueDraftOutcome(record=result.value, replayed=result.replayed)

    def resolve_issue(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        issue_id: str,
        command: ResolveManualIssueCommand,
        if_match: str,
        idempotency_key: str,
        request_id: str,
    ) -> ManualIssueMutationOutcome:
        scope = self._authorize_resolve(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        payload = {"command": command.model_dump(mode="json"), "if_match": if_match}

        def action() -> ManualIssueMutationRecord:
            previous = self._required_issue(scope=scope, issue_id=issue_id)
            self._require_etag(record=previous, if_match=if_match)
            if previous.status == "RESOLVED":
                raise _terminal_issue()
            if previous.status != "IN_PROGRESS":
                raise problem(
                    status=409,
                    code="MANUAL_ISSUE_NOT_IN_PROGRESS",
                    title="ManualIssue is not ready to resolve",
                    detail="Resolve requires an IN_PROGRESS ManualIssue.",
                )
            candidate = self._repository.resolution_candidate(
                scope=scope,
                issue_id=issue_id,
                resolution_version_id=command.resolution_version_id,
            )
            if candidate is None:
                raise problem(
                    status=422,
                    code="RESOLUTION_VERSION_INELIGIBLE",
                    title="Resolution Version is ineligible",
                    detail=(
                        "Resolution requires a same-scope READY output from a successful "
                        "Draft commit with immutable ancestry from this ManualIssue."
                    ),
                )
            now = self._clock()
            resolution = ManualIssueResolutionVersion(
                version_id=candidate.version_id,
                producer_draft_id=candidate.producer_draft_id,
                root_issue_draft_id=candidate.root_issue_draft_id,
                lineage_depth=candidate.lineage_depth,
                resolved_at=now,
            )
            next_issue = previous.model_copy(
                update={
                    "etag": _next_etag(previous.etag),
                    "status": "RESOLVED",
                    "resolution_version": resolution,
                    "resolution_note": command.resolution_note.strip(),
                    "resolved_at": now,
                    "resolved_by": ManualIssuePrincipal(
                        id=auth.subject_id,
                        display_name=auth.subject_id,
                    ),
                    "updated_at": now,
                }
            )
            audit = ManualIssueAuditEvent(
                project_id=scope.project_id,
                region_code=scope.region_code,
                actor_id=auth.subject_id,
                action="manual_issue.resolved",
                resource_id=previous.id,
                request_id=request_id,
                occurred_at=now,
                before_hash=canonical_hash(previous.model_dump(mode="json")),
                after_hash=canonical_hash(next_issue.model_dump(mode="json")),
                details={"resolution_version_id": candidate.version_id},
            )
            try:
                self._repository.update_issue(
                    expected_etag=previous.etag,
                    record=next_issue,
                    audit_event=audit,
                )
            except ManualIssuePreconditionError as exc:
                raise _etag_conflict() from exc
            return ManualIssueMutationRecord(issue=next_issue)

        result = self._idempotency.execute(
            scope=project_id,
            key=f"manual-issue:resolve:{issue_id}:{idempotency_key}",
            payload=payload,
            action=action,
        )
        return ManualIssueMutationOutcome(record=result.value, replayed=result.replayed)

    def detail_envelope(
        self, *, record: ManualIssueRecord, auth: AuthContext, request_id: str
    ) -> ManualIssueDetailEnvelope:
        return ManualIssueDetailEnvelope(
            data=self._present(record, auth=auth),
            scope=record.scope,
            request_id=request_id,
        )

    def draft_envelope(
        self, *, record: ManualIssueDraftMutationRecord, request_id: str
    ) -> ManualIssueCreateDraftEnvelope:
        return ManualIssueCreateDraftEnvelope(
            data=record.result,
            scope=record.scope,
            request_id=request_id,
        )

    def _authorize_read(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
    ) -> ManualIssueScope:
        ScopeGuard.require(auth, project_id, region_code)
        auth.require_capability("manual_issue.read", project_id)
        return self._scope(organization_id, project_id, region_code)

    def _authorize_create(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
    ) -> ManualIssueScope:
        ScopeGuard.require(auth, project_id, region_code)
        auth.require_capability("manual_issue.create", project_id)
        return self._scope(organization_id, project_id, region_code)

    def _authorize_triage(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
    ) -> ManualIssueScope:
        ScopeGuard.require(auth, project_id, region_code)
        auth.require_capability("manual_issue.triage", project_id)
        return self._scope(organization_id, project_id, region_code)

    def _authorize_cleaning_create(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
    ) -> ManualIssueScope:
        ScopeGuard.require(auth, project_id, region_code)
        auth.require_capability("cleaning.create", project_id)
        return self._scope(organization_id, project_id, region_code)

    def _authorize_resolve(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
    ) -> ManualIssueScope:
        ScopeGuard.require(auth, project_id, region_code)
        auth.require_capability("manual_issue.resolve", project_id)
        return self._scope(organization_id, project_id, region_code)

    def _scope(self, organization_id: str, project_id: str, region_code: str) -> ManualIssueScope:
        if not self._repository.has_organization_project(
            organization_id=organization_id,
            project_id=project_id,
        ):
            raise problem(
                status=403,
                code="ORGANIZATION_SCOPE_DENIED",
                title="Organization access denied",
                detail="The selected organization is not bound to this project.",
            )
        return ManualIssueScope(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )

    def _required_issue(self, *, scope: ManualIssueScope, issue_id: str) -> ManualIssueRecord:
        record = self._repository.get_issue(scope=scope, issue_id=issue_id)
        if record is None:
            raise problem(
                status=404,
                code="MANUAL_ISSUE_NOT_FOUND",
                title="ManualIssue not found",
                detail="The ManualIssue does not exist in the selected scope.",
            )
        return record

    @staticmethod
    def _require_etag(*, record: ManualIssueRecord, if_match: str) -> None:
        if record.etag != if_match:
            raise _etag_conflict()

    def _present(self, record: ManualIssueRecord, *, auth: AuthContext) -> ManualIssueRecord:
        actions: list[str] = ["VIEW_EPISODE"]
        reasons: list[ManualIssueBlockedReason] = []
        project_id = record.scope.project_id
        if record.status == "RESOLVED":
            reasons.append(
                ManualIssueBlockedReason(
                    code="MANUAL_ISSUE_TERMINAL",
                    message="This ManualIssue is resolved and its lifecycle is terminal.",
                )
            )
        else:
            if auth.has_capability("manual_issue.triage", project_id):
                actions.append("TRIAGE")
            if auth.has_capability("cleaning.create", project_id):
                actions.append("CREATE_DRAFT" if record.status == "OPEN" else "CONTINUE_DRAFT")
            if record.status == "IN_PROGRESS" and auth.has_capability(
                "manual_issue.resolve", project_id
            ):
                actions.append("RESOLVE")
        return record.model_copy(
            update={
                "allowed_actions": tuple(actions),
                "blocked_reasons": tuple(reasons),
            }
        )

    def _list_item(self, record: ManualIssueRecord, *, auth: AuthContext) -> ManualIssueListItem:
        visible = self._present(record, auth=auth)
        return ManualIssueListItem(
            id=visible.id,
            etag=visible.etag,
            scope=visible.scope,
            dataset_id=visible.dataset_id,
            origin_dataset_version_id=visible.origin_dataset_version_id,
            episode_id=visible.episode_id,
            episode_revision_id=visible.episode_revision_id,
            episode_stream_id=visible.episode_stream_id,
            start_ns=visible.start_ns,
            end_ns=visible.end_ns,
            issue_type=visible.issue_type,
            severity=visible.severity,
            status=visible.status,
            assignee=visible.assignee,
            related_draft_count=str(len(visible.related_drafts)),
            resolution_version=visible.resolution_version,
            resolution_note=visible.resolution_note,
            resolved_at=visible.resolved_at,
            resolved_by=visible.resolved_by,
            allowed_actions=visible.allowed_actions,
            updated_at=visible.updated_at,
        )

    @staticmethod
    def _page_actions(*, auth: AuthContext, project_id: str) -> tuple[str, ...]:
        actions: list[str] = []
        if auth.has_capability("manual_issue.triage", project_id):
            actions.append("TRIAGE")
        if auth.has_capability("cleaning.create", project_id):
            actions.append("CREATE_DRAFT")
        if auth.has_capability("manual_issue.resolve", project_id):
            actions.append("RESOLVE")
        return tuple(actions)

    @staticmethod
    def _sort(
        records: tuple[ManualIssueRecord, ...], sort: ManualIssueSort
    ) -> tuple[ManualIssueRecord, ...]:
        if sort == "updated_at:asc,id:asc":
            return tuple(sorted(records, key=lambda item: (item.updated_at, item.id)))
        if sort == "severity:desc,updated_at:desc,id:desc":
            rank = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
            return tuple(
                sorted(
                    records,
                    key=lambda item: (rank[item.severity], item.updated_at, item.id),
                    reverse=True,
                )
            )
        if sort == "created_at:desc,id:desc":
            return tuple(sorted(records, key=lambda item: (item.created_at, item.id), reverse=True))
        return tuple(sorted(records, key=lambda item: (item.updated_at, item.id), reverse=True))

    def _page(
        self,
        *,
        records: tuple[ManualIssueRecord, ...],
        scope: ManualIssueScope,
        filters: ManualIssueFilters,
        sort: ManualIssueSort,
        after: str | None,
        before: str | None,
        limit: int,
    ) -> tuple[tuple[ManualIssueRecord, ...], ManualIssuePageInfo]:
        snapshot = _snapshot(records)
        cursor = after or before
        position: tuple[str, str] | None = None
        if cursor is not None:
            payload = self._cursor.decode(cursor)
            expected = {
                "kind": "manual-issue-page",
                "organization_id": scope.organization_id,
                "project_id": scope.project_id,
                "region_code": scope.region_code,
                "filters": _filter_document(filters),
                "sort": sort,
                "snapshot": snapshot,
            }
            if any(payload.get(key) != value for key, value in expected.items()):
                raise problem(
                    status=409,
                    code="CURSOR_SNAPSHOT_STALE",
                    title="ManualIssue page changed",
                    detail="Refresh the ManualIssue list before continuing pagination.",
                )
            raw_position = payload.get("position")
            if not isinstance(raw_position, list) or len(raw_position) != 2:
                raise problem(
                    status=400,
                    code="INVALID_CURSOR",
                    title="Invalid pagination cursor",
                    detail="The cursor does not contain a valid ManualIssue position.",
                )
            position = (str(raw_position[0]), str(raw_position[1]))
        keys = [self._cursor_position(record, sort) for record in records]
        if after is not None:
            start = _after_index(keys, position)
            visible = records[start : start + limit]
            has_previous = start > 0
            has_next = start + len(visible) < len(records)
        elif before is not None:
            end = _before_index(keys, position)
            start = max(0, end - limit)
            visible = records[start:end]
            has_previous = start > 0
            has_next = end < len(records)
        else:
            visible = records[:limit]
            has_previous = False
            has_next = len(visible) < len(records)
        next_after = (
            self._encode_cursor(
                scope=scope,
                filters=filters,
                sort=sort,
                snapshot=snapshot,
                position=self._cursor_position(visible[-1], sort),
            )
            if visible and has_next
            else None
        )
        next_before = (
            self._encode_cursor(
                scope=scope,
                filters=filters,
                sort=sort,
                snapshot=snapshot,
                position=self._cursor_position(visible[0], sort),
            )
            if visible and has_previous
            else None
        )
        return tuple(visible), ManualIssuePageInfo(
            after=next_after,
            before=next_before,
            has_next=has_next,
            has_previous=has_previous,
        )

    @staticmethod
    def _cursor_position(record: ManualIssueRecord, sort: ManualIssueSort) -> tuple[str, str]:
        if sort == "created_at:desc,id:desc":
            return (record.created_at.isoformat(), record.id)
        if sort == "severity:desc,updated_at:desc,id:desc":
            rank = {"LOW": "1", "MEDIUM": "2", "HIGH": "3", "CRITICAL": "4"}
            return (f"{rank[record.severity]}:{record.updated_at.isoformat()}", record.id)
        return (record.updated_at.isoformat(), record.id)

    def _encode_cursor(
        self,
        *,
        scope: ManualIssueScope,
        filters: ManualIssueFilters,
        sort: ManualIssueSort,
        snapshot: str,
        position: tuple[str, str],
    ) -> str:
        return self._cursor.encode(
            {
                "kind": "manual-issue-page",
                "organization_id": scope.organization_id,
                "project_id": scope.project_id,
                "region_code": scope.region_code,
                "filters": _filter_document(filters),
                "sort": sort,
                "snapshot": snapshot,
                "position": list(position),
            }
        )

    def _draft_context(
        self, issue: ManualIssueRecord, *, selected_stream_path: str | None
    ) -> ManualIssueDraftContext:
        return ManualIssueDraftContext(
            dataset_id=issue.dataset_id,
            base_version_id=issue.origin_dataset_version_id,
            episode_id=issue.episode_id,
            base_revision_id=issue.episode_revision_id,
            selected_stream_id=issue.episode_stream_id,
            selected_channel_path=selected_stream_path,
            start_ns=issue.start_ns,
            end_ns=issue.end_ns,
            manual_issue_ids=(issue.id,),
        )

    def _draft_candidate(self, draft: ManualCleaningDraftRecord) -> ManualIssueDraftCandidate:
        return ManualIssueDraftCandidate(
            draft_id=draft.draft_id,
            base_version_id=draft.base_version_id,
            base_revision_id=draft.base_revision_id,
            manual_issue_ids=(draft.source_issue_id,),
            updated_at=draft.updated_at,
        )

    def _selection_token(
        self,
        *,
        scope: ManualIssueScope,
        issue: ManualIssueRecord,
        candidates: tuple[ManualCleaningDraftRecord, ...],
        expires_at: datetime,
    ) -> str:
        return self._cursor.encode(
            {
                "kind": "manual-issue-draft-selection",
                "organization_id": scope.organization_id,
                "project_id": scope.project_id,
                "region_code": scope.region_code,
                "manual_issue_id": issue.id,
                "etag": issue.etag,
                "candidate_ids": [candidate.draft_id for candidate in candidates],
                "expires_at": expires_at.isoformat(),
            }
        )

    def _audit(
        self,
        *,
        auth: AuthContext,
        scope: ManualIssueScope,
        action: str,
        resource_id: str,
        request_id: str,
        details: dict[str, object] | None = None,
    ) -> None:
        self._repository.append_audit(
            ManualIssueAuditEvent(
                project_id=scope.project_id,
                region_code=scope.region_code,
                actor_id=auth.subject_id,
                action=action,
                resource_id=resource_id,
                request_id=request_id,
                occurred_at=self._clock(),
                details=details,
            )
        )


def _validated_filters(filters: ManualIssueFilters) -> ManualIssueFilters:
    def text(value: str | None, *, maximum: int = 256) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or len(normalized) > maximum:
            raise problem(
                status=422,
                code="MANUAL_ISSUE_FILTER_INVALID",
                title="Invalid ManualIssue filter",
                detail="ManualIssue filters must be non-empty and within their size limit.",
            )
        return normalized

    return ManualIssueFilters(
        query=text(filters.query),
        dataset_id=text(filters.dataset_id),
        version_id=text(filters.version_id),
        episode_id=text(filters.episode_id),
        statuses=tuple(sorted(set(filters.statuses))),
        issue_types=tuple(sorted(set(filters.issue_types))),
        severities=tuple(sorted(set(filters.severities))),
        assignee_id=text(filters.assignee_id),
    )


def _filter_document(filters: ManualIssueFilters) -> dict[str, object]:
    return {
        "q": filters.query,
        "dataset_id": filters.dataset_id,
        "version_id": filters.version_id,
        "episode_id": filters.episode_id,
        "status": list(filters.statuses),
        "issue_type": list(filters.issue_types),
        "severity": list(filters.severities),
        "assignee_id": filters.assignee_id,
    }


def _snapshot(records: tuple[ManualIssueRecord, ...]) -> str:
    return canonical_hash(
        [(record.id, record.etag, record.updated_at.isoformat()) for record in records]
    )


def _after_index(keys: list[tuple[str, str]], position: tuple[str, str] | None) -> int:
    if position is None:
        raise problem(
            status=400,
            code="INVALID_CURSOR",
            title="Invalid pagination cursor",
            detail="The cursor does not include an ordered position.",
        )
    try:
        return keys.index(position) + 1
    except ValueError as exc:
        raise problem(
            status=409,
            code="CURSOR_SNAPSHOT_STALE",
            title="ManualIssue page changed",
            detail="Refresh the ManualIssue list before continuing pagination.",
        ) from exc


def _before_index(keys: list[tuple[str, str]], position: tuple[str, str] | None) -> int:
    if position is None:
        raise problem(
            status=400,
            code="INVALID_CURSOR",
            title="Invalid pagination cursor",
            detail="The cursor does not include an ordered position.",
        )
    try:
        return keys.index(position)
    except ValueError as exc:
        raise problem(
            status=409,
            code="CURSOR_SNAPSHOT_STALE",
            title="ManualIssue page changed",
            detail="Refresh the ManualIssue list before continuing pagination.",
        ) from exc


def _range_within_source(
    *, command: CreateManualIssueCommand, source: ManualIssueSourceFacts
) -> bool:
    return (
        int(source.stream_start_ns)
        <= int(command.start_ns)
        < int(command.end_ns)
        <= int(source.stream_end_ns)
    )


def _issue_id(*, scope: ManualIssueScope, idempotency_key: str) -> str:
    value = uuid5(
        NAMESPACE_URL,
        f"manual-issue:{scope.organization_id}:{scope.project_id}:{scope.region_code}:{idempotency_key}",
    )
    return f"issue_{value.hex}"


def _draft_id(*, scope: ManualIssueScope, issue_id: str, idempotency_key: str) -> CleaningDraftId:
    value = uuid5(
        NAMESPACE_URL,
        f"manual-draft:{scope.organization_id}:{scope.project_id}:{scope.region_code}:{issue_id}:{idempotency_key}",
    )
    return f"draft_{value.hex}"


def _next_etag(value: str) -> str:
    version = ResourceVersion.from_etag(value)
    return ResourceVersion(version.value + 1).etag


def _source_version_conflict() -> Exception:
    return problem(
        status=412,
        code="VERSION_CONFLICT",
        title="Fixed source changed",
        detail="The requested Version, Revision, Stream, or range no longer matches durable facts.",
    )


def _etag_conflict() -> Exception:
    return problem(
        status=412,
        code="PRECONDITION_FAILED",
        title="ManualIssue changed",
        detail="The supplied If-Match value does not match the current ManualIssue.",
    )


def _terminal_issue() -> Exception:
    return problem(
        status=409,
        code="MANUAL_ISSUE_TERMINAL",
        title="ManualIssue is resolved",
        detail="Resolved ManualIssue records cannot be triaged, drafted, or resolved again.",
    )
