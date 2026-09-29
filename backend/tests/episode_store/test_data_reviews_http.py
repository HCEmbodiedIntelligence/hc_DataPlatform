"""Normal sessions through the isolated gateway; no overridden auth dependencies."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from hc_data_platform.episode_store.commit import EpisodePackageCommitter
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_http_services import register_and_grant


@pytest.mark.integration
def test_real_review_api_permissions_retries_versions_and_pagination(services, package):
    repository, storage = services
    root, source = package
    ident = source.identity
    with scoped(source):
        committer = EpisodePackageCommitter(repository, storage)
        first_data = committer.commit(root, source).data
        changed_source = source.model_copy(update={"configuration_sha256": "c" * 64})
        second_data = committer.commit(root, changed_source).data
        with httpx.Client(base_url="http://gateway:8080", timeout=30, trust_env=False) as client:
            principal, headers = register_and_grant(
                client,
                repository,
                ident,
                capabilities=["dataset_version.read", "dataset_version.review"],
            )
            _, reader_headers = register_and_grant(
                client, repository, ident, capabilities=["dataset_version.read"]
            )
            base = (
                f"/api/v1/projects/{ident.project_id}/regions/{ident.region_code}"
                f"/datasets/{ident.dataset_id}/episodes/{ident.episode_id}/data-versions/"
            )
            path = base + first_data.data_version_id + "/data-reviews"
            command = {
                "expected_content_hash": first_data.content_hash,
                "expected_sequence": 0,
                "decision": "RETURNED",
                "comment": "Software fixture: task coverage requires rework.",
                "locations": [{"start_step": 0, "end_step": 2, "comment": "Coverage."}],
            }
            assert client.get(path).status_code == 401
            assert (
                client.post(
                    path,
                    json=command,
                    headers={**reader_headers, "Idempotency-Key": "no-permission"},
                ).status_code
                == 403
            )
            page = client.get(path, headers=reader_headers)
            assert page.status_code == 200, page.text
            assert page.json()["status"] == "NOT_REVIEWED"
            assert page.json()["allowed_actions"][0]["denied_reason"] == "MISSING_CAPABILITY"
            assert client.post(path, json=command, headers=headers).status_code == 422

            # Every actual HTTP request checks the session, capabilities and full tenant scope.
            def retry(_):
                response = client.post(
                    path, json=command, headers={**headers, "Idempotency-Key": "review-first"}
                )
                assert response.status_code == 201, response.text
                return response.json()

            with ThreadPoolExecutor(max_workers=4) as pool:
                responses = list(pool.map(retry, range(4)))
            first = responses[0]
            assert all(item == first for item in responses)
            assert first["actor_id"] == principal and first["sequence"] == 1
            assert first["data"] == first_data.model_dump(mode="json")
            for body, key, code in (
                ({**command, "comment": "changed"}, "review-first", "IDEMPOTENCY_CONFLICT"),
                (command, "another", "SEQUENCE_CONFLICT"),
                ({**command, "expected_content_hash": "0" * 64}, "wrong-hash", "CONTENT_CONFLICT"),
            ):
                response = client.post(path, json=body, headers={**headers, "Idempotency-Key": key})
                assert response.status_code == 409, response.text
                assert response.json()["code"] == "EPISODE_DATA_REVIEW_" + code
            tagged = {
                **command,
                "expected_sequence": 1,
                "locations": [
                    {
                        "comment": "cannot read tags",
                        "tag": {
                            "task_id": "unknown",
                            "revision": 1,
                            "revision_content_hash": "0" * 64,
                            "annotation_id": "unknown",
                        },
                    }
                ],
            }
            assert (
                client.post(
                    path, json=tagged, headers={**headers, "Idempotency-Key": "tagged"}
                ).status_code
                == 403
            )
            newer_command = {
                **command,
                "expected_sequence": 1,
                "decision": "APPROVED",
                "comment": "Software fixture: capture rechecked.",
            }
            response = client.post(
                path, json=newer_command, headers={**headers, "Idempotency-Key": "review-second"}
            )
            assert response.status_code == 201, response.text
            newer = response.json()
            assert newer["previous_review_id"] == first["review_id"]
            page = client.get(path, headers=headers, params={"limit": 1})
            assert page.status_code == 200 and page.json()["items"] == [newer]
            assert page.json()["status"] == "APPROVED" and page.json()["next_before"] == 2
            older_page = client.get(path, headers=headers, params={"before": 2, "limit": 1})
            assert older_page.json()["items"] == [first]
            assert older_page.json()["latest"] == newer
            assert retry(0) == first
            other = client.get(
                base + second_data.data_version_id + "/data-reviews", headers=headers
            )
            assert other.status_code == 200 and other.json()["status"] == "NOT_REVIEWED"
            assert other.json()["data"] == second_data.model_dump(mode="json")
            assert (
                client.get(path, headers={**headers, "X-Organization-Id": "foreign"}).status_code
                == 403
            )
            assert (
                client.get(
                    path.replace("/regions/r/", "/regions/foreign/"),
                    headers={**headers, "X-Region-Code": "foreign"},
                ).status_code
                == 404
            )
            assert (
                client.get(
                    path.replace(ident.episode_id, "absent-episode"), headers=headers
                ).status_code
                == 404
            )
            with repository.transaction() as cursor:
                cursor.execute(
                    """UPDATE access_control.capability_grants SET active=false
                    WHERE principal_id=%s AND capability_key='dataset_version.review'""",
                    (principal,),
                )
            # Even an idempotent retry re-authorizes; a previous success grants no permission.
            assert (
                client.post(
                    path, json=command, headers={**headers, "Idempotency-Key": "review-first"}
                ).status_code
                == 403
            )
            assert (
                client.get(path, headers=headers).json()["allowed_actions"][0]["allowed"] is False
            )
            assert client.get(path, headers=reader_headers).json()["items"] == [newer, first]
            result = {
                "scope": "synthetic actual gateway/API/PostgreSQL software verification",
                "data": first_data.model_dump(mode="json"),
                "second_version": second_data.data_version_id,
                "first_review": first["review_id"],
                "second_review": newer["review_id"],
                "normal_session_authorization": True,
                "concurrent_retry_same_record": True,
                "stale_sequence_and_changed_key_conflicts": True,
                "history_pagination": True,
                "different_version_not_reviewed": True,
                "foreign_scope_denied": True,
                "tag_links_require_annotation_read": True,
                "revoked_retry_denied": True,
                "real_recording": False,
                "human_acceptance": False,
            }
            evidence = Path("/app/.g1-artifacts/data-reviews-http.json")
            evidence.parent.mkdir(exist_ok=True)
            evidence.write_text(json.dumps(result, indent=2) + "\n")
            for session_headers in (headers, reader_headers):
                assert (
                    client.post("/api/v1/auth/session:logout", headers=session_headers).status_code
                    == 204
                )
