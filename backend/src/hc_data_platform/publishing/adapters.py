"""Adapters from the frozen BE-08/BE-09 contracts into publishing ports."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from hc_data_platform.annotation.models import (
    AnnotationRevision,
    AnnotationStatus,
    AnnotationTask,
    ExclusionRange,
)
from hc_data_platform.core.errors import problem
from hc_data_platform.lance_catalog.ports import LanceCatalogPort, StepReaderPort

from .models import (
    ApprovedAnnotationSnapshotV1,
    CatalogRolloutSnapshotV1,
    DerivedStatus,
    ExportStepV1,
    QualityStatus,
    StepRangeV1,
)

_LANCE_VERSION = re.compile(r"(?:(?:lance-)?v)?([1-9][0-9]*)")


def parse_lance_version(value: str) -> int:
    """Translate the BE-11 external version token to BE-08's integer version."""

    match = _LANCE_VERSION.fullmatch(value)
    if match is None:
        raise problem(
            status=422,
            code="INVALID_LANCE_VERSION",
            title="Invalid Lance version",
            detail="The base Lance version must be a positive integer or a v-prefixed token.",
        )
    return int(match.group(1))


class CatalogRolloutStateV1(BaseModel):
    """Readiness facts not owned by BE-08, frozen at one catalog version."""

    model_config = ConfigDict(frozen=True)

    project_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_version: int = Field(ge=1)
    rollout_id: str = Field(min_length=1)
    total_steps: int = Field(gt=0)
    quality_status: QualityStatus
    derived_status: DerivedStatus
    quality_profile_version: str = Field(min_length=1)
    alignment_profile_version: str = Field(min_length=1)
    alignment_frequency_hz: int = Field(gt=0)


class CatalogRolloutStatePort(Protocol):
    def list_states(
        self, *, project_id: str, dataset_id: str, dataset_version: int
    ) -> Sequence[CatalogRolloutStateV1]: ...


class InMemoryCatalogRolloutState:
    def __init__(self, states: Sequence[CatalogRolloutStateV1] = ()) -> None:
        self._states = tuple(states)

    def list_states(
        self, *, project_id: str, dataset_id: str, dataset_version: int
    ) -> Sequence[CatalogRolloutStateV1]:
        return tuple(
            state
            for state in self._states
            if state.project_id == project_id
            and state.dataset_id == dataset_id
            and state.dataset_version == dataset_version
        )


class CatalogSnapshotAdapter:
    """Combines BE-08 lineage with frozen QC/alignment readiness facts."""

    def __init__(
        self, *, catalog: LanceCatalogPort, rollout_states: CatalogRolloutStatePort
    ) -> None:
        self._catalog = catalog
        self._rollout_states = rollout_states

    def list_rollouts(
        self, *, project_id: str, dataset_id: str, lance_version: str
    ) -> Sequence[CatalogRolloutSnapshotV1]:
        version = parse_lance_version(lance_version)
        states = self._rollout_states.list_states(
            project_id=project_id,
            dataset_id=dataset_id,
            dataset_version=version,
        )
        snapshots: list[CatalogRolloutSnapshotV1] = []
        for state in states:
            if (
                state.project_id != project_id
                or state.dataset_id != dataset_id
                or state.dataset_version != version
            ):
                raise problem(
                    status=409,
                    code="CATALOG_SNAPSHOT_SCOPE_MISMATCH",
                    title="Catalog snapshot scope mismatch",
                    detail="A rollout state does not belong to the requested frozen snapshot.",
                )
            try:
                lineage = self._catalog.lineage(
                    dataset_id,
                    state.rollout_id,
                    version=version,
                    project_id=project_id,
                )
            except KeyError as exc:
                raise problem(
                    status=409,
                    code="CATALOG_LINEAGE_MISSING",
                    title="Catalog lineage is incomplete",
                    detail=(
                        "A readiness record exists without BE-08 lineage at the requested "
                        "Lance version."
                    ),
                    details={"rollout_id": state.rollout_id, "lance_version": version},
                ) from exc
            snapshots.append(
                CatalogRolloutSnapshotV1(
                    rollout_id=state.rollout_id,
                    source_mcap_sha256=lineage.source_sha256,
                    total_steps=state.total_steps,
                    quality_status=state.quality_status,
                    derived_status=state.derived_status,
                    quality_profile_version=state.quality_profile_version,
                    alignment_profile_version=state.alignment_profile_version,
                    alignment_frequency_hz=state.alignment_frequency_hz,
                    converter_version=lineage.converter_version,
                )
            )
        return tuple(sorted(snapshots, key=lambda item: item.rollout_id))


class StepReaderAdapter:
    """Maps BE-08 logical Step windows to exporter rows without physical row IDs."""

    def __init__(self, reader: StepReaderPort) -> None:
        self._reader = reader

    def read_steps(
        self,
        *,
        project_id: str,
        dataset_id: str,
        rollout_id: str,
        lance_version: str,
        ranges: Sequence[StepRangeV1],
    ) -> Sequence[ExportStepV1]:
        version = parse_lance_version(lance_version)
        exported: list[ExportStepV1] = []
        for step_range in ranges:
            try:
                window = self._reader.read_steps(
                    dataset_id,
                    rollout_id,
                    step_range.start_step,
                    step_range.end_step,
                    version=version,
                    project_id=project_id,
                )
            except KeyError as exc:
                raise problem(
                    status=409,
                    code="EXPORT_SOURCE_NOT_FOUND",
                    title="Export source not found",
                    detail="BE-08 cannot resolve the frozen Lance Step window.",
                    details={
                        "dataset_id": dataset_id,
                        "rollout_id": rollout_id,
                        "lance_version": version,
                    },
                ) from exc
            if (
                window.dataset_id != dataset_id
                or window.dataset_version != version
                or window.rollout_id != rollout_id
            ):
                raise problem(
                    status=409,
                    code="EXPORT_SOURCE_SCOPE_MISMATCH",
                    title="Export source scope mismatch",
                    detail="BE-08 returned a Step window from another frozen snapshot.",
                )
            exported.extend(
                ExportStepV1(
                    rollout_id=step.rollout_id,
                    step_index=step.step_index,
                    timestamp_ns=step.timestamp_ns,
                    modalities=step.modalities,
                    source_timestamps_ns=step.source_timestamps_ns,
                    time_error_ns=step.time_error_ns,
                    valid=step.valid,
                    repeated=step.repeated,
                    sample_valid=step.sample_valid,
                )
                for step in window.steps
            )
        return tuple(exported)


class ApprovedAnnotationTaskLocatorPort(Protocol):
    def find_task_id(
        self,
        *,
        project_id: str,
        dataset_id: str,
        dataset_version: int,
        rollout_id: str,
    ) -> str | None: ...


class ApprovedAnnotationReadPort(Protocol):
    """Structural subset of BE-09's ``AnnotationReadPort`` used by publishing."""

    def get_task(self, task_id: str) -> AnnotationTask: ...

    def approved_revision(self, task_id: str) -> AnnotationRevision: ...


class AnnotationExclusionReadPort(Protocol):
    """Structural subset of BE-09's ``EffectiveExclusionPort``."""

    def effective_exclusions(
        self, task_id: str, *, revision: int | None = None
    ) -> tuple[ExclusionRange, ...]: ...


class InMemoryApprovedAnnotationTaskLocator:
    def __init__(self, task_ids: dict[tuple[str, str, int, str], str] | None = None) -> None:
        self._task_ids = dict(task_ids or {})

    def find_task_id(
        self,
        *,
        project_id: str,
        dataset_id: str,
        dataset_version: int,
        rollout_id: str,
    ) -> str | None:
        return self._task_ids.get((project_id, dataset_id, dataset_version, rollout_id))


class ApprovedAnnotationSnapshotAdapter:
    """Reads only the exact revision that BE-09 still considers APPROVED."""

    def __init__(
        self,
        *,
        annotations: ApprovedAnnotationReadPort,
        exclusions: AnnotationExclusionReadPort,
        task_locator: ApprovedAnnotationTaskLocatorPort,
    ) -> None:
        self._annotations = annotations
        self._exclusions = exclusions
        self._task_locator = task_locator

    def get_approved_revision(
        self,
        *,
        project_id: str,
        rollout_id: str,
        dataset_id: str | None = None,
        lance_version: str | None = None,
    ) -> ApprovedAnnotationSnapshotV1 | None:
        if dataset_id is None or lance_version is None:
            return None
        dataset_version = parse_lance_version(lance_version)
        task_id = self._task_locator.find_task_id(
            project_id=project_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            rollout_id=rollout_id,
        )
        if task_id is None:
            return None
        try:
            task = self._annotations.get_task(task_id)
        except KeyError:
            return None
        if (
            task.project_id != project_id
            or task.dataset_id != dataset_id
            or task.dataset_version != dataset_version
            or task.rollout_id != rollout_id
        ):
            raise problem(
                status=409,
                code="ANNOTATION_SNAPSHOT_SCOPE_MISMATCH",
                title="Annotation snapshot scope mismatch",
                detail="The located annotation task belongs to another catalog target.",
            )
        revision_number = task.approved_revision
        if (
            task.status is not AnnotationStatus.APPROVED
            or revision_number is None
            or task.current_revision != revision_number
        ):
            return None
        try:
            revision = self._annotations.approved_revision(task_id)
        except (KeyError, RuntimeError):
            return None
        if revision.revision != revision_number:
            raise problem(
                status=409,
                code="ANNOTATION_APPROVAL_MISMATCH",
                title="Approved annotation revision mismatch",
                detail="BE-09 returned a revision different from the task's approval pointer.",
            )
        effective = self._exclusions.effective_exclusions(
            task_id,
            revision=revision_number,
        )
        return ApprovedAnnotationSnapshotV1(
            rollout_id=rollout_id,
            annotation_revision=revision_number,
            annotation_task_id=task_id,
            excluded_step_ranges=tuple(
                StepRangeV1(start_step=item.start_step, end_step=item.end_step)
                for item in effective
            ),
        )


# Descriptive aliases used by production composition and older task notes.
LanceCatalogSnapshotAdapter = CatalogSnapshotAdapter
LanceStepReaderAdapter = StepReaderAdapter
