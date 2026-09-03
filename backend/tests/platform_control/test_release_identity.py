from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.platform_control.release_identity import (
    PlatformReleaseIdentityV1,
    release_identity_from_settings,
)
from hc_data_platform.workflow.worker import (
    worker_build_id,
    worker_release_identity,
    worker_runtime_build_id,
)

DIGEST_A = f"sha256:{'a' * 64}"
DIGEST_B = f"sha256:{'b' * 64}"
DIGEST_C = f"sha256:{'c' * 64}"
ROOT = Path(__file__).resolve().parents[3]


def _identity_settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "runtime_backend": "memory",
        "release_id": "platform-v0.1.0-test.1",
        "platform_version": "0.1.0",
        "git_commit": "1" * 40,
        "chart_version": "0.1.0",
        "release_manifest_digest": DIGEST_A,
        "migration_manifest_digest": DIGEST_B,
        "component_role": "api",
        "component_image_digest": DIGEST_C,
        "_env_file": None,
    }
    values.update(updates)
    return Settings(**values)  # type: ignore[arg-type]


def test_release_identity_is_strict_immutable_and_matches_installed_package() -> None:
    identity = release_identity_from_settings(_identity_settings())
    assert identity.model_dump() == {
        "format_version": "hc-platform-release-identity/v1",
        "release_id": "platform-v0.1.0-test.1",
        "semantic_version": "0.1.0",
        "git_commit": "1" * 40,
        "chart_version": "0.1.0",
        "release_manifest_digest": DIGEST_A,
        "migration_manifest_digest": DIGEST_B,
        "component": "api",
        "component_image_digest": DIGEST_C,
    }
    with pytest.raises(ValidationError, match="installed backend package version"):
        PlatformReleaseIdentityV1.model_validate(
            {**identity.model_dump(), "semantic_version": "0.1.1"}
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PlatformReleaseIdentityV1.model_validate({**identity.model_dump(), "secret": "no"})


def test_staging_and_production_reject_unreleased_or_zero_identity_fields() -> None:
    for environment in ("staging", "production"):
        with pytest.raises(ValidationError, match="HC_RELEASE_ID"):
            _production_security_settings(environment=environment)
        with pytest.raises(ValidationError, match="HC_RELEASE_MANIFEST_DIGEST"):
            _production_security_settings(
                environment=environment,
                release_id="platform-v0.1.0",
                git_commit="1" * 40,
                release_manifest_digest=f"sha256:{'0' * 64}",
                migration_manifest_digest=DIGEST_B,
                component_image_digest=DIGEST_C,
            )


def test_platform_version_endpoint_is_public_and_exact() -> None:
    app = create_app(settings=_identity_settings())
    with TestClient(app) as client:
        response = client.get("/api/v1/platform/version")

    assert response.status_code == 200
    assert response.json() == release_identity_from_settings(_identity_settings()).model_dump()
    operation = app.openapi()["paths"]["/api/v1/platform/version"]["get"]
    assert operation["operationId"] == "getPlatformVersion"
    assert operation["security"] == []
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/PlatformReleaseIdentityV1"
    }


def test_worker_identity_requires_the_exact_main_or_media_component_role() -> None:
    main = worker_release_identity(_identity_settings(component_role="worker"), worker_role="main")
    media = worker_release_identity(
        _identity_settings(component_role="media-worker"), worker_role="media"
    )
    assert (main.component, main.release_manifest_digest) == ("worker", DIGEST_A)
    assert (media.component, media.release_manifest_digest) == ("media-worker", DIGEST_A)

    with pytest.raises(ValueError, match="HC_COMPONENT_ROLE=worker"):
        worker_release_identity(_identity_settings(), worker_role="main")
    with pytest.raises(ValueError, match="HC_COMPONENT_ROLE=media-worker"):
        worker_release_identity(_identity_settings(component_role="worker"), worker_role="media")


def test_temporal_worker_build_id_is_release_bound_and_can_be_explicit() -> None:
    settings = _identity_settings(component_role="worker")
    assert worker_build_id(settings) == "platform-v0.1.0-test.1"
    assert worker_runtime_build_id(settings) is None
    explicit = settings.model_copy(update={"temporal_worker_build_id": "build-42"})
    assert worker_build_id(explicit) == "build-42"
    assert worker_runtime_build_id(explicit) == "build-42"
    assert (
        worker_build_id(Settings(environment="test", runtime_backend="memory", _env_file=None))
        is None
    )


def test_managed_environment_activates_release_bound_temporal_worker_versioning() -> None:
    settings = _production_security_settings(
        release_id="platform-v0.1.0-production.1",
        git_commit="1" * 40,
        release_manifest_digest=DIGEST_A,
        migration_manifest_digest=DIGEST_B,
        component_role="worker",
        component_image_digest=DIGEST_C,
        secret_bundle_revision=f"sha256:{'d' * 64}",
    )

    assert worker_runtime_build_id(settings) == "platform-v0.1.0-production.1"


def test_release_renderer_binds_manifest_migrations_and_every_component_image(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "release.json"
    values_path = tmp_path / "values.json"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "deploy/scripts/render_release_manifest.py"),
            "--release-id",
            "platform-v0.1.0-test.1",
            "--git-commit",
            "1" * 40,
            "--frontend",
            f"registry.example/frontend@{DIGEST_A}",
            "--api",
            f"registry.example/api@{DIGEST_B}",
            "--worker",
            f"registry.example/worker@{DIGEST_C}",
            "--output",
            str(manifest_path),
            "--values-output",
            str(values_path),
        ],
        check=True,
        cwd=ROOT,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    values = json.loads(values_path.read_text(encoding="utf-8"))
    manifest_digest = f"sha256:{hashlib.sha256(manifest_path.read_bytes()).hexdigest()}"
    migration_manifest = (ROOT / "backend/migrations/manifest.txt").read_bytes()
    migration_digest = f"sha256:{hashlib.sha256(migration_manifest).hexdigest()}"
    assert manifest["spec"]["semanticVersion"] == "0.1.0"
    assert manifest["spec"]["database"]["migrationManifestDigest"] == migration_digest
    assert values["global"] == {
        "releaseId": "platform-v0.1.0-test.1",
        "releaseManifestDigest": manifest_digest,
        "gitCommit": "1" * 40,
        "migrationManifestDigest": migration_digest,
    }
    assert values["backend"]["api"]["image"]["digest"] == DIGEST_B
    assert values["backend"]["worker"]["image"]["digest"] == DIGEST_C
    assert values["backend"]["mediaWorker"]["image"]["digest"] == DIGEST_C


def _production_security_settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "environment": "production",
        "runtime_backend": "memory",
        "cursor_secret": "production-cursor-secret",
        "data_source_credential_key": "production-data-source-credential-key",
        "auth_abuse_enabled": True,
        "auth_abuse_hmac_secret": "production-auth-abuse-hmac-secret-at-least-32",
        "auth_challenge_provider": "turnstile",
        "auth_turnstile_site_key": "production-turnstile-site-key",
        "auth_turnstile_secret": "production-turnstile-secret-at-least-32",
        "auth_turnstile_expected_hostnames": ("app.example.com",),
        "object_store_secret_key": "production-object-secret",
        "object_store_public_endpoint": "https://uploads.example.com",
        "jwt_issuer": "https://issuer.example/",
        "jwt_signing_key": "production-jwt-signing-key",
        "enforce_schema_migrations": True,
        "_env_file": None,
    }
    values.update(updates)
    return Settings(**values)  # type: ignore[arg-type]
