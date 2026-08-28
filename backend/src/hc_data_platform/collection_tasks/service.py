from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid5

from hc_data_platform.core.errors import problem
from hc_data_platform.core.pagination import CursorCodec
from hc_data_platform.security.idempotency import (
    IdempotencyStore,
    InMemoryIdempotencyStore,
    request_fingerprint,
)
from hc_data_platform.security.versioning import ResourceVersion

from .models import (
    CollectionTaskAttainment,
    CollectionTaskAttainmentStatus,
    CollectionTaskPackageList,
    CollectionTaskPage,
    CollectionTaskProgress,
    CollectionTaskQcProgress,
    CollectionTaskQualityRequirementStatus,
    CollectionTaskRatio,
    CollectionTaskRecord,
    CollectionTaskStatus,
    CollectionTaskTargetMetric,
    CollectionTaskTargetMetricStatus,
    CreateCollectionTask,
    ManifestObservedSources,
    ProgressFacts,
    UpdateCollectionTask,
    dataset_id_for_collection_task,
    utc_now,
)
from .ports import CollectionTaskRepositoryPort
from .repository import InMemoryCollectionTaskRepository, task_not_found

_TASK_ID_NAMESPACE = UUID("e076395c-8aa8-44cd-87e0-10e9b25a910d")
_CURSOR_VERSION = 1


@dataclass(frozen=True, slots=True)
class CommandResult:
    record: CollectionTaskRecord
    replayed: bool


class CollectionTaskService:
    def __init__(
        self,
        repository: CollectionTaskRepositoryPort | None = None,
        idempotency: IdempotencyStore | None = None,
        *,
        cursor_secret: str = "collection-task-local-cursor-secret",
    ) -> None:
        self.repository = repository or InMemoryCollectionTaskRepository()
        self.idempotency = idempotency or InMemoryIdempotencyStore()
        self._cursor = CursorCodec(cursor_secret)

    @staticmethod
    def etag(record: CollectionTaskRecord) -> str:
        return ResourceVersion(record.version).etag

    def create(
        self,
        *,
        organization_id: str,
        project_id: str,
        command: CreateCollectionTask,
        idempotency_key: str,
        created_by: str | None = None,
    ) -> CommandResult:
        payload = command.model_dump(mode="json")
        fingerprint = request_fingerprint(payload)
        task_id = str(
            uuid5(_TASK_ID_NAMESPACE, f"{organization_id}\0{project_id}\0{idempotency_key}")
        )

        def create_once() -> CollectionTaskRecord:
            dataset_id = command.dataset_id or dataset_id_for_collection_task(
                organization_id,
                project_id,
                task_id,
            )
            if command.dataset_id is not None and not self.repository.is_dataset_assignable(
                organization_id,
                project_id,
                command.dataset_id,
            ):
                raise problem(
                    status=422,
                    code="COLLECTION_TASK_DATASET_NOT_ASSIGNABLE",
                    title="Dataset cannot receive this task",
                    detail=(
                        "Select an active dataset in the current organization and project, "
                        "or let the task create a new dataset."
                    ),
                )
            return self.repository.create(
                CollectionTaskRecord(
                    collection_task_id=task_id,
                    organization_id=organization_id,
                    project_id=project_id,
                    dataset_id=dataset_id,
                    created_by=created_by,
                    name=command.name,
                    type=command.type,
                    scenario=command.scenario,
                    description=command.description,
                    target=command.target,
                    quality_threshold=command.quality_threshold,
                    create_fingerprint=fingerprint,
                )
            )

        result = self.idempotency.execute(
            scope=project_id,
            key=f"collection-task:{organization_id}:create:{idempotency_key}",
            payload=payload,
            action=create_once,
        )
        return CommandResult(record=result.value, replayed=result.replayed)

    def list(
        self,
        *,
        organization_id: str,
        project_id: str,
        status: CollectionTaskStatus | None,
        limit: int,
        cursor: str | None,
    ) -> CollectionTaskPage:
        after = self._decode_cursor(
            cursor,
            organization_id=organization_id,
            project_id=project_id,
            status=status,
        )
        records, has_more = self.repository.list(
            organization_id=organization_id,
            project_id=project_id,
            status=status,
            limit=limit,
            after=after,
        )
        next_cursor = None
        if has_more and records:
            last = records[-1]
            next_cursor = self._cursor.encode(
                {
                    "v": _CURSOR_VERSION,
                    "organization_id": organization_id,
                    "project_id": project_id,
                    "status": None if status is None else status.value,
                    "created_at": last.created_at.isoformat(),
                    "collection_task_id": last.collection_task_id,
                }
            )
        return CollectionTaskPage(
            items=tuple(record.public() for record in records),
            next_cursor=next_cursor,
        )

    def detail(
        self,
        organization_id: str,
        project_id: str,
        collection_task_id: str,
    ) -> CollectionTaskRecord:
        record = self.repository.get(organization_id, project_id, collection_task_id)
        if record is None:
            raise task_not_found()
        return record

    def update(
        self,
        *,
        organization_id: str,
        project_id: str,
        collection_task_id: str,
        command: UpdateCollectionTask,
        if_match: str,
    ) -> CollectionTaskRecord:
        changes = command.model_dump(exclude_unset=True)
        if not changes:
            raise problem(
                status=422,
                code="COLLECTION_TASK_UPDATE_EMPTY",
                title="Collection task update is empty",
                detail="At least one editable task field must be supplied.",
            )
        current = self.detail(organization_id, project_id, collection_task_id)
        requested_dataset_id = changes.get("dataset_id")
        if isinstance(requested_dataset_id, str) and requested_dataset_id != current.dataset_id:
            if not self.repository.is_dataset_assignable(
                organization_id,
                project_id,
                requested_dataset_id,
            ):
                raise problem(
                    status=422,
                    code="COLLECTION_TASK_DATASET_NOT_ASSIGNABLE",
                    title="Dataset cannot receive this task",
                    detail=("Select an active dataset in the current organization and project."),
                )
            if self.repository.has_received_packages(
                organization_id,
                project_id,
                collection_task_id,
            ):
                raise problem(
                    status=409,
                    code="COLLECTION_TASK_DATASET_REASSIGNMENT_BLOCKED",
                    title="Dataset assignment is locked",
                    detail=(
                        "This task already has received data. Use an explicit data migration "
                        "or split workflow instead of changing its dataset."
                    ),
                )
        expected_version = ResourceVersion.from_etag(if_match).value
        return self.repository.update(
            organization_id=organization_id,
            project_id=project_id,
            collection_task_id=collection_task_id,
            expected_version=expected_version,
            changes=changes,
        )

    def close(
        self,
        *,
        organization_id: str,
        project_id: str,
        collection_task_id: str,
        if_match: str,
        idempotency_key: str,
    ) -> CommandResult:
        expected_version = ResourceVersion.from_etag(if_match).value
        payload = {
            "collection_task_id": collection_task_id,
            "if_match": if_match,
        }

        def close_once() -> CollectionTaskRecord:
            return self.repository.close(
                organization_id=organization_id,
                project_id=project_id,
                collection_task_id=collection_task_id,
                expected_version=expected_version,
            )

        result = self.idempotency.execute(
            scope=project_id,
            key=(f"collection-task:{organization_id}:close:{collection_task_id}:{idempotency_key}"),
            payload=payload,
            action=close_once,
        )
        return CommandResult(record=result.value, replayed=result.replayed)

    def cancel(
        self,
        *,
        organization_id: str,
        project_id: str,
        collection_task_id: str,
        if_match: str,
        idempotency_key: str,
    ) -> CommandResult:
        return self._transition(
            organization_id=organization_id,
            project_id=project_id,
            collection_task_id=collection_task_id,
            if_match=if_match,
            idempotency_key=idempotency_key,
            action="cancel",
        )

    def reopen(
        self,
        *,
        organization_id: str,
        project_id: str,
        collection_task_id: str,
        if_match: str,
        idempotency_key: str,
    ) -> CommandResult:
        return self._transition(
            organization_id=organization_id,
            project_id=project_id,
            collection_task_id=collection_task_id,
            if_match=if_match,
            idempotency_key=idempotency_key,
            action="reopen",
        )

    def _transition(
        self,
        *,
        organization_id: str,
        project_id: str,
        collection_task_id: str,
        if_match: str,
        idempotency_key: str,
        action: str,
    ) -> CommandResult:
        expected_version = ResourceVersion.from_etag(if_match).value
        payload = {
            "collection_task_id": collection_task_id,
            "if_match": if_match,
            "action": action,
        }

        def transition_once() -> CollectionTaskRecord:
            if action == "cancel":
                return self.repository.cancel(
                    organization_id=organization_id,
                    project_id=project_id,
                    collection_task_id=collection_task_id,
                    expected_version=expected_version,
                )
            if action == "reopen":
                return self.repository.reopen(
                    organization_id=organization_id,
                    project_id=project_id,
                    collection_task_id=collection_task_id,
                    expected_version=expected_version,
                )
            raise ValueError(f"unsupported collection task transition action {action!r}")

        result = self.idempotency.execute(
            scope=project_id,
            key=(
                f"collection-task:{organization_id}:{action}:{collection_task_id}:{idempotency_key}"
            ),
            payload=payload,
            action=transition_once,
        )
        return CommandResult(record=result.value, replayed=result.replayed)

    def progress(
        self,
        organization_id: str,
        project_id: str,
        collection_task_id: str,
        region_code: str,
    ) -> CollectionTaskProgress:
        task = self.detail(organization_id, project_id, collection_task_id)
        facts = self.repository.progress(
            organization_id,
            project_id,
            collection_task_id,
            region_code,
        )
        evaluated = facts.evaluated_count
        if facts.pending_count < 0:
            raise RuntimeError("QC facts cannot exceed received package facts")
        return CollectionTaskProgress(
            collection_task_id=collection_task_id,
            organization_id=organization_id,
            project_id=project_id,
            status=task.status,
            as_of=facts.as_of,
            received_package_count=facts.received_package_count,
            captured_duration_seconds=facts.captured_duration_seconds,
            duration_observed_package_count=facts.duration_observed_package_count,
            duration_unknown_package_count=facts.duration_unknown_package_count,
            qc=CollectionTaskQcProgress(
                evaluated_count=evaluated,
                pass_count=facts.pass_count,
                risk_count=facts.risk_count,
                reject_count=facts.reject_count,
                pending_count=facts.pending_count,
                pass_rate=CollectionTaskRatio(
                    numerator=facts.pass_count,
                    denominator=evaluated,
                    value=None if evaluated == 0 else facts.pass_count / evaluated,
                ),
            ),
            attainment=self._attainment(task, facts),
            observed_sources=ManifestObservedSources(
                device_ids=facts.device_ids,
                camera_ids=facts.camera_ids,
                topic_names=facts.topic_names,
            ),
        )

    def packages(
        self,
        organization_id: str,
        project_id: str,
        collection_task_id: str,
        region_code: str,
    ) -> CollectionTaskPackageList:
        task = self.detail(organization_id, project_id, collection_task_id)
        items = self.repository.packages(
            organization_id,
            project_id,
            collection_task_id,
            region_code,
            task.dataset_id
            or dataset_id_for_collection_task(
                organization_id,
                project_id,
                collection_task_id,
            ),
        )
        return CollectionTaskPackageList(
            collection_task_id=collection_task_id,
            organization_id=organization_id,
            project_id=project_id,
            as_of=utc_now(),
            items=items,
        )

    @staticmethod
    def _attainment(
        task: CollectionTaskRecord,
        facts: ProgressFacts,
    ) -> CollectionTaskAttainment:
        """Evaluate explicit goals without changing lifecycle state.

        Repository implementations supply only immutable package/Manifest/QC facts;
        this is the one place where the public target policy is frozen.
        """

        target = task.target
        package_metric = (
            None
            if target is None or target.package_count is None
            else CollectionTaskService._target_metric(
                actual=float(facts.received_package_count),
                target=float(target.package_count),
            )
        )
        duration_metric = (
            None
            if target is None or target.duration_seconds is None
            else CollectionTaskService._target_metric(
                actual=(
                    None
                    if facts.duration_unknown_package_count
                    else facts.captured_duration_seconds
                ),
                target=target.duration_seconds,
            )
        )
        if task.quality_threshold is None:
            quality_status = CollectionTaskQualityRequirementStatus.NOT_CONFIGURED
        elif facts.received_package_count == 0 or facts.pending_count > 0:
            quality_status = CollectionTaskQualityRequirementStatus.PENDING_QC
        elif facts.pass_count / facts.received_package_count >= task.quality_threshold:
            quality_status = CollectionTaskQualityRequirementStatus.MET
        else:
            quality_status = CollectionTaskQualityRequirementStatus.NOT_MET

        configured = (
            package_metric is not None
            or duration_metric is not None
            or task.quality_threshold is not None
        )
        if not configured:
            status = CollectionTaskAttainmentStatus.NOT_CONFIGURED
        else:
            metric_statuses = tuple(
                metric.status for metric in (package_metric, duration_metric) if metric is not None
            )
            metrics_met = all(
                item
                in {
                    CollectionTaskTargetMetricStatus.MET,
                    CollectionTaskTargetMetricStatus.EXCEEDED,
                }
                for item in metric_statuses
            )
            quality_met = quality_status in {
                CollectionTaskQualityRequirementStatus.NOT_CONFIGURED,
                CollectionTaskQualityRequirementStatus.MET,
            }
            if metrics_met and quality_met:
                status = (
                    CollectionTaskAttainmentStatus.EXCEEDED
                    if CollectionTaskTargetMetricStatus.EXCEEDED in metric_statuses
                    else CollectionTaskAttainmentStatus.ATTAINED
                )
            else:
                status = CollectionTaskAttainmentStatus.IN_PROGRESS
        return CollectionTaskAttainment(
            status=status,
            package_count=package_metric,
            duration_seconds=duration_metric,
            quality_threshold=task.quality_threshold,
            quality_status=quality_status,
        )

    @staticmethod
    def _target_metric(*, actual: float | None, target: float) -> CollectionTaskTargetMetric:
        if actual is None:
            return CollectionTaskTargetMetric(
                actual=None,
                target=target,
                progress=None,
                status=CollectionTaskTargetMetricStatus.UNKNOWN,
            )
        status = (
            CollectionTaskTargetMetricStatus.EXCEEDED
            if actual > target
            else CollectionTaskTargetMetricStatus.MET
            if actual == target
            else CollectionTaskTargetMetricStatus.IN_PROGRESS
        )
        return CollectionTaskTargetMetric(
            actual=actual,
            target=target,
            progress=actual / target,
            status=status,
        )

    def _decode_cursor(
        self,
        cursor: str | None,
        *,
        organization_id: str,
        project_id: str,
        status: CollectionTaskStatus | None,
    ) -> tuple[datetime, str] | None:
        if cursor is None:
            return None
        payload = self._cursor.decode(cursor)
        expected_status = None if status is None else status.value
        try:
            if (
                payload["v"] != _CURSOR_VERSION
                or payload["organization_id"] != organization_id
                or payload["project_id"] != project_id
                or payload["status"] != expected_status
            ):
                raise ValueError
            created_at = datetime.fromisoformat(str(payload["created_at"]))
            task_id = str(payload["collection_task_id"])
            if created_at.tzinfo is None or not task_id:
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise problem(
                status=400,
                code="INVALID_CURSOR",
                title="Invalid pagination cursor",
                detail="The cursor does not belong to this collection-task query.",
            ) from exc
        return created_at, task_id
