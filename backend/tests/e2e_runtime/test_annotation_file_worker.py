"""Isolated real gateway + authenticated API + running worker, with synthetic RGB-D.

Explicitly separate from the deterministic fault tests: do not concurrently drain
their outbox with a live worker. This does not count as real-sensor/human acceptance.
"""

import hashlib
import json
import os
import time
from pathlib import Path

import httpx
import pytest

from tests.episode_store.test_annotation_bindings_services import target  # noqa: F401
from tests.episode_store.test_annotation_revision_files_services import files  # noqa: F401
from tests.episode_store.test_bundle_assets_services import prepared_bundle  # noqa: F401
from tests.episode_store.test_commit_services import package, scoped, services  # noqa: F401
from tests.episode_store.test_http_services import register_and_grant


@pytest.mark.integration
def test_running_worker_and_authenticated_revision_download(request):
    if os.environ.get("HC_G1_LIVE_SNAPSHOT_WORKER") != "1":
        pytest.skip("explicit isolated worker proof flag required")
    source, task, service, _ = request.getfixturevalue("files")
    with (
        scoped(source),
        httpx.Client(base_url="http://gateway:8080", timeout=15, trust_env=False) as client,
    ):
        principal, headers = register_and_grant(
            client,
            service.repository,
            source.identity,
            capabilities=["annotation_task.read", "annotation.save", "export.download"],
        )
        path = f"/api/v1/annotation-tasks/{task.task_id}"
        assert client.get(path + "/revisions/1/file").status_code == 401

        def ready(generation):
            deadline = time.monotonic() + 90
            last = None
            while time.monotonic() < deadline:
                result = client.get(path + "/revisions/1/file", headers=headers)
                assert result.status_code == 200, result.text
                last = result.json()
                assert last["status"] != "FAILED", last
                if last["status"] == "READY" and last["generation"] == generation:
                    return last
                time.sleep(0.5)
            raise AssertionError(f"running worker did not generate the file: {last}")

        first = ready(1)
        assert client.get(path + "/annotation-file", headers=headers).status_code == 409
        assert client.get(path + "/annotation-file?revision=1", headers=headers).status_code == 409
        download = client.get(
            path + "/annotation-file?revision=1&allow_unreviewed=true", headers=headers
        )
        assert download.status_code == 200, download.text
        assert download.headers["x-annotation-review-status"] == "unreviewed"
        assert hashlib.sha256(download.content).hexdigest() == first["content_sha256"]
        assert download.json()["data_binding"]["data_ref"] == task.data_ref.model_dump(mode="json")
        assert (
            client.get(
                path + "/revisions/1/file", headers={**headers, "X-Organization-Id": "foreign"}
            ).status_code
            == 403
        )
        regenerate = client.post(
            path + "/revisions/1/file", headers=headers, json={"expected_generation": 1}
        )
        assert regenerate.status_code == 200, regenerate.text
        second = ready(2)
        assert second["content_sha256"] == first["content_sha256"]
        with service.repository.transaction() as cursor:
            cursor.execute(
                """SELECT count(*) FROM core.outbox_events
                WHERE envelope->'payload'->>'task_id'=%s
                  AND event_type='annotation.revision_file.requested.v1'
                  AND published_at IS NOT NULL""",
                (task.task_id,),
            )
            published = cursor.fetchone()[0]
            assert published == 3  # revision 0 + revision 1 + generation 2
            cursor.execute(
                "UPDATE access_control.capability_grants SET active=false WHERE principal_id=%s",
                (principal,),
            )
        assert (
            client.get(
                path + "/annotation-file?revision=1&allow_unreviewed=true", headers=headers
            ).status_code
            == 403
        )
        assert (
            client.post(
                path + "/revisions/1/file", headers=headers, json={"expected_generation": 2}
            ).status_code
            == 403
        )
        assert client.post("/api/v1/auth/session:logout", headers=headers).status_code == 204
        evidence = {
            "scope": ("isolated real gateway/session/API/outbox-worker/PostgreSQL/MinIO; "
                      "synthetic RGB-D"),
            "data_ref": task.data_ref.model_dump(mode="json"),
            "task_id": task.task_id,
            "revision": 1,
            "generations": [first, second],
            "published_events": published,
            "default_unapproved_download_denied": True,
            "explicit_unreviewed_download": True,
            "same_revision_bytes_after_regeneration": True,
            "foreign_org_denied": True,
            "revoked_download_and_retry_denied": True,
            "human_acceptance": False,
        }
        Path("/app/.g1-artifacts/annotation-files-worker.json").write_text(
            json.dumps(evidence, indent=2) + "\n"
        )
