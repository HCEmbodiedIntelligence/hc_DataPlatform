"""Authorize a task's immutable data target through existing logical readers."""

from typing import TYPE_CHECKING, Protocol

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import ProblemException, problem
from hc_data_platform.episode_store.catalog_models import CollectionVersion
from hc_data_platform.logical_data.models import AnnotationLogicalSnapshot, SnapshotSelection
from hc_data_platform.logical_data.ports import SnapshotReader
from hc_data_platform.security import AuthContext

if TYPE_CHECKING:
    from hc_data_platform.annotation.service import AnnotationService
    from hc_data_platform.dataset_registry.service import DatasetPageService


class FixedCollectionReader(Protocol):
    def get(self, dataset_id: str, *, version: int) -> CollectionVersion: ...


class AnnotationLogicalService:
    def __init__(
        self,
        annotations: "AnnotationService",
        collections: FixedCollectionReader,
        revisions: "DatasetPageService",
        logical: SnapshotReader,
    ) -> None:
        self.annotations, self.collections = annotations, collections
        self.revisions, self.logical = revisions, logical

    def snapshot(
        self,
        task_id: str,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        auth: AuthContext,
    ) -> AnnotationLogicalSnapshot:
        task = self.annotations.read_task(task_id, auth)
        if (task.project_id, task.region_code) != (project_id, region_code):
            raise self._unavailable("Task scope differs from the selected logical scope.")
        if task.data_ref is not None:
            data = task.data_ref
            if data.identity.scope != (organization_id, project_id, region_code):
                raise self._unavailable("Task data belongs to another tenant scope.")
            collection = self.collections.get(task.dataset_id, version=task.dataset_version)
            if data not in collection.episodes:
                raise self._unavailable("The task data is absent from its frozen collection.")
            version_id = "version_" + collection.collection_version_id
        else:
            if task.base_lance_version is None:
                raise self._unavailable("Task has no frozen historical source version.")
            version_id = "version_lance_" + str(task.base_lance_version)
        scope = dict(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            dataset_id=task.dataset_id,
            version_id=version_id,
        )
        request_id = current_request_context().request_id
        bootstrap = self.revisions.version_bootstrap(auth=auth, request_id=request_id, **scope).data
        revision = self.revisions.rollout_revision(
            auth=auth,
            request_id=request_id,
            **scope,
            rollout_id=task.rollout_id,
            snapshot_token=bootstrap.snapshot_token,
        ).data
        for stream in revision.streams:
            binding = stream.package_binding or stream.data_binding or stream.aligned_media_binding
            if (
                binding is None
                or binding.start_step != 0
                or binding.end_step != task.base_step_count
            ):
                raise self._unavailable("Task samples differ from the fixed product revision.")
            if task.data_ref is not None:
                if stream.package_binding is None or stream.package_binding.data != task.data_ref:
                    raise self._unavailable("Task package differs from the fixed logical source.")
            elif stream.package_binding is not None:
                raise self._unavailable("Historical task cannot be rebound to a migrated package.")
        view = self.logical.snapshot(
            SnapshotSelection(**scope, revision_id=revision.revision_id),
            auth=auth,
            snapshot_token=bootstrap.snapshot_token,
        )
        return AnnotationLogicalSnapshot(
            **view.model_dump(), task_id=task.task_id, snapshot_token=bootstrap.snapshot_token
        )

    @staticmethod
    def _unavailable(detail: str) -> ProblemException:
        return problem(
            status=409,
            code="ANNOTATION_LOGICAL_BINDING_UNAVAILABLE",
            title="Annotation logical source unavailable",
            detail=detail,
        )
