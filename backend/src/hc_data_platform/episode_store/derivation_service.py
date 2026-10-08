"""Durable explicit Tag creation, preserving parent data and all old approvals."""

import asyncio
import logging
from contextlib import suppress
from pathlib import Path
from tempfile import TemporaryDirectory

from hc_data_platform.annotation.automation import (
    AutomaticAnnotationBlocked,
    AutomaticAnnotationRequest,
    AutomaticAnnotationTaskService,
    PostgresAutomaticAnnotationRepository,
)
from hc_data_platform.annotation.delivery_models import RequestAnnotationDelivery
from hc_data_platform.annotation.revision_files import SnapshotIntegrityError
from hc_data_platform.core.artifact_tasks import DeliveryLeaseLost, renewing_lease
from hc_data_platform.core.context import current_request_context
from hc_data_platform.dataset_registry.ingest_projection import (
    DatasetIngestProjectionConflict,
    PostgresDatasetIngestProjector,
)
from hc_data_platform.dataset_registry.tag_projection import parent_projection
from hc_data_platform.quality.evidence import QcEvaluation
from hc_data_platform.security import AuthContext, ScopeGuard

from .delivery_repository import scope_key
from .derivation import freeze_tag_episode, write_tag_episode
from .derivation_jobs import TagDerivationPlan
from .derivation_repository import TagDerivationBusy, failure, public_job
from .errors import PackageConflict
from .models import digest
from .quality import read_episode_quality

logger = logging.getLogger(__name__)


class TagDerivationService:
    def __init__(self, packages, annotations, repository):
        self.packages, self.annotations, self.repository = packages, annotations, repository
        self.projector = PostgresDatasetIngestProjector(
            repository.connections, episode_packages=packages
        )
        self.automatic = AutomaticAnnotationTaskService(
            PostgresAutomaticAnnotationRepository(repository.connections)
        )

    def authorize(self, task_id, auth, *, command=False):
        organization, project, region = scope_key()
        ScopeGuard.require(auth, project, region, organization)
        auth.require_capability("dataset_version.read", project, organization)
        if command:
            # This creates primary data. Export or Tag-edit permission alone does
            # not confer the existing ingestion/data-creation capability.
            auth.require_capability("upload.manage", project, organization)
        return self.annotations.authorize(task_id, auth)

    @staticmethod
    def bound_job(row, task):
        job = public_job(row)
        if (
            job.plan.episode.annotation.binding.task_id != task.task_id
            or job.plan.episode.parent != task.data_ref
        ):
            raise failure("NOT_FOUND", "Tag job belongs to another annotation target.", 404)
        return job

    def freeze(self, task, body, auth):
        selection, _ = self.annotations._freeze(
            task.task_id,
            RequestAnnotationDelivery(
                revision=body.revision,
                allow_unreviewed=body.allow_unreviewed,
                track={
                    "track_id": "tag-episode",
                    "label": "创建新 Episode",
                    "annotation_ids": [body.annotation_id],
                },
            ),
            auth,
        )
        snapshot = self.annotations.snapshot_for_plan(selection, auth)
        data = task.data_ref
        package = self.packages.files.open(data.identity, data.data_version_id)
        schema = self.packages.schemas.schema_for(
            data.identity.project_id, data.identity.dataset_id
        )
        episode = freeze_tag_episode(
            package, selection, snapshot, schema, display_name=body.display_name
        )
        collection = self.packages.collections.get(
            data.identity.dataset_id, version=task.dataset_version
        )
        # Missing/ambiguous product provenance is rejected before enqueuing any
        # primary writes. The immutable selection is rechecked by the producer.
        with self.repository.transaction() as cursor:
            parent_projection(self.projector, cursor, episode, collection.collection_version_id)
        return TagDerivationPlan(
            episode=episode, parent_collection_version_id=collection.collection_version_id
        )

    def request(self, task_id, body, key, auth):
        task = self.authorize(task_id, auth, command=True)
        row = self.repository.request(
            task_id,
            key,
            auth.subject_id,
            digest(body.model_dump(mode="json")),
            lambda: self.freeze(task, body, auth),
        )
        return self.bound_job(row, task)

    def status(self, task_id, job_id, auth):
        task = self.authorize(task_id, auth)
        return self.bound_job(self.repository.get_job(job_id), task)

    def list(self, task_id, auth, *, limit=25, before=None):
        task = self.authorize(task_id, auth)
        jobs = self.repository.list_jobs(task_id, limit=limit, before=before)
        if any(j.plan.episode.parent != task.data_ref for j in jobs):
            raise PackageConflict("Tag task list differs from its annotation data binding")
        return jobs

    def command(self, task_id, job_id, generation, auth, *, retry):
        task = self.authorize(task_id, auth, command=True)
        self.bound_job(self.repository.get_job(job_id), task)
        return self.bound_job(
            self.repository.command(job_id, generation, auth.subject_id, retry=retry), task
        )

    def generate(self, job_id, generation):
        if not current_request_context().service_identity:
            raise PermissionError("Tag generation requires worker identity")
        row = self.repository.claim(job_id, generation)
        if row is None:
            return
        plan = public_job(row).plan
        episode, source = plan.episode, plan.episode.input
        try:
            with renewing_lease(self.repository, row) as checkpoint:
                auth = worker_auth()
                self.bound_job(row, self.authorize(row["task_id"], auth))
                snapshot = self.annotations.snapshot_for_plan(episode.annotation, auth)
                parent = self.packages.files.open(
                    episode.parent.identity, episode.parent.data_version_id
                )
                with self.repository.transaction() as cursor:
                    parent_projection(
                        self.projector, cursor, episode, plan.parent_collection_version_id
                    )
                try:
                    receipt = self.packages.repository.get(source.identity, source.data_version_id)
                except KeyError:
                    self.repository.phase(row, "deriving")
                    directory = self.packages.directory / "tag-episodes"
                    directory.mkdir(parents=True, exist_ok=True)
                    with TemporaryDirectory(prefix="attempt-", dir=directory) as temporary:
                        root = Path(temporary) / "episode"
                        write_tag_episode(root, parent, snapshot, episode, checkpoint=checkpoint)
                        self.repository.phase(row, "committing")
                        receipt = self.packages.committer.commit(root, source)
                self.repository.pin_data(row, receipt.data)
                quality = read_episode_quality(
                    self.packages.files.open(source.identity, source.data_version_id)
                )
                if quality.evidence is not None:
                    self.packages.quality_evidence.put(
                        QcEvaluation(report=quality.report, evidence=quality.evidence)
                    )
                checkpoint()
                self.repository.phase(row, "projecting")
                # Recovery verifies every committed primary member before reusing
                # previews/collection. It never re-quantizes or cuts the media again.
                publication = self.packages.recover_bundle(episode)
                if publication is None:
                    raise PackageConflict("committed Tag package disappeared during publication")
                version, ready = publication
                checkpoint()
                viewer = self.projector.project_tag_episode(
                    plan=episode,
                    parent_collection_version_id=plan.parent_collection_version_id,
                    version=version,
                    ready=ready,
                )
                self.repository.pin_projection(row, version, viewer)
                self.repository.phase(row, "annotating")
                task = self.automatic.ensure_task(
                    AutomaticAnnotationRequest(
                        project_id=source.identity.project_id,
                        region_code=source.identity.region_code,
                        dataset_id=source.identity.dataset_id,
                        rollout_id=source.identity.rollout_id,
                        dataset_version=version.version,
                        lance_version=None,
                        data_ref=ready.data,
                        dataset_schema_snapshot_id=source.schema_snapshot_id,
                        base_step_count=ready.step_count,
                        source_workflow_id="tag-episode:" + job_id,
                    )
                )
                self.repository.finish(row, annotation_task_id=task.task_id)
        except DeliveryLeaseLost:
            current = self.repository.get_job(job_id)
            if current["generation"] == generation and current["status"] in {"PENDING", "RUNNING"}:
                raise TagDerivationBusy("Tag creation awaits lease recovery") from None
        except Exception as exc:
            code = (
                "TAG_EPISODE_INTEGRITY_FAILED"
                if isinstance(
                    exc, (PackageConflict, SnapshotIntegrityError, DatasetIngestProjectionConflict)
                )
                else "ANNOTATION_SCHEMA_BINDING_MISSING"
                if isinstance(exc, AutomaticAnnotationBlocked)
                else "TAG_EPISODE_PROCESSING_FAILED"
            )
            logger.warning("Tag Episode creation failed (%s)", type(exc).__name__)
            with suppress(DeliveryLeaseLost):
                # A committed package can outlive a lost response. Expose that
                # fact on a failed job instead of implying nothing was created.
                try:
                    receipt = self.packages.repository.get(source.identity, source.data_version_id)
                except KeyError:
                    pass
                else:
                    self.repository.pin_data(row, receipt.data)
                self.repository.finish(row, error_code=code)


def worker_auth():
    organization, project, region = scope_key()
    return AuthContext(
        subject_id="tag-episode-worker",
        service_identity=True,
        capabilities=frozenset({"annotation_task.read", "export.download", "dataset_version.read"}),
        organization_ids=frozenset({organization}),
        project_ids=frozenset({project}),
        region_codes=frozenset({region}),
        scope_pairs=frozenset({(project, region)}),
        organization_scope_triples=frozenset({(organization, project, region)}),
    )


class TagDerivationOutboxHandler:
    EVENT_TYPE = "episode.tag.requested.v1"

    def __init__(self, service):
        self.service = service

    async def __call__(self, event):
        payload = event.payload
        if (
            event.event_type != self.EVENT_TYPE
            or event.schema_version != 1
            or event.aggregate_type != "tag_episode"
            or (event.organization_id, event.project_id, event.region_code) != scope_key()
            or set(payload) != {"job_id", "generation"}
            or type(payload["generation"]) is not int
            or payload["generation"] < 1
            or event.aggregate_id != payload["job_id"]
        ):
            raise ValueError("invalid Tag creation event")
        self.service.repository.validate_event(event)
        await asyncio.to_thread(self.service.generate, payload["job_id"], payload["generation"])
