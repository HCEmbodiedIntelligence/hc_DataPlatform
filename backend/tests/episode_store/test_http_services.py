"""Actual isolated gateway -> session auth -> API -> PostgreSQL/MinIO.

The package is synthetic. This verifies software routing/download authorization,
not an ingestion entrance, a real sensor recording, or human acceptance.
"""

import hashlib
import io
import json
import secrets
import zipfile
from pathlib import Path
from uuid import uuid4

import httpx
import numpy as np
import pytest

from hc_data_platform.episode_store.commit import EpisodePackageCommitter
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_writer import build_rgbd_package, identity


def register_and_grant(client, repository, ident, *, capabilities=None):
    command = {"username": "g1-reader-" + uuid4().hex, "password": secrets.token_urlsafe(32)}
    response = client.post("/api/v1/auth/registrations", json=command)
    assert response.status_code == 201, response.text
    principal = response.json()["principal"]["principal_id"]
    # Test bootstrap only, against the explicitly selected isolated database.
    # The HTTP request still uses a normal real session and checked capabilities.
    with repository.transaction() as cursor:
        cursor.execute(
            """INSERT INTO registry.organization_projects
            (organization_id, project_id, display_name) VALUES (%s,%s,'G1 HTTP fixture')
            ON CONFLICT (organization_id,project_id) DO NOTHING""",
            (ident.organization_id, ident.project_id),
        )
        cursor.execute(
            """INSERT INTO access_control.organization_memberships
            (principal_id,organization_id,activated_by) VALUES (%s,%s,'g1-http-bootstrap')""",
            (principal, ident.organization_id),
        )
        request = uuid4()
        cursor.execute(
            """INSERT INTO access_control.membership_requests
            (request_id,organization_id,project_id,requester_id,status,decided_by)
            VALUES (%s,%s,%s,%s,'APPROVED','g1-http-bootstrap')""",
            (request, ident.organization_id, ident.project_id, principal),
        )
        cursor.execute(
            """INSERT INTO access_control.memberships
            (principal_id,organization_id,project_id,source_request_id,activated_by)
            VALUES (%s,%s,%s,%s,'g1-http-bootstrap')""",
            (principal, ident.organization_id, ident.project_id, request),
        )
        capabilities = capabilities or ["dataset_version.read", "export.download"]
        request = uuid4()
        cursor.execute(
            """INSERT INTO access_control.capability_requests
            (request_id,organization_id,project_id,requester_id,capability_keys,status,decided_by)
            VALUES (%s,%s,%s,%s,%s,'APPROVED','g1-http-bootstrap')""",
            (request, ident.organization_id, ident.project_id, principal, capabilities),
        )
        for capability in capabilities:
            cursor.execute(
                """INSERT INTO access_control.capability_grants
                (grant_id,organization_id,project_id,principal_id,source_request_id,capability_key,activated_by)
                VALUES (%s,%s,%s,%s,%s,%s,'g1-http-bootstrap')""",
                (uuid4(), ident.organization_id, ident.project_id, principal, request, capability),
            )
    response = client.post("/api/v1/auth/sessions", json=command)
    assert response.status_code == 201, "isolated session creation failed"
    token = response.json()["access_token"]
    return principal, {
        "Authorization": "Bearer " + token,
        "X-Organization-Id": ident.organization_id,
        "X-Project-ID": ident.project_id,
        "X-Region-Code": ident.region_code,
    }


@pytest.mark.integration
def test_real_http_complete_download_depth_and_capability_revocation(services, tmp_path):
    repository, storage = services
    ident = identity().model_copy(update={"organization_id": "http-" + uuid4().hex})
    root, source, manifest, _ = build_rgbd_package(tmp_path, ident)
    with scoped(source):
        receipt = EpisodePackageCommitter(repository, storage).commit(root, source)
        version = receipt.data.data_version_id
        # Internal Compose gateway is fixed; this test cannot target port 8088.
        with httpx.Client(base_url="http://gateway:8080", timeout=45, trust_env=False) as client:
            principal, headers = register_and_grant(client, repository, ident)
            base = (
                f"/api/v1/projects/{ident.project_id}/regions/{ident.region_code}"
                f"/datasets/{ident.dataset_id}/episodes/{ident.episode_id}/data-versions"
            )
            path = base + "/" + version
            assert client.get(path).status_code == 401
            response = client.get(base, headers=headers)
            assert response.status_code == 200, response.text
            assert [v["data_version_id"] for v in response.json()] == [version]
            response = client.get(path, headers=headers)
            assert response.status_code == 200, response.text
            assert response.json()["download"]["excluded_modalities"] == []
            response = client.get(
                path + "/steps",
                params={"start_step": 1, "end_step": 3, "columns": "/observation/state"},
                headers=headers,
            )
            assert response.status_code == 200, response.text
            assert response.json()["steps"][0]["timestamp_ns"] == "33333333"
            depth = path + "/depth/observation.images.head_depth/2"
            metadata = client.get(depth, headers=headers)
            assert metadata.status_code == 200, metadata.text
            assert metadata.json()["source"]["target_timestamp_ns"] == "66666666"
            response = client.get(
                depth, headers=headers, params={"representation": "npz", "unit": "m"}
            )
            assert response.status_code == 200, response.text
            with np.load(io.BytesIO(response.content), allow_pickle=False) as decoded:
                assert np.isnan(decoded["values"][0, 0])
                assert abs(float(decoded["values"][1, 1]) - 1.5) < 0.003
            response = client.get(depth, headers=headers, params={"representation": "png"})
            assert response.status_code == 200 and response.content.startswith(b"\x89PNG")
            pixel_path = depth + "/pixel"
            pixel_params = {"x": 1, "y": 1, "unit": "m"}
            response = client.get(pixel_path, headers=headers, params=pixel_params)
            assert response.status_code == 200, response.text
            pixel = response.json()
            assert pixel["data"] == receipt.data.model_dump(mode="json")
            assert pixel["valid"] and abs(pixel["value"] - 1.5) < 0.003
            assert pixel["coordinate_space"] == "depth_native" and pixel["unit"] == "m"
            assert pixel["source"]["target_timestamp_ns"] == "66666666"
            invalid = client.get(pixel_path, headers=headers, params={"x": 0, "y": 0}).json()
            assert invalid["value"] is None and not invalid["valid"]
            for params in ({"x": -1, "y": 0}, {"x": 16, "y": 0}, {"x": 0, "y": 16}):
                assert client.get(pixel_path, headers=headers, params=params).status_code == 422
            assert client.get(pixel_path, params=pixel_params).status_code == 401
            assert (
                client.get(
                    pixel_path,
                    params=pixel_params,
                    headers={**headers, "X-Organization-Id": "foreign"},
                ).status_code
                == 403
            )
            response = client.get(path + "/download", headers=headers)
            assert response.status_code == 200, response.text
            archive_hash = hashlib.sha256(response.content).hexdigest()
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                assert set(archive.namelist()) == {
                    *("episode/" + entry.path for entry in manifest.files),
                    "episode/hc/manifest.json",
                    "episode/hc/commit.json",
                }
                for entry in manifest.files:
                    assert (
                        hashlib.sha256(archive.read("episode/" + entry.path)).hexdigest()
                        == entry.sha256
                    )
                destination = Path("/app/.g1-artifacts") / ("http-rgbd-" + version)
                # Exact member names are verified above against safe manifest paths.
                archive.extractall(destination)
                Path("/app/.g1-artifacts/http-rgbd-latest.txt").write_text(
                    str(destination / "episode") + "\n"
                )
            # Repeated complete download is deterministic and byte-for-byte media reuse.
            again = client.get(path + "/download", headers=headers)
            assert hashlib.sha256(again.content).hexdigest() == archive_hash
            foreign = {**headers, "X-Organization-Id": "foreign"}
            assert client.get(path, headers=foreign).status_code == 403
            assert (
                client.get(
                    path.replace(ident.region_code, "foreign-region"),
                    headers={**headers, "X-Region-Code": "foreign-region"},
                ).status_code
                == 404
            )
            assert (
                client.get(
                    path.replace(ident.episode_id, "foreign-episode"), headers=headers
                ).status_code
                == 404
            )
            assert (
                client.get(
                    path + "/steps", params={"start_step": 0, "end_step": 5}, headers=headers
                ).status_code
                == 422
            )
            with repository.transaction() as cursor:
                cursor.execute(
                    """UPDATE access_control.capability_grants SET active=false
                    WHERE principal_id=%s AND capability_key='export.download'""",
                    (principal,),
                )
            # Warm package/cache grants no permission after revocation.
            assert client.get(path + "/download", headers=headers).status_code == 403
            assert client.get(path, headers=headers).status_code == 200
            with repository.transaction() as cursor:
                cursor.execute(
                    """UPDATE access_control.capability_grants SET active=false
                    WHERE principal_id=%s AND capability_key='dataset_version.read'""",
                    (principal,),
                )
            assert client.get(pixel_path, headers=headers, params=pixel_params).status_code == 403
            result = {
                "scope": "synthetic real gateway/API/DB/object-store verification",
                "data": receipt.data.model_dump(mode="json"),
                "gateway": "isolated gateway:8080",
                "session_auth": True,
                "depth_npz_m": True,
                "masked_invalid_nan": True,
                "depth_png": True,
                "depth_pixel_m": pixel,
                "invalid_pixel_null": True,
                "revoked_pixel_denied_on_warm_cache": True,
                "full_archive_sha256": archive_hash,
                "archive_files": len(manifest.files) + 2,
                "all_file_hashes_verified": True,
                "repeat_download_identical": True,
                "foreign_scope_denied": True,
                "revoked_download_denied_on_warm_cache": True,
                "real_recording": False,
                "ingress_verified": False,
            }
            evidence = Path("/app/.g1-artifacts/http-reader-evidence.json")
            evidence.parent.mkdir(exist_ok=True)
            evidence.write_text(json.dumps(result, indent=2) + "\n")
            assert client.post("/api/v1/auth/session:logout", headers=headers).status_code == 204
