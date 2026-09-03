from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from hc_data_platform.backup.contracts import SecretDependencyV1
from hc_data_platform.backup.dependencies import (
    ConfigurationBackupArtifact,
    ConfigurationBackupError,
    PublicConfigArtifact,
    SecretsEnvelopeArtifact,
)
from hc_data_platform.backup.dependency_cli import (
    _artifact_from_receipt,
    _parser,
    _write_receipt,
    main,
)

_REQUIRED = (
    "HC_AUTH_ABUSE_HMAC_SECRET",
    "HC_AUTH_TURNSTILE_SECRET",
    "HC_CURSOR_SECRET",
    "HC_DATA_SOURCE_CREDENTIAL_KEY",
    "HC_OBJECT_STORE_ACCESS_KEY",
    "HC_OBJECT_STORE_SECRET_KEY",
    "HC_POSTGRES_DSN",
)


def _artifact(staging: Path) -> ConfigurationBackupArtifact:
    config_directory = staging / "config"
    secrets_directory = staging / "secrets"
    config_directory.mkdir(mode=0o700)
    secrets_directory.mkdir(mode=0o700)
    public_path = config_directory / "public.yaml"
    envelope_path = secrets_directory / "envelope.json"
    public_path.write_bytes(b"format_version: hc-deployment-public-config/v1\n")
    envelope_path.write_bytes(b'{"format_version":"hc-secret-dependencies/v1"}\n')
    public_path.chmod(0o600)
    envelope_path.chmod(0o600)
    render_sha = "a" * 64
    hmac_reference = "kms://vault/unit/transit/config-hmac/versions/v1"
    dependencies = tuple(
        SecretDependencyV1(
            environment_variable=name,
            secret_reference="vault-kv://unit/secret/hc-data-platform/runtime/versions/v3",
            secret_key_name=name.casefold().replace("hc_", "").replace("_", "-"),
            version="v3",
            fingerprint_sha256=hashlib.sha256(name.encode()).hexdigest(),
        )
        for name in _REQUIRED
    )
    return ConfigurationBackupArtifact(
        public_config=PublicConfigArtifact(
            path=public_path.resolve(),
            size_bytes=public_path.stat().st_size,
            sha256=hashlib.sha256(public_path.read_bytes()).hexdigest(),
            helm_render_sha256=render_sha,
        ),
        secrets_envelope=SecretsEnvelopeArtifact(
            path=envelope_path.resolve(),
            logical_path="secrets/envelope.json",
            mode="snapshot",
            media_type="application/vnd.hc.secret-dependencies.v1+json",
            client_side_encryption="repository_kms_only",
            size_bytes=envelope_path.stat().st_size,
            sha256=hashlib.sha256(envelope_path.read_bytes()).hexdigest(),
            dependency_count=7,
            provider_reference="unit-vault",
            hmac_key_reference=hmac_reference,
        ),
        manifest_dependencies=dependencies,
        preflight_coordinate=f"secret-dependency-set/v1:sha256:{'b' * 64}",
    )


def test_cli_exposes_no_provider_credential_or_identity_arguments() -> None:
    parser = _parser()
    help_text = parser.format_help()
    for command in ("snapshot-create", "portable-create", "verify"):
        help_text += parser._subparsers._group_actions[0].choices[command].format_help()

    assert "snapshot-create" in help_text
    assert "portable-create" in help_text
    assert "verify" in help_text
    for forbidden in (
        "--vault-endpoint",
        "--vault-token",
        "--postgres-dsn",
        "--password",
        "--age-recipient",
        "--age-identity",
        "--secret-value",
    ):
        assert forbidden not in help_text


def test_cli_configuration_failure_is_stable_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    rendered = tmp_path / "rendered.yaml"
    rendered.write_text("apiVersion: v1\n", encoding="utf-8")
    secret = "vault-token-that-must-never-be-printed"
    monkeypatch.setenv("HC_BACKUP_VAULT_TOKEN", secret)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hc-configuration-backup",
            "snapshot-create",
            "--helm-rendered-manifest",
            str(rendered),
            "--staging-directory",
            str(staging),
        ],
    )

    with pytest.raises(SystemExit) as captured:
        main()

    assert captured.value.code == 2
    output = capsys.readouterr()
    assert json.loads(output.err) == {
        "code": "BACKUP_CONFIGURATION_JOB_CONFIG_INVALID",
        "status": "failed",
    }
    assert secret not in output.err
    assert output.out == ""


def test_receipt_is_private_strict_and_has_no_provider_connection_material(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    artifact = _artifact(staging)

    receipt = _write_receipt(
        artifact,
        staging_directory=staging,
        receipt_name="configuration-receipt.json",
    )
    loaded = _artifact_from_receipt(receipt)

    assert loaded == artifact
    assert receipt.stat().st_mode & 0o077 == 0
    payload = receipt.read_text(encoding="utf-8")
    for forbidden in (
        "endpoint_url",
        "vault_token",
        "postgres_password",
        "secret_value",
        "127.0.0.1",
    ):
        assert forbidden not in payload


def test_receipt_rejects_duplicate_json_member(tmp_path: Path) -> None:
    receipt = tmp_path / "duplicate.json"
    receipt.write_bytes(b'{"format_version":"one","format_version":"two"}\n')
    receipt.chmod(0o600)

    with pytest.raises(ConfigurationBackupError) as captured:
        _artifact_from_receipt(receipt)
    assert captured.value.code == "BACKUP_CONFIGURATION_RECEIPT_INVALID"


def test_dockerfile_has_pinned_nonroot_configuration_maintenance_target() -> None:
    dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text(encoding="utf-8")
    target = dockerfile.split("FROM runtime-base AS configuration-maintenance", maxsplit=1)[1]
    target = target.split("FROM runtime-media AS worker", maxsplit=1)[0]
    assert "AS configuration-maintenance-builder" in dockerfile
    assert "COPY --from=age-tools /usr/local/bin/age /usr/local/bin/age" in target
    assert 'org.opencontainers.image.component="configuration-maintenance"' in target
    assert "RUN install -d -o 65532 -g 65532 -m 0700 /backup-staging" in target
    assert 'USER 65532:65532\nENTRYPOINT ["hc-configuration-backup"]' in target
