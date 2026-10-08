"""Authorized annotation selections shared by delivery and primary derivation.

A frozen plan retains the original review receipt. Later edits never change its
bytes; every read checks current access and exact immutable file/history bindings.
"""

import json

from hc_data_platform.core.errors import problem

from .delivery_models import AnnotationDeliveryPlan
from .revision_files import _failure
from .revision_models import ReviewReference, RevisionFileDownload, SnapshotIntegrityError
from .selection_ports import FrozenAnnotationSelection
from .subtasks import SubtaskProjection, SubtaskProjectionError


class AnnotationSelections:
    def __init__(self, files, package_files):
        self.files, self.package_files = files, package_files

    def authorize(self, task_id, auth):
        task = self.files.authorize(task_id, auth, capability="export.download")
        if task.data_ref is None:
            raise _failure(
                "ANNOTATION_DELIVERY_REQUIRES_EPISODE",
                "This delivery requires an independent Episode data version.",
            )
        return task

    def freeze_selection(self, task_id, body, auth):
        self.authorize(task_id, auth)
        download = self.files.download(
            task_id, auth, revision=body.revision, allow_unreviewed=body.allow_unreviewed
        )
        history = self.files.annotation.get_history(task_id, auth)
        data = history.task.data_ref
        package = self.package_files.open(data.identity, data.data_version_id)
        try:
            projection = SubtaskProjection.freeze(
                json.loads(download.content), data, download.file.content_sha256, body.track
            )
        except SubtaskProjectionError as exc:
            raise problem(
                status=422,
                code=exc.code,
                title="Invalid subtask selection",
                detail=str(exc),
                details={"annotation_ids": list(exc.annotation_ids)},
            ) from exc
        review = next((r for r in history.reviews if r.review_id == download.review_id), None)
        submission = next(
            (s for s in history.submissions if s.submission_id == download.submission_id), None
        )
        plan = AnnotationDeliveryPlan(
            binding=projection.binding,
            annotation_size=len(download.content),
            review=ReviewReference(
                status="APPROVED" if download.approved else "UNREVIEWED",
                review_id=download.review_id,
                submission_id=download.submission_id,
            ),
            approval=review,
            submission=submission,
            parent_manifest_sha256=package.receipt.manifest_sha256,
        )
        return FrozenAnnotationSelection(plan, download)

    def snapshot_for_plan(self, plan, auth):
        # Validate restored plans too; model_copy is not a trust boundary.
        plan = AnnotationDeliveryPlan.model_validate(plan.model_dump(mode="json"))
        task = self.authorize(plan.binding.task_id, auth)
        if task.data_ref != plan.binding.data:
            raise SnapshotIntegrityError("delivery plan changed")
        history = self.files.annotation.get_history(plan.binding.task_id, auth)
        if plan.approval is not None and (
            plan.approval not in history.reviews or plan.submission not in history.submissions
        ):
            raise SnapshotIntegrityError("frozen delivery approval is unavailable")
        download = self.files.read_frozen(
            task.task_id,
            plan.binding.revision,
            auth,
            revision_content_hash=plan.binding.revision_content_hash,
            content_sha256=plan.binding.annotation_file_sha256,
            size_bytes=plan.annotation_size,
        )
        return RevisionFileDownload(
            download.content,
            download.file,
            plan.review.status == "APPROVED",
            plan.review.review_id,
            plan.review.submission_id,
            object_key=download.object_key,
        )
