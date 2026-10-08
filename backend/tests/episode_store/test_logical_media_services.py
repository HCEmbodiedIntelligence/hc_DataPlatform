"""Real logical grants across both storage adapters, decoded video and HTTP Range."""

import hashlib
import io
from urllib.parse import urlsplit, urlunsplit

import av
import httpx
import pytest

from tests.episode_store.test_collection_releases_services import release_source  # noqa: F401
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_http_services import register_and_grant
from tests.episode_store.test_migration_annotations_services import annotation_source  # noqa: F401
from tests.episode_store.test_migration_batches_services import batch_source  # noqa: F401


@pytest.mark.integration
@pytest.mark.parametrize("batch_source", [{"logical_media": True}], indirect=True)
def test_logical_media_http_both_stores_decode_range_and_revoke(release_source):  # noqa: F811
    source, packages, command, _ = release_source
    _, plan, _, _, _, _ = source
    ident = plan.legacy.identity
    with (
        scoped(plan.source),
        httpx.Client(base_url="http://gateway:8080", timeout=45, trust_env=False) as client,
    ):
        principal, headers = register_and_grant(
            client,
            packages.repository,
            ident,
            capabilities=["dataset_version.read", "episode.read"],
        )
        seen = set()
        for version in ["version_lance_2", "version_" + command.collection_version_id]:
            root = f"/api/v1/projects/{ident.project_id}/datasets/{ident.dataset_id}"
            bootstrap = client.get(root + f"/versions/{version}/bootstrap", headers=headers)
            assert bootstrap.status_code == 200, bootstrap.text
            token = bootstrap.json()["data"]["snapshot_token"]
            with packages.repository.transaction() as cursor:
                cursor.execute(
                    """SELECT revision_document
                    FROM dataset_registry.dataset_version_episode_revisions
                    WHERE organization_id=%s AND project_id=%s AND region_code=%s
                    AND dataset_id=%s AND version_id=%s ORDER BY ordinal""",
                    (*ident.scope, ident.dataset_id, version),
                )
                revisions = [row[0] for row in cursor.fetchall()]
            for revision in revisions:
                path = (
                    f"/api/v1/projects/{ident.project_id}/regions/{ident.region_code}"
                    f"/datasets/{ident.dataset_id}/versions/{version}"
                    f"/episode-revisions/{revision['revision_id']}/logical"
                )
                result = client.get(path, headers=headers, params={"snapshot_token": token})
                assert result.status_code == 200, result.text
                view = result.json()
                params = {"snapshot_token": token, "snapshot_id": view["snapshot_id"]}
                for stream in revision["streams"]:
                    kind = "episode" if stream.get("package_binding") else "lance"
                    binding = stream.get("package_binding") or stream.get("aligned_media_binding")
                    endpoint = path + f"/streams/{stream['episode_stream_id']}/media/authorize"
                    response = client.post(endpoint, headers=headers, params=params)
                    is_media = bool(binding) and binding.get("value_kind") is None
                    if not is_media:
                        assert response.status_code == 422, response.text
                        continue
                    assert response.status_code == 200, response.text
                    grant = response.json()
                    seen.add(kind)
                    assert response.headers["cache-control"] == "no-store"
                    assert grant["snapshot_id"] == view["snapshot_id"]
                    assert grant["stream_id"] == stream["episode_stream_id"]
                    assert grant["sample_count"] == 4 and grant["sample_rate_hz"] == 30
                    assert grant["media_start_seconds"] == 0
                    assert all(
                        k not in grant for k in ["artifact_id", "lance_version", "data", "timeline"]
                    )
                    url = urlsplit(grant["media_url"])
                    internal = urlunsplit(
                        (url.scheme, "object-store-browser:9000", url.path, url.query, "")
                    )
                    media = client.get(internal, headers={"Host": url.netloc})
                    assert media.status_code == 200
                    assert hashlib.sha256(media.content).hexdigest() == grant["media_sha256"]
                    with av.open(io.BytesIO(media.content)) as container:
                        frames = tuple(container.decode(video=0))
                        assert len(frames) == 4
                        assert [float(f.pts * f.time_base) for f in frames] == pytest.approx(
                            [i / 30 for i in range(4)]
                        )
                    ranged = client.get(
                        internal, headers={"Host": url.netloc, "Range": "bytes=0-31"}
                    )
                    assert ranged.status_code == 206 and ranged.content == media.content[:32]
                    assert client.post(endpoint, params=params).status_code == 401
                    assert (
                        client.post(
                            endpoint,
                            headers=headers,
                            params={**params, "snapshot_id": "snapshot_" + "0" * 64},
                        ).status_code
                        == 409
                    )
                    assert (
                        client.post(
                            endpoint,
                            headers={**headers, "X-Organization-Id": "foreign"},
                            params=params,
                        ).status_code
                        == 403
                    )
                    if kind == "episode" and stream["kind"] == "DEPTH":
                        assert grant["kind"] == "depth-preview" and grant["colorizer"] is not None
                        assert (grant["depth_min_m"], grant["depth_max_m"]) == (0.01, 10)
                    last_endpoint = endpoint
        assert seen == {"episode", "lance"}
        with packages.repository.transaction() as cursor:
            cursor.execute(
                "UPDATE access_control.capability_grants SET active=false WHERE principal_id=%s",
                (principal,),
            )
            cursor.execute(
                "SELECT action FROM core.audit_events WHERE actor_id=%s "
                "AND action IN ('episode.preview.authorized','aligned_media.authorized')",
                (principal,),
            )
            assert {r[0] for r in cursor.fetchall()} == {
                "episode.preview.authorized",
                "aligned_media.authorized",
            }
        assert client.post(last_endpoint, headers=headers, params=params).status_code == 403
