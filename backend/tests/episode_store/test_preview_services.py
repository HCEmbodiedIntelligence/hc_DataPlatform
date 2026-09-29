"""Actual preview encoding, PG/MinIO publication and gateway grants; synthetic sensor data."""

import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import av
import httpx
import numpy as np
import pytest

from hc_data_platform.episode_store.service import EpisodePackageService
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_http_services import register_and_grant
from tests.episode_store.test_writer import build_rgbd_package, identity


def video_frames(content):
    with av.open(io.BytesIO(content)) as container:
        stream = container.streams.video[0]
        assert stream.codec_context.name == "h264"
        assert stream.codec_context.format.name == "yuv420p"
        frames = tuple(container.decode(stream))
        assert len(frames) == 4
        assert [float(f.pts * f.time_base) for f in frames] == pytest.approx(
            [i / 30 for i in range(4)]
        )
        return [f.to_ndarray(format="rgb24") for f in frames]


@pytest.mark.integration
@pytest.mark.parametrize("rgb_codec", ["libx265", "libx264"])
def test_preview_real_grants_masks_reuse_retry_and_primary_bytes(
    services, tmp_path, monkeypatch, rgb_codec
):
    repository, storage = services
    ident = identity().model_copy(update={"organization_id": "preview-" + uuid4().hex})
    root, source, manifest, _ = build_rgbd_package(tmp_path, ident, rgb_codec=rgb_codec)
    service = EpisodePackageService(repository.connections, storage, tmp_path / "service")
    rgb, depth = "observation.images.head_rgb", "observation.images.head_depth"
    try:
        with scoped(source):
            data = service.committer.commit(root, source).data
            with httpx.Client(
                base_url="http://gateway:8080", timeout=45, trust_env=False
            ) as client:
                principal, headers = register_and_grant(client, repository, ident)
                base = (
                    f"/api/v1/projects/{ident.project_id}/regions/{ident.region_code}/datasets/"
                    f"{ident.dataset_id}/episodes/{ident.episode_id}/data-versions/{data.data_version_id}"
                )

                def authorize(feature, *, request_headers=headers):
                    return client.post(
                        base + f"/media/{feature}/authorize", headers=request_headers
                    )

                assert authorize(depth, request_headers={}).status_code == 401
                assert authorize(depth).status_code == 425
                assert (
                    service.previews._row(data, depth) is None
                )  # No task or encoder from authorization.

                def fail(stage):
                    if stage == "objects_verified":
                        raise InterruptedError("preview publication interruption")

                service.previews.checkpoint = fail
                with pytest.raises(InterruptedError):
                    service.previews.prepare(data, depth)
                assert authorize(depth).status_code == 409
                assert service.repository.get(ident, data.data_version_id).data == data
                service.previews.checkpoint = lambda stage: None

                def prepare(_):
                    with scoped(source):
                        return service.previews.prepare(data, depth)

                with ThreadPoolExecutor(max_workers=4) as pool:
                    receipts = tuple(pool.map(prepare, range(4)))
                assert all(r == receipts[0] for r in receipts)
                rgb_receipt = service.previews.prepare(data, rgb)
                assert rgb_receipt.source_reused == (rgb_codec == "libx264")
                assert not receipts[0].source_reused

                def forbidden(*args, **kwargs):
                    raise AssertionError("a completed preview must not be regenerated")

                monkeypatch.setattr(service.previews, "_encode", forbidden)
                assert service.previews.prepare(data, depth) == receipts[0]
                assert service.previews.prepare(data, rgb) == rgb_receipt

                for feature in (rgb, depth):
                    response = authorize(feature)
                    assert response.status_code == 200, response.text
                    grant = response.json()
                    assert grant["data"] == data.model_dump(mode="json")
                    assert grant["frame_count"] == 4 and grant["fps"] == 30
                    assert grant["kind"] == ("depth-preview" if feature == depth else "rgb-video")
                    # The public presigner uses the isolated browser edge. Preserve its
                    # Host signature while connecting through the internal compose DNS.
                    url = urlsplit(grant["media_url"])
                    internal = urlunsplit(
                        (url.scheme, "object-store-browser:9000", url.path, url.query, "")
                    )
                    media = client.get(internal, headers={"Host": url.netloc})
                    assert media.status_code == 200, "isolated preview presign rejected"
                    assert hashlib.sha256(media.content).hexdigest() == grant["media_sha256"]
                    frames = video_frames(media.content)
                    if feature == rgb:
                        assert [float(f.mean()) for f in frames] == pytest.approx(
                            [0, 50, 100, 150], abs=8
                        )
                    else:
                        assert (grant["depth_min_m"], grant["depth_max_m"]) == (0.01, 10)
                        assert float(frames[2][0, 0].mean()) < float(frames[2][8, 8].mean())
                        frame = service.depth.read(ident, data.data_version_id, depth, 2, unit="m")
                        assert np.isnan(frame.values[0, 0]) and frame.flags[0, 0] == 0
                        assert frame.values[8, 8] == pytest.approx(1.5, abs=0.003)
                    # Signed storage reads support seek without routing video bodies through API.
                    ranged = client.get(
                        internal, headers={"Host": url.netloc, "Range": "bytes=0-31"}
                    )
                    assert ranged.status_code == 206 and ranged.content == media.content[:32]

                for entry in manifest.files:
                    body = b"".join(storage.read_chunks(f"{data.package_prefix}/{entry.path}"))
                    assert hashlib.sha256(body).hexdigest() == entry.sha256
                assert (
                    authorize(
                        depth, request_headers={**headers, "X-Organization-Id": "foreign"}
                    ).status_code
                    == 403
                )
                assert (
                    client.post(
                        base.replace(ident.region_code, "foreign-region")
                        + f"/media/{depth}/authorize",
                        headers={**headers, "X-Region-Code": "foreign-region"},
                    ).status_code
                    == 404
                )
                with repository.transaction() as cursor:
                    cursor.execute(
                        "UPDATE access_control.capability_grants SET active=false "
                        "WHERE principal_id=%s",
                        (principal,),
                    )
                assert authorize(depth).status_code == 403
                assert (
                    client.post("/api/v1/auth/session:logout", headers=headers).status_code == 204
                )
    finally:
        service.close()


@pytest.mark.integration
def test_preview_lost_commit_response_recovers_without_encoding(services, tmp_path, monkeypatch):
    repository, storage = services
    ident = identity().model_copy(update={"organization_id": "preview-lost-" + uuid4().hex})
    root, source, _, _ = build_rgbd_package(tmp_path, ident)
    service = EpisodePackageService(repository.connections, storage, tmp_path / "service")
    try:
        with scoped(source):
            data = service.committer.commit(root, source).data

            def fail(stage):
                if stage == "database_committed":
                    raise InterruptedError("lost preview response")

            service.previews.checkpoint = fail
            with pytest.raises(InterruptedError):
                service.previews.prepare(data, "observation.images.head_depth")

            def forbidden(*args, **kwargs):
                raise AssertionError("READY retry encoded again")

            monkeypatch.setattr(service.previews, "_encode", forbidden)
            assert (
                service.previews.prepare(data, "observation.images.head_depth").kind
                == "depth-preview"
            )
    finally:
        service.close()
