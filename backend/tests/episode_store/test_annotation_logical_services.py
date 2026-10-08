"""Ordinary task -> immutable logical snapshot -> actual Lance/Parquet/media reads."""

import hashlib
from uuid import uuid4

import httpx
import pytest

from tests.episode_store.test_annotation_bindings_services import actor
from tests.episode_store.test_collection_releases_services import release_source  # noqa: F401
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_http_services import register_and_grant
from tests.episode_store.test_migration_annotations_services import annotation_source  # noqa: F401
from tests.episode_store.test_migration_batches_services import batch_source  # noqa: F401


@pytest.mark.integration
@pytest.mark.parametrize("batch_source", [{"logical_media": True}], indirect=True)
@pytest.mark.parametrize("kind", ["episode", "lance"])
def test_task_logical_selection_reads_fixed_samples_media_and_reauthorizes(release_source, kind):  # noqa: F811
    source, packages, _, target_task = release_source
    _, plan, annotations, legacy_task, _, _ = source
    task_id = target_task if kind == "episode" else legacy_task
    ident = plan.source.identity
    with (
        scoped(plan.source),
        httpx.Client(base_url="http://gateway:8080", trust_env=False, timeout=60) as client,
    ):
        principal, headers = register_and_grant(
            client,
            packages.repository,
            ident,
            capabilities=["annotation_task.read", "dataset_version.read", "episode.read"],
        )
        path = (
            f"/api/v1/projects/{ident.project_id}/regions/{ident.region_code}"
            f"/annotation-tasks/{task_id}/logical"
        )
        result = client.get(path, headers=headers)
        assert result.status_code == 200, result.text
        assert result.headers["cache-control"] == "no-store"
        view = result.json()
        assert view["task_id"] == task_id and view["snapshot"]["episode_id"] == ident.episode_id
        selection = view["snapshot"]["selection"]
        assert selection["version_id"].startswith(
            "version_cv_" if kind == "episode" else "version_lance_"
        )
        root = (
            f"/api/v1/projects/{ident.project_id}/regions/{ident.region_code}/datasets/{ident.dataset_id}"
            f"/versions/{selection['version_id']}/episode-revisions/{selection['revision_id']}/logical"
        )
        assert (
            client.get(
                root, headers=headers, params={"snapshot_token": view["snapshot_token"]}
            ).json()["snapshot_id"]
            == view["snapshot_id"]
        )
        read = {"snapshot_token": view["snapshot_token"], "snapshot_id": view["snapshot_id"]}
        numeric = media = 0
        for stream in view["snapshot"]["streams"]:
            url = root + f"/streams/{stream['stream_id']}"
            samples = client.get(
                url + "/samples", headers=headers, params={**read, "start": 0, "end": 4}
            )
            assert samples.status_code == 200, samples.text
            assert len(samples.json()["samples"]) == 4
            if stream["name"] == "action":
                assert [s["value"] for s in samples.json()["samples"]] == [
                    [float(i)] for i in range(4)
                ]
                numeric += 1
            if stream["value_kind"] is None:
                grant = client.post(url + "/media/authorize", headers=headers, params=read)
                assert grant.status_code == 200, grant.text
                data = grant.json()
                content = client.get(data["media_url"])
                assert content.status_code == 200 and content.content
                if data["media_sha256"] is not None:
                    assert hashlib.sha256(content.content).hexdigest() == data["media_sha256"]
                media += 1
        assert numeric == 1 and media == 2
        task = annotations.get_task(task_id)
        editor = actor(ident, task.assignee_id or "release-editor")
        history = annotations.get_history(task_id, editor)
        annotations.save_draft(
            task_id,
            editor,
            (),
            tags=history.revisions[task.current_revision].tags,
            expected_revision=task.current_revision,
            if_match=task.etag,
            client_mutation_id=uuid4().hex,
        )
        revised = client.get(path, headers=headers)
        assert revised.status_code == 200 and revised.json()["snapshot_id"] == view["snapshot_id"]
        assert revised.json()["snapshot"] == view["snapshot"]
        assert client.get(path).status_code == 401
        assert (
            client.get(path, headers={**headers, "X-Organization-Id": "foreign"}).status_code == 403
        )
        assert client.get(path.replace(task_id, "not-present"), headers=headers).status_code == 404
        with packages.repository.transaction() as cursor:
            cursor.execute(
                "UPDATE access_control.capability_grants SET active=false "
                "WHERE principal_id=%s AND capability_key='annotation_task.read'",
                (principal,),
            )
        assert client.get(path, headers=headers).status_code == 403
        with packages.repository.transaction() as cursor:
            cursor.execute(
                "UPDATE access_control.capability_grants SET active=true "
                "WHERE principal_id=%s AND capability_key='annotation_task.read'",
                (principal,),
            )
            cursor.execute(
                "UPDATE access_control.capability_grants SET active=false "
                "WHERE principal_id=%s AND capability_key='dataset_version.read'",
                (principal,),
            )
        assert client.get(path, headers=headers).status_code == 403
        assert (
            client.get(
                root, headers=headers, params={"snapshot_token": view["snapshot_token"]}
            ).status_code
            == 403
        )
