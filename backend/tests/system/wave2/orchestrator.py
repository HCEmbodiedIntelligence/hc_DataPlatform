"""Diagnostic stage orchestration for the BE22 real-API main chain."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from .fixture import CleanupController, RunArtifact, RunScope, StageStatus, utc_now


class StageNotRunnable(RuntimeError):
    """A declared external/product dependency prevents a stage from executing."""


@dataclass(frozen=True, slots=True)
class StageResult:
    resource_ids: dict[str, str] = field(default_factory=dict)
    request_ids: tuple[str, ...] = ()
    audit_event_ids: tuple[str, ...] = ()
    detail: str | None = None


class MainChainAdapter(Protocol):
    def register_empty_accounts(self, scope: RunScope) -> StageResult: ...

    def approve_membership_and_capabilities(self, scope: RunScope) -> StageResult: ...

    def assert_cross_project_denied(self, scope: RunScope) -> StageResult: ...

    def create_collection_task(self, scope: RunScope) -> StageResult: ...

    def upload_and_discover_manifest(self, scope: RunScope) -> StageResult: ...

    def wait_for_worker_qc(self, scope: RunScope) -> StageResult: ...

    def annotate_multilevel_tags(self, scope: RunScope) -> StageResult: ...

    def review_tags(self, scope: RunScope) -> StageResult: ...

    def close_collection_task(self, scope: RunScope) -> StageResult: ...


STAGES = (
    ("register_empty_accounts", "register_empty_accounts"),
    ("approve_membership_and_capabilities", "approve_membership_and_capabilities"),
    ("cross_project_identity_denied", "assert_cross_project_denied"),
    ("create_collection_task", "create_collection_task"),
    ("upload_and_manifest_discovery", "upload_and_discover_manifest"),
    ("worker_manifest_qc_alignment", "wait_for_worker_qc"),
    ("annotation_multilevel_tags", "annotate_multilevel_tags"),
    ("distinct_reviewer_tag_approval", "review_tags"),
    ("close_collection_task", "close_collection_task"),
)


@dataclass(slots=True)
class MainChainRun:
    adapter: MainChainAdapter
    cleanup: CleanupController

    def execute(self, scope: RunScope, *, artifact_path: Path | None = None) -> RunArtifact:
        artifact = RunArtifact.for_scope(scope)
        pre_cleanup = self.cleanup.execute(scope)
        artifact.cleanup.append({"phase": "before", "results": pre_cleanup})
        if any(item["status"] != "CLEAN" for item in pre_cleanup):
            now = utc_now()
            artifact.add_stage(
                "pre_cleanup",
                StageStatus.FAIL,
                started_at=now,
                detail="At least one cleanup target failed; no test data was created.",
            )
            self._mark_remaining(artifact, 0, "pre-cleanup failed")
            if artifact_path is not None:
                artifact.write(artifact_path)
            return artifact

        stopped_at: int | None = None
        try:
            for index, (stage_name, method_name) in enumerate(STAGES):
                started = utc_now()
                try:
                    result = getattr(self.adapter, method_name)(scope)
                except StageNotRunnable as exc:
                    artifact.add_stage(
                        stage_name,
                        StageStatus.NOT_RUN,
                        started_at=started,
                        detail=str(exc),
                    )
                    stopped_at = index + 1
                    break
                except Exception as exc:
                    artifact.add_stage(
                        stage_name,
                        StageStatus.FAIL,
                        started_at=started,
                        detail=f"{type(exc).__name__}: {exc}",
                    )
                    stopped_at = index + 1
                    break
                artifact.add_stage(
                    stage_name,
                    StageStatus.PASS,
                    started_at=started,
                    resource_ids=result.resource_ids,
                    request_ids=result.request_ids,
                    audit_event_ids=result.audit_event_ids,
                    detail=result.detail,
                )
            if stopped_at is not None:
                self._mark_remaining(artifact, stopped_at, "blocked by an earlier stage")
        finally:
            post_cleanup = self.cleanup.execute(scope)
            artifact.cleanup.append({"phase": "after", "results": post_cleanup})
            if any(item["status"] != "CLEAN" for item in post_cleanup):
                artifact.add_stage(
                    "post_cleanup",
                    StageStatus.FAIL,
                    started_at=datetime.now().astimezone(),
                    detail="Cleanup is recoverable by rerunning with the same run_id.",
                )
            if artifact_path is not None:
                artifact.write(artifact_path)
        return artifact

    @staticmethod
    def _mark_remaining(artifact: RunArtifact, start: int, reason: str) -> None:
        for stage_name, _method_name in STAGES[start:]:
            now = utc_now()
            artifact.add_stage(
                stage_name,
                StageStatus.NOT_RUN,
                started_at=now,
                detail=reason,
            )


def passed(artifact: RunArtifact) -> bool:
    return (
        bool(artifact.stages)
        and all(item.status is StageStatus.PASS for item in artifact.stages)
        and all(
            result["status"] == "CLEAN"
            for cleanup in artifact.cleanup
            for result in cleanup["results"]
        )
    )
