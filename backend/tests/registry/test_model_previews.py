import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.registry.models import RobotModelAsset
from hc_data_platform.registry.service import RegistryService
from hc_data_platform.core.context import RequestContext, bind_request_context, reset_request_context
from tests.registry.test_robot_model_api import _auth, _service, _app
from hc_data_platform.registry.router import configure_registry
from fastapi.testclient import TestClient


def test_all_viewers_get_one_scoped_metadata_batch_with_lightweight_meshes(tmp_path, monkeypatch):
    service, repository = _service()
    service.previews.root = tmp_path
    assets = tuple(RobotModelAsset(
        asset_id=role.lower(), relative_path=path, role=role, size_bytes=1000,
        sha256=digest * 64, media_type=media_type, created_at=datetime.now(timezone.utc),
    ) for role, path, digest, media_type in (
        ("URDF", "model.urdf", "a", "application/xml"),
        ("MESH", "body.stl", "b", "model/stl"),
        ("CONFIG", "config.json", "c", "application/json"),
    ))
    for asset in assets:
        repository._assets[("organization-a", "project-a", asset.asset_id)] = (
            "object", "version-a", asset,
        )
    folder = tmp_path / assets[1].sha256
    folder.mkdir()
    (folder / "manifest-glb.json").write_text(json.dumps(dict(
        source_sha256=assets[1].sha256, sha256="d" * 64, size_bytes=100,
        source_size_bytes=1000, source_faces=100, faces=20,
        media_type="model/gltf-binary", policy="mesh-indexed-glb-v2",
    )))
    def forbid_content(**_):
        pytest.fail("Viewer authorization must not read model content")
    monkeypatch.setattr(repository, "get_robot_model_asset", forbid_content)
    configure_registry(service)
    current = {"value": _auth()}
    client = TestClient(_app(current))
    path = "/api/v1/organizations/organization-a/robot-model-versions/version-a/viewer-assets:authorize"
    response = client.post(path)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    page = response.json()
    assert page["version_id"] == "version-a"
    assert {item["asset"]["role"] for item in page["items"]} == {"URDF", "MESH"}
    mesh = next(item for item in page["items"] if item["asset"]["role"] == "MESH")
    assert mesh["preview"]["source_sha256"] == assets[1].sha256
    assert mesh["preview"]["media_type"] == "model/gltf-binary"
    assert mesh["authorization"]["sha256"] == assets[1].sha256
    assert "preview_sha256=" in mesh["preview"]["download_url"]
    assert client.post(path.replace("version-a", "missing")).status_code == 404
    current["value"] = _auth(can_read=False)
    assert client.post(path).status_code == 403
    current["value"] = _auth(project_id="project-b")
    assert client.post(path).status_code == 403


def test_preview_retains_original_token_scope_expiry_and_source_identity(tmp_path):
    original, preview = b"original model bytes", b"preview"
    digest = lambda value: hashlib.sha256(value).hexdigest()
    source_sha, preview_sha = digest(original), digest(preview)
    asset = RobotModelAsset(asset_id="mesh", relative_path="mesh.stl", role="MESH",
        size_bytes=len(original), sha256=source_sha, media_type="model/stl", created_at=datetime.now(timezone.utc))
    repository = SimpleNamespace(get_robot_model_asset=lambda **_: ("object", asset, original))
    now = datetime.now(timezone.utc)
    service = RegistryService(repository, preview_root=str(tmp_path), clock=lambda: now)
    folder = tmp_path / source_sha
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps(dict(source_sha256=source_sha,
        sha256=preview_sha, size_bytes=len(preview), source_size_bytes=len(original),
        source_faces=100, faces=20, media_type="model/stl", policy="mesh-qem-v1")))
    path = folder / (preview_sha + ".stl")
    path.write_bytes(preview)

    def token(expires=60):
        return service._preflight_codec.encode(dict(kind="robot-model-asset-database-download/v1",
            organization_id="org", project_id="project", asset_id="mesh",
            expires_at=int(now.timestamp()) + expires))

    def read(org="org", sha=preview_sha, credential=None):
        return service.open_authorized_asset_download(organization_id=org, token=credential or token(), preview_sha256=sha)

    assert b"".join(read()[1]) == preview
    assert b"".join(read(sha=None)[1]) == original
    for kwargs in ({"org": "foreign"}, {"credential": token(-1)}, {"credential": "invalid"}, {"sha": "f" * 64}):
        with pytest.raises(ProblemException):
            read(**kwargs)
    path.write_bytes(b"corrupt")
    with pytest.raises(ProblemException) as error:
        read()
    assert error.value.problem.status == 409


def test_batch_reads_only_metadata_once_and_keeps_permission_and_membership_checks():
    events, reads = [], []
    asset = RobotModelAsset(asset_id="mesh", relative_path="mesh.stl", role="MESH",
        size_bytes=100, sha256="a" * 64, media_type="model/stl", created_at=datetime.now(timezone.utc))

    def find_many(org, version, ids):
        reads.append((org, version, ids))
        return {"mesh": ("upload-project", asset)} if version == "published" else {}

    service = RegistryService(SimpleNamespace(append_audit=events.append),
        published_assets=SimpleNamespace(find_many=find_many))
    context = bind_request_context(RequestContext(organization_id="organization-a", project_id="project-a"))
    try:
        def authorize(auth=None, version="published"):
            return service.authorize_published_asset_downloads(auth=auth or _auth(),
                organization_id="organization-a", project_id="project-a", version_id=version,
                asset_ids=("mesh",), request_id="batch-test")

        grant, = authorize()
        assert grant.sha256 == asset.sha256
        assert len(reads) == len(events) == 1
        payload = service._preflight_codec.decode(grant.download_url.split("token=")[1])
        assert payload["organization_id"] == "organization-a"
        assert payload["project_id"] == "upload-project"
        for auth in (_auth(can_read=False), _auth(project_id="project-b")):
            with pytest.raises(ProblemException):
                authorize(auth)
        assert len(reads) == 1
        with pytest.raises(ProblemException):
            authorize(version="draft-or-foreign")
        assert len(events) == 1
    finally:
        reset_request_context(context)
