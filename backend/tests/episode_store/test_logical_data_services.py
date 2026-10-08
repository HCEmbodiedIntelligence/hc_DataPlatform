"""Ordinary HTTP reads against actual mixed PostgreSQL, Lance and Parquet data."""

import json
from pathlib import Path

import httpx
import pytest

from hc_data_platform.episode_store.models import DataVersionRef
from tests.episode_store.test_collection_releases_services import release_source  # noqa: F401
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_http_services import register_and_grant
from tests.episode_store.test_migration_annotations_services import annotation_source  # noqa: F401
from tests.episode_store.test_migration_batches_services import batch_source  # noqa: F401


@pytest.mark.integration
@pytest.mark.parametrize("batch_source", [{}, {"missing_camera": True}], indirect=True)
def test_real_logical_http_reads_native_and_legacy_with_identical_contract(release_source, request):  # noqa: F811
    source, packages, command, _ = release_source
    batches, plan, _, _, _, _ = source
    identity = plan.legacy.identity
    with (
        scoped(plan.source),
        httpx.Client(base_url="http://gateway:8080", timeout=45, trust_env=False) as client,
    ):
        principal, headers = register_and_grant(
            client,
            packages.repository,
            identity,
            capabilities=["dataset_version.read", "episode.read"],
        )
        versions = ("version_lance_2", "version_" + command.collection_version_id)
        root = f"/api/v1/projects/{identity.project_id}/datasets/{identity.dataset_id}"
        logical_root = (
            f"/api/v1/projects/{identity.project_id}/regions/{identity.region_code}"
            f"/datasets/{identity.dataset_id}"
        )
        seen, evidence = set(), []
        for version in versions:
            bootstrap = client.get(root + f"/versions/{version}/bootstrap", headers=headers)
            assert bootstrap.status_code == 200, bootstrap.text
            params = {"snapshot_token": bootstrap.json()["data"]["snapshot_token"]}
            with packages.repository.transaction() as cursor:
                cursor.execute(
                    """SELECT revision_document
                    FROM dataset_registry.dataset_version_episode_revisions
                    WHERE organization_id=%s AND project_id=%s AND region_code=%s
                      AND dataset_id=%s AND version_id=%s ORDER BY ordinal""",
                    (*identity.scope, identity.dataset_id, version),
                )
                revisions = [row[0] for row in cursor.fetchall()]
            assert revisions
            for revision in revisions:
                path = (
                    logical_root
                    + f"/versions/{version}/episode-revisions/"
                    + revision["revision_id"]
                    + "/logical"
                )
                result = client.get(path, headers=headers, params=params)
                assert result.status_code == 200, result.text
                assert result.headers["cache-control"] == "no-store"
                view = result.json()
                descriptor = view["snapshot"]
                assert descriptor["episode_id"] == revision["episode_id"]
                assert descriptor["content_sha256"] == revision["content_sha256"]
                assert client.get(path, params=params).status_code == 401
                assert (
                    client.get(
                        path, params=params, headers={**headers, "X-Organization-Id": "foreign"}
                    ).status_code
                    == 403
                )
                streams = {s["episode_stream_id"]: s for s in revision["streams"]}
                for stream in descriptor["streams"]:
                    stored = streams[stream["stream_id"]]
                    seen.add("episode" if stored.get("package_binding") else "lance")
                    assert stream["temporal"] is True and stream["sample_count"] == 4
                    sample_path = path + f"/streams/{stream['stream_id']}/samples"
                    query = {**params, "snapshot_id": view["snapshot_id"], "start": 0, "end": 4}
                    response = client.get(sample_path, headers=headers, params=query)
                    assert response.status_code == 200, (version, stored, response.text)
                    batch = response.json()
                    assert batch["snapshot_id"] == view["snapshot_id"]
                    assert [s["sample_index"] for s in batch["samples"]] == list(range(4))
                    assert [s["timestamp_ns"] for s in batch["samples"]] == [
                        str(i * 33_333_333) for i in range(4)
                    ]
                    if stream["name"] == "action":
                        assert [s["value"] for s in batch["samples"]] == [
                            [float(i)] for i in range(4)
                        ]
                    missing_camera = request.node.callspec.params["batch_source"].get(
                        "missing_camera", False
                    )
                    expected_valid = [True] * 4
                    if missing_camera and stream["name"] == "observation.images.head_rgb":
                        expected_valid[1] = False
                    assert [s["valid"] for s in batch["samples"]] == expected_valid
                    if stored.get("package_binding"):
                        binding = stored["package_binding"]
                        ref = DataVersionRef.model_validate(binding["data"])
                        physical = packages.steps.read_steps(
                            ref.identity, ref.data_version_id, 1, 2
                        )
                    else:
                        binding = stored.get("data_binding")
                        if binding is not None:
                            physical = batches.legacy.read_steps(
                                identity.dataset_id,
                                binding["rollout_id"],
                                1,
                                2,
                                version=int(binding["lance_version"]),
                                project_id=identity.project_id,
                            )
                        else:
                            physical = None
                    if physical is not None:
                        assert physical.steps[0].sample_valid is not missing_camera
                    serialized = json.dumps(batch)
                    assert all(
                        key not in serialized
                        for key in (
                            "lance_version",
                            "package_prefix",
                            "dataset_uri",
                            "data_version_id",
                        )
                    )
                    assert (
                        client.get(
                            sample_path,
                            headers=headers,
                            params={
                                **query,
                                "snapshot_id": "snapshot_" + "0" * 64,
                            },
                        ).status_code
                        == 409
                    )
                    assert (
                        client.get(
                            sample_path,
                            headers=headers,
                            params={
                                **query,
                                "end": 5000,
                            },
                        ).status_code
                        == 422
                    )
                    # Subwindows use stable sample IDs and preserve nanoseconds.
                    window = client.get(
                        sample_path,
                        headers=headers,
                        params={
                            **query,
                            "start": 1,
                            "end": 3,
                        },
                    )
                    assert window.status_code == 200, window.text
                    assert window.json()["samples"] == batch["samples"][1:3]
                    evidence.append({"snapshot": view, "batch": batch})
        assert seen == {"episode", "lance"}
        # A real cached request still requires current account capabilities.
        with packages.repository.transaction() as cursor:
            cursor.execute(
                """UPDATE access_control.capability_grants SET active=false
                WHERE principal_id=%s AND capability_key='dataset_version.read'""",
                (principal,),
            )
        assert client.get(sample_path, headers=headers, params=query).status_code == 403
        assert client.get(path, headers=headers, params=params).status_code == 403
        Path("/app/.g1-artifacts/logical-samples-http.json").write_text(
            json.dumps({"synthetic": True, "adapters": sorted(seen), "reads": evidence}, indent=2)
        )
