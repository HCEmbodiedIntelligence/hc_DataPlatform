"""Fixed-version aggregation through real isolated SQL, object store and gateway."""

import json
import shutil
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
import pytest

from hc_data_platform.annotation.models import ReviewDecision
from hc_data_platform.annotation.service import InvalidAnnotationStateError
from hc_data_platform.episode_store.commit import EpisodePackageCommitter
from hc_data_platform.episode_store.detail import read_detail
from hc_data_platform.episode_store.review_models import SubmitEpisodeDataReview
from hc_data_platform.security import AuthContext
from tests.episode_store.test_annotation_bindings_services import actor, publish_schema
from tests.episode_store.test_annotation_delivery_jobs_services import (  # noqa: F401
    delivery,
    selection,
)
from tests.episode_store.test_annotation_revision_files_services import files  # noqa: F401
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_http_services import register_and_grant


def auth_for(data, *, annotations=True):
    ident = data.identity
    return AuthContext(
        subject_id="detail-reviewer",
        service_identity=True,
        organization_ids=frozenset({ident.organization_id}),
        project_ids=frozenset({ident.project_id}),
        region_codes=frozenset({ident.region_code}),
        scope_pairs=frozenset({(ident.project_id, ident.region_code)}),
        organization_scope_triples=frozenset({ident.scope}),
        capabilities=frozenset(
            {"dataset_version.read", "export.download", "dataset_version.review"}
            | ({"annotation_task.read"} if annotations else set())
        ),
    )


@pytest.mark.integration
def test_overview_keeps_three_reviews_and_historical_approval_independent(target, tmp_path):
    packages, source, annotation, automatic, request = target
    data = request.data_ref
    ident = data.identity
    with scoped(source):
        package = packages.files.open(ident, data.data_version_id)
        auth = auth_for(data)
        initial = read_detail(packages, package, auth)
        assert initial.annotation.availability == "NOT_CREATED"
        assert initial.data_review_status == "NOT_REVIEWED"
        assert initial.quality.status == "UNKNOWN"
        assert initial.subtasks["segment_count"] is None
        assert len(initial.cameras) == 2
        assert all(c.preview_status == "READY" for c in initial.cameras)
        depth = next(c.depth for c in initial.cameras if c.depth)
        assert depth["source_dtypes"] == ["<u2"]
        assert depth["source_scales_m_per_unit"] == [0.001]
        assert depth["video_encoding"]["codec_profile"] == "Rext"
        assert depth["invalid_pixel_ratio"] == 1 / 256
        assert initial.source["parent_duration_reason"] == "NOT_RECORDED"
        publish_schema(source, annotation)
        task = automatic.ensure_task(request)
        annotation.claim(task.task_id, actor(ident))
        submission = annotation.submit_for_review(
            task.task_id,
            actor(ident),
            expected_revision=0,
            if_match=annotation.get_task(task.task_id).etag,
            idempotency_key="detail-submit",
        )
        annotation.review(
            task.task_id,
            actor(ident, "other-reviewer"),
            ReviewDecision.APPROVE,
            revision=0,
            submission_id=submission.submission_id,
            if_match=annotation.get_task(task.task_id).etag,
            comment="Software fixture review, not human acceptance",
        )
        approved = read_detail(packages, package, auth)
        assert approved.annotation.latest_submission["submission_id"] == submission.submission_id
        assert approved.annotation.latest_approval["revision"] == 0
        assert approved.annotation.current_approved_review_id is not None
        assert approved.data_review_status == "NOT_REVIEWED"
        packages.data_reviews.submit(
            ident,
            data.data_version_id,
            SubmitEpisodeDataReview(
                expected_content_hash=data.content_hash,
                expected_sequence=0,
                decision="RETURNED",
                comment="Capture compliance differs from label correctness",
            ),
            actor_id="capture-reviewer",
            idempotency_key="detail-review",
        )
        returned = read_detail(packages, package, auth)
        assert returned.data_review_status == "RETURNED"
        assert (
            returned.annotation.current_approved_review_id
            == approved.annotation.current_approved_review_id
        )
        # Existing approval locks remain in force; this read API must not reopen them.
        with pytest.raises(InvalidAnnotationStateError):
            annotation.restore_revision(
                task.task_id,
                actor(ident),
                target_revision=0,
                expected_revision=0,
                if_match=annotation.get_task(task.task_id).etag,
                client_mutation_id="cannot-reopen",
            )
        second_root = tmp_path / "second"
        for entry in package.manifest.files:
            dest = second_root / entry.path
            dest.parent.mkdir(parents=True, exist_ok=True)
            with package.file(entry.path) as path:
                shutil.copyfile(path, dest)
        second = packages.committer.commit(
            second_root, source.model_copy(update={"configuration_sha256": "f" * 64})
        )
        other = read_detail(packages, packages.files.open(ident, second.data.data_version_id), auth)
        assert other.annotation.availability == "NOT_CREATED"
        assert other.data_review_status == "NOT_REVIEWED" and other.latest_data_review is None
        assert all(c.preview_status == "NOT_GENERATED" for c in other.cameras)
        forbidden = read_detail(packages, package, auth_for(data, annotations=False))
        assert forbidden.annotation.availability == "FORBIDDEN"
        assert forbidden.annotation.current_revision is None and not forbidden.deliveries
        assert forbidden.latest_data_review == returned.latest_data_review


@pytest.mark.integration
def test_reworked_draft_does_not_replace_prior_submission(target):
    packages, source, annotation, automatic, request = target
    ident = source.identity
    with scoped(source):
        publish_schema(source, annotation)
        task = automatic.ensure_task(request)
        annotation.claim(task.task_id, actor(ident))
        submitted = annotation.submit_for_review(
            task.task_id,
            actor(ident),
            expected_revision=0,
            if_match=annotation.get_task(task.task_id).etag,
            idempotency_key="detail-return",
        )
        annotation.review(
            task.task_id,
            actor(ident, "reviewer"),
            ReviewDecision.NEEDS_REVISION,
            revision=0,
            submission_id=submitted.submission_id,
            if_match=annotation.get_task(task.task_id).etag,
        )
        annotation.save_draft(
            task.task_id,
            actor(ident),
            (),
            expected_revision=0,
            if_match=annotation.get_task(task.task_id).etag,
            client_mutation_id="new-draft",
        )
        detail = read_detail(
            packages,
            packages.files.open(ident, request.data_ref.data_version_id),
            auth_for(request.data_ref),
        )
        assert detail.annotation.current_revision.revision == 1
        assert detail.annotation.latest_submission["revision"] == 0
        assert detail.annotation.latest_review["decision"] == "NEEDS_REVISION"
        assert (
            detail.annotation.has_unsubmitted_changes and detail.annotation.latest_approval is None
        )


@pytest.mark.integration
def test_overview_file_pages_and_download_authorize_exact_version(services, package, tmp_path):
    repository, storage = services
    root, source = package
    ident = source.identity
    with scoped(source):
        data = EpisodePackageCommitter(repository, storage).commit(root, source).data
        with httpx.Client(base_url="http://gateway:8080", timeout=45, trust_env=False) as client:
            principal, headers = register_and_grant(client, repository, ident)
            base = (
                f"/api/v1/projects/{ident.project_id}/regions/{ident.region_code}/datasets/"
                f"{ident.dataset_id}/episodes/{ident.episode_id}/data-versions/{data.data_version_id}"
            )
            response = client.get(base + "/overview", headers=headers)
            assert response.status_code == 200, response.text
            overview = response.json()
            assert overview["data"] == data.model_dump(mode="json")
            assert overview["annotation"]["availability"] == "FORBIDDEN"
            assert overview["data_review_status"] == "NOT_REVIEWED"
            assert overview["task"]["requirements_status"] == "NOT_RECORDED"
            cursor = None
            files = []
            while True:
                response = client.get(
                    base + "/files",
                    headers=headers,
                    params={"limit": 3, **({"after": cursor} if cursor else {})},
                )
                assert response.status_code == 200, response.text
                page = response.json()
                files.extend(page["items"])
                cursor = page["next_after"]
                if cursor is None:
                    break
            assert len({f["file_id"] for f in files}) == page["total"] == len(files)
            for file in files:
                response = client.get(base + f"/files/{file['file_id']}/download", headers=headers)
                assert response.status_code == 200, response.text
                assert sha256(response.content).hexdigest() == file["sha256"]
                assert len(response.content) == file["size"]
            path = base + f"/files/{files[0]['file_id']}/download"
            authorize_path = base + f"/files/{files[0]['file_id']}/authorize"
            signed = client.post(authorize_path, headers=headers)
            assert signed.status_code == 200, signed.text
            assert signed.headers["cache-control"] == "no-store"
            grant = signed.json()
            assert grant["data"] == data.model_dump(mode="json")
            assert grant["sha256"] == files[0]["sha256"]
            with httpx.Client(trust_env=False) as downloader:
                url = urlsplit(grant["url"])
                artifact = downloader.get(
                    urlunsplit((url.scheme, "object-store-browser:9000", url.path, url.query, "")),
                    headers={"Host": url.netloc},
                )
            assert artifact.status_code == 200
            assert artifact.headers["content-disposition"].startswith("attachment;")
            assert sha256(artifact.content).hexdigest() == grant["sha256"]
            assert client.post(authorize_path).status_code == 401
            assert (
                client.post(
                    authorize_path, headers={**headers, "X-Organization-Id": "foreign"}
                ).status_code == 403
            )
            assert client.get(path).status_code == 401
            assert (
                client.get(path, headers={**headers, "X-Organization-Id": "foreign"}).status_code
                == 403
            )
            assert (
                client.get(base + "/files/" + "0" * 64 + "/download", headers=headers).status_code
                == 404
            )
            assert (
                client.get(base + "/files", headers=headers, params={"after": "0" * 64}).status_code
                == 422
            )
            assert (
                client.get(
                    base + "/overview", headers=headers, params={"delivery_id": "ad_" + "0" * 64}
                ).status_code
                == 403
            )
            with repository.transaction() as db:
                db.execute(
                    "UPDATE access_control.capability_grants SET active=false "
                    "WHERE principal_id=%s AND capability_key='export.download'",
                    (principal,),
                )
            assert client.get(path, headers=headers).status_code == 403
            assert client.post(authorize_path, headers=headers).status_code == 403
            view = client.get(base + "/overview", headers=headers).json()
            assert not next(
                a for a in view["allowed_actions"] if a["action"] == "DOWNLOAD_COMPLETE"
            )["allowed"]
            assert all(not f["download"]["allowed"] for f in view["files"]["items"])
            with repository.transaction() as db:
                db.execute(
                    "SELECT count(*) FROM core.audit_events WHERE organization_id=%s "
                    "AND action='episode.file.authorized'",
                    (ident.organization_id,),
                )
                assert db.fetchone()[0] == len(files) + 1
            Path("/app/.g1-artifacts/episode-detail-http.json").write_text(
                json.dumps(
                    {
                        "data": data.model_dump(mode="json"),
                        "files_verified": len(files),
                        "real_recording": False,
                        "human_acceptance": False,
                        "ordinary_session": True,
                        "revoked_cached_file_denied": True,
                        "overview": overview,
                    },
                    indent=2,
                )
                + "\n"
            )
            assert client.post("/api/v1/auth/session:logout", headers=headers).status_code == 204


@pytest.mark.integration
def test_subtask_count_uses_selected_delivery_track_only(delivery):  # noqa: F811
    source, task, service, auth = delivery
    with scoped(source):
        job = service.request(task.task_id, selection(), "detail-track", auth)
        data = job.plan.binding.data
        package = service.packages.files.open(source.identity, data.data_version_id)
        before = read_detail(service.packages, package, auth)
        assert before.subtasks["status"] == "NOT_SPECIFIED"
        assert before.subtasks["segment_count"] is None
        selected = read_detail(service.packages, package, auth, delivery_id=job.job_id)
        assert selected.subtasks["segment_count"] == 1
        assert selected.subtasks["track"]["annotation_ids"] == ["parent"]
        assert selected.subtasks["revision"] == 1
        assert selected.deliveries[0]["review"]["status"] == "UNREVIEWED"
        with pytest.raises(KeyError):
            read_detail(service.packages, package, auth, delivery_id="ad_" + "0" * 64)


@pytest.mark.integration
def test_missing_file_and_preview_are_explicit_without_generation(target, monkeypatch):
    packages, source, _, _, request = target
    data = request.data_ref
    with scoped(source):
        package = packages.files.open(source.identity, data.data_version_id)
        before = read_detail(packages, package, auth_for(data))
        video = before.cameras[0].feature
        preview_key = packages.previews._row(data, video)[2]["object_key"]
        data_path = next(e.path for e in package.manifest.files if e.path.startswith("data/"))
        absent = {preview_key, f"{data.package_prefix}/{data_path}"}
        head = package.storage.head
        # Fault injection only: no committed object is overwritten or deleted.
        monkeypatch.setattr(
            package.storage, "head", lambda key: None if key in absent else head(key)
        )

        def cannot_generate(*args, **kwargs):
            raise AssertionError("a read attempted to generate a preview")

        monkeypatch.setattr(packages.previews, "prepare", cannot_generate)
        view = read_detail(packages, package, auth_for(data))
        assert (
            next(c for c in view.cameras if c.feature == video).preview_status == "INTEGRITY_FAILED"
        )
        missing = next(f for f in view.files.items if f.path == data_path)
        assert missing.availability == "MISSING" and not missing.download.allowed
        assert not next(a for a in view.allowed_actions if a.action == "DOWNLOAD_COMPLETE").allowed


@pytest.mark.integration
def test_multiple_bound_tasks_require_explicit_selection(target, tmp_path):
    packages, source, annotation, automatic, request = target
    with scoped(source):
        publish_schema(source, annotation)
        first = automatic.ensure_task(request)
        package = packages.files.open(source.identity, request.data_ref.data_version_id)
        # A second real collection retains this Episode and adds another. The
        # database must continue rejecting invented collection version numbers.
        second_source = source.model_copy(
            update={
                "identity": source.identity.model_copy(
                    update={"episode_id": "another-episode", "rollout_id": "another-rollout"}
                )
            }
        )
        second_root = tmp_path / "another-episode"
        for entry in package.manifest.files:
            dest = second_root / entry.path
            dest.parent.mkdir(parents=True, exist_ok=True)
            with package.file(entry.path) as path:
                shutil.copyfile(path, dest)
        origin_path = second_root / "hc/source.json"
        origin = json.loads(origin_path.read_text())
        origin["identity"] = second_source.identity.model_dump(mode="json")
        origin_path.write_text(json.dumps(origin))
        receipt = packages.committer.commit(second_root, second_source)
        collection = packages.collections.publish(
            receipt.data,
            packages.schemas.schema_for(source.identity.project_id, source.identity.dataset_id),
        )
        assert request.data_ref in collection.episodes
        second = automatic.ensure_task(
            replace(
                request,
                dataset_version=collection.version,
                source_workflow_id=request.source_workflow_id + "-another-collection",
            )
        )
        auth = auth_for(request.data_ref)
        view = read_detail(packages, package, auth)
        assert view.annotation.availability == "SELECTION_REQUIRED"
        assert {o["task_id"] for o in view.annotation.task_options} == {
            first.task_id,
            second.task_id,
        }
        assert view.annotation.current_revision is None
        selected = read_detail(packages, package, auth, annotation_task_id=first.task_id)
        assert selected.annotation.task_id == first.task_id
        with pytest.raises(KeyError):
            read_detail(packages, package, auth, annotation_task_id="foreign")
