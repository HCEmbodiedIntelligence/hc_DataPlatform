"""Actual PG/MinIO/outbox primary commands, with explicitly synthetic media."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import psycopg
import pytest

from hc_data_platform.annotation.delivery import AnnotationDeliveryService
from hc_data_platform.annotation.delivery_repository import PostgresAnnotationDeliveries
from hc_data_platform.core.artifact_tasks import DeliveryLeaseLost
from hc_data_platform.core.context import (
    bind_request_context,
    current_request_context,
    reset_request_context,
)
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.episode_store.derivation_jobs import RequestTagEpisode
from hc_data_platform.episode_store.derivation_repository import (
    PostgresTagDerivations,
    TagDerivationBusy,
)
from hc_data_platform.episode_store.derivation_service import (
    TagDerivationOutboxHandler,
    TagDerivationService,
)
from tests.episode_store.test_annotation_revision_files_services import files  # noqa: F401
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_tag_derivation_services import selected  # noqa: F401
from tests.episode_store.test_tag_projection_services import projected  # noqa: F401

pytestmark = [
    pytest.mark.integration,
    pytest.mark.parametrize(
        "prepared_bundle",
        [{"identity": {"dataset_id": "dataset_tag_jobs", "episode_id": "episode_tag_parent"}}],
        indirect=True,
    ),
]


@pytest.fixture
def tag_jobs(projected, files):  # noqa: F811
    source, packages, _, _, _ = projected
    _, task, revision_files, auth = files
    repository = PostgresTagDerivations(packages.repository.connections)
    annotations = AnnotationDeliveryService(
        revision_files, packages, PostgresAnnotationDeliveries(packages.repository.connections)
    )
    service = TagDerivationService(packages, annotations, repository)
    auth = replace(auth, capabilities=auth.capabilities | {"upload.manage", "dataset_version.read"})
    return source, service, task.task_id, auth


def event_for(service, job):
    with service.repository.transaction() as cursor:
        cursor.execute(
            """SELECT envelope FROM core.outbox_events WHERE event_type=%s
        AND envelope->'payload'->>'job_id'=%s AND envelope->'payload'->>'generation'=%s""",
            (TagDerivationOutboxHandler.EVENT_TYPE, job.job_id, str(job.generation)),
        )
        rows = cursor.fetchall()
        assert len(rows) == 1
        return DomainEventEnvelope.model_validate(rows[0][0])


def run_job(service, job):
    asyncio.run(TagDerivationOutboxHandler(service)(event_for(service, job)))


def request(service, task_id, auth, **changes):
    return service.request(
        task_id,
        RequestTagEpisode(revision=1, annotation_id="child", allow_unreviewed=True, **changes),
        "test-tag",
        auth,
    )


def test_concurrent_command_freezes_once_and_outbox_creates_new_primary(tag_jobs, monkeypatch):
    source, service, task_id, auth = tag_jobs

    def create(_):
        with scoped(source):
            return request(service, task_id, auth)

    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = tuple(pool.map(create, range(4)))
    assert all(j == jobs[0] for j in jobs)
    job = jobs[0]
    with scoped(source):
        event = event_for(service, job)
        assert event.payload == {"job_id": job.job_id, "generation": 1}
        run_job(service, job)
        ready = service.status(task_id, job.job_id, auth)
        assert ready.status == "READY", ready
        assert ready.committed_data.identity != source.identity
        assert ready.committed_data.frame_count == 3
        assert ready.annotation_task_id != task_id
        assert ready.viewer_target.episode_id == ready.committed_data.identity.episode_id
        assert ready.plan == job.plan
        assert service.list(task_id, auth) == (ready,)
        with service.packages.repository.transaction() as cursor:
            cursor.execute(
                """SELECT count(*) FROM episode_store.qc_evidence
                WHERE organization_id=%s AND project_id=%s AND region_code=%s AND rollout_id=%s""",
                (*source.identity.scope, ready.committed_data.identity.rollout_id),
            )
            assert cursor.fetchone()[0] == 1
        with monkeypatch.context() as guard:
            guard.setattr(
                service,
                "freeze",
                lambda *a, **k: pytest.fail("idempotent request refroze selection"),
            )
            guard.setattr(
                "hc_data_platform.episode_store.derivation_service.write_tag_episode",
                lambda *a, **k: pytest.fail("READY job rebuilt"),
            )
            assert request(service, task_id, auth) == ready
            run_job(service, job)
        assert service.status(task_id, job.job_id, auth) == ready
        with pytest.raises(ProblemException) as conflict:
            request(service, task_id, auth, display_name="different selection")
        assert conflict.value.problem.code == "TAG_EPISODE_IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize(
    "point",
    ["before_commit", "lost_commit", "quality", "preview", "product", "annotation", "lost_finish"],
)
def test_failure_recovers_same_plan_without_rebuilding_committed_data(tag_jobs, monkeypatch, point):
    source, service, task_id, auth = tag_jobs
    with scoped(source):
        job = request(service, task_id, auth)
        with monkeypatch.context() as fault:

            def interrupted(*args, **kwargs):
                raise InterruptedError("synthetic Tag publication fault")

            if point == "before_commit":
                fault.setattr(service.packages.committer, "commit", interrupted)
            elif point == "lost_commit":
                original = service.packages.committer.commit

                def lost(*a, **k):
                    original(*a, **k)
                    interrupted()

                fault.setattr(service.packages.committer, "commit", lost)
            elif point == "quality":
                fault.setattr(service.packages.quality_evidence, "put", interrupted)
            elif point == "preview":
                fault.setattr(service.packages.previews, "prepare_all", interrupted)
            elif point == "product":
                fault.setattr(service.projector, "_put_schema", interrupted)
            elif point == "annotation":
                original = service.automatic._repository.create_automatic

                def lost(*a, **k):
                    original(*a, **k)
                    interrupted()

                fault.setattr(service.automatic._repository, "create_automatic", lost)
            else:
                original = service.repository.finish

                def lost(*a, **k):
                    original(*a, **k)
                    interrupted()

                fault.setattr(service.repository, "finish", lost)
            run_job(service, job)
        state = service.status(task_id, job.job_id, auth)
        if point == "lost_finish":
            assert state.status == "READY"
            run_job(service, job)
            return
        assert state.status == "FAILED", state
        assert (state.committed_data is None) == (point == "before_commit")
        retry = service.command(task_id, job.job_id, state.generation, auth, retry=True)
        assert retry.generation == 2 and retry.plan == job.plan
        with pytest.raises(ProblemException) as cancel:
            service.command(task_id, job.job_id, 2, auth, retry=False)
        assert cancel.value.problem.code == "TAG_EPISODE_CANCEL_NOT_ALLOWED"
        if state.committed_data is not None:
            monkeypatch.setattr(
                "hc_data_platform.episode_store.derivation_service.write_tag_episode",
                lambda *a, **k: pytest.fail("committed data rebuilt"),
            )
        run_job(service, retry)
        ready = service.status(task_id, job.job_id, auth)
        assert ready.status == "READY", ready
        assert ready.attempts == 2 and ready.plan == job.plan
        if state.committed_data is not None:
            assert ready.committed_data == state.committed_data


def test_cancel_only_before_first_claim_and_fence_expired_workers(tag_jobs):
    source, service, task_id, auth = tag_jobs
    with scoped(source):
        job = request(service, task_id, auth)
        cancelled = service.command(task_id, job.job_id, 1, auth, retry=False)
        assert cancelled.status == "CANCELLED"
        run_job(service, job)
        assert service.status(task_id, job.job_id, auth) == cancelled
        retry = service.command(task_id, job.job_id, 1, auth, retry=True)
        old = service.repository.claim(job.job_id, 2)
        with pytest.raises(TagDerivationBusy):
            service.repository.claim(job.job_id, 2)
        with service.repository.transaction() as cursor:
            cursor.execute(
                "UPDATE episode_store.tag_derivation_jobs "
                "SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",
                (job.job_id,),
            )
        new = service.repository.claim(job.job_id, 2)
        assert new["lease_token"] != old["lease_token"]
        with pytest.raises(DeliveryLeaseLost):
            service.repository.heartbeat(old)
        with pytest.raises(DeliveryLeaseLost):
            service.repository.finish(old, error_code="STALE_WORKER")
        with pytest.raises(ProblemException):
            service.command(task_id, job.job_id, retry.generation, auth, retry=False)
        service.repository.finish(new, error_code="SYNTHETIC_INTERRUPTION")
        retry = service.command(task_id, job.job_id, 2, auth, retry=True)
        run_job(service, retry)
        assert service.status(task_id, job.job_id, auth).status == "READY"


def test_crash_after_primary_commit_recovers_without_encoding(tag_jobs, monkeypatch):
    class ProcessCrash(BaseException):
        pass

    source, service, task_id, auth = tag_jobs
    with scoped(source):
        job = request(service, task_id, auth)
        with monkeypatch.context() as fault:
            original = service.packages.committer.commit

            def crash(*a, **k):
                original(*a, **k)
                raise ProcessCrash()

            fault.setattr(service.packages.committer, "commit", crash)
            with pytest.raises(ProcessCrash):
                run_job(service, job)
        assert service.status(task_id, job.job_id, auth).status == "RUNNING"
        with service.repository.transaction() as cursor:
            cursor.execute(
                "UPDATE episode_store.tag_derivation_jobs "
                "SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",
                (job.job_id,),
            )
        monkeypatch.setattr(
            "hc_data_platform.episode_store.derivation_service.write_tag_episode",
            lambda *a, **k: pytest.fail("crashed committed data rebuilt"),
        )
        run_job(service, job)
        ready = service.status(task_id, job.job_id, auth)
        assert ready.status == "READY" and ready.attempts == 2


def test_permissions_cached_state_and_event_integrity(tag_jobs):
    source, service, task_id, auth = tag_jobs
    with scoped(source):
        body = RequestTagEpisode(revision=1, annotation_id="child", allow_unreviewed=True)
        for capability in (
            "upload.manage",
            "dataset_version.read",
            "annotation_task.read",
            "export.download",
        ):
            denied = replace(auth, capabilities=auth.capabilities - {capability})
            with pytest.raises(ProblemException):
                service.request(task_id, body, "denied", denied)
        job = request(service, task_id, auth)
        event = event_for(service, job)
        forged = event.model_copy(update={"payload": {"job_id": job.job_id, "generation": 99}})
        with pytest.raises(ValueError):
            asyncio.run(TagDerivationOutboxHandler(service)(forged))
        assert service.status(task_id, job.job_id, auth).status == "PENDING"
        run_job(service, job)
        for capability in ("dataset_version.read", "annotation_task.read", "export.download"):
            denied = replace(auth, capabilities=auth.capabilities - {capability})
            with pytest.raises(ProblemException):
                service.status(task_id, job.job_id, denied)
            with pytest.raises(ProblemException):
                service.list(task_id, denied)
        with pytest.raises(ProblemException):
            service.status("different-task", job.job_id, auth)


def test_database_protects_plan_scope_and_primary_receipts(tag_jobs):
    source, service, task_id, auth = tag_jobs
    with scoped(source):
        job = request(service, task_id, auth)
        for assignment in (
            "plan=jsonb_set(plan,'{episode,display_name}','\"forged\"'::jsonb)",
            "committed_data=plan->'episode'->'parent'",
        ):
            with (
                pytest.raises(psycopg.errors.CheckViolation),
                service.repository.transaction() as cursor,
            ):
                cursor.execute(
                    "UPDATE episode_store.tag_derivation_jobs SET "
                    + assignment
                    + " WHERE job_id=%s",
                    (job.job_id,),
                )
        token = bind_request_context(replace(current_request_context(), service_identity=False))
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                service.repository.claim(job.job_id, 1)
        finally:
            reset_request_context(token)
        for field in ("organization_id", "project_id", "region_code"):
            token = bind_request_context(
                replace(current_request_context(), **{field: "other-scope"})
            )
            try:
                with pytest.raises(ProblemException) as absent:
                    service.repository.get_job(job.job_id)
                assert absent.value.problem.status == 404
            finally:
                reset_request_context(token)
        assert service.status(task_id, job.job_id, auth).status == "PENDING"
        run_job(service, job)
        with (
            pytest.raises(psycopg.errors.CheckViolation),
            service.repository.transaction() as cursor,
        ):
            cursor.execute(
                "UPDATE episode_store.tag_derivation_jobs "
                "SET updated_at=clock_timestamp() WHERE job_id=%s",
                (job.job_id,),
            )
