from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from hc_data_platform.backup.object_cli import _parser, _write_receipt, main
from hc_data_platform.backup.objects import (
    AgeCliEncryptor,
    ObjectBackupArtifact,
    ObjectBackupError,
    ObjectInventoryArtifact,
)


def _snapshot_artifact(path: Path) -> ObjectBackupArtifact:
    path.write_bytes(b"snapshot-inventory")
    path.chmod(0o600)
    return ObjectBackupArtifact(
        inventory=ObjectInventoryArtifact(
            path=path,
            logical_path="objects/inventory.jsonl.zst",
            mode="snapshot",
            media_type="application/vnd.hc.object-inventory.v1+jsonl+zstd",
            client_side_encryption="repository_kms_only",
            size_bytes=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            consistency_coordinate=f"s3-version-set/v1:sha256:{'a' * 64}",
            object_count=0,
            total_bytes=0,
        ),
        resumed_part_count=0,
    )


def test_cli_exposes_no_connection_or_credential_arguments() -> None:
    help_text = _parser().format_help()
    for subcommand in ("snapshot-create", "portable-create"):
        subparser = _parser()._subparsers._group_actions[0].choices[subcommand]
        help_text += subparser.format_help()
    assert "snapshot-create" in help_text
    assert "portable-create" in help_text
    assert "--endpoint" not in help_text
    assert "--bucket" not in help_text
    assert "--access-key" not in help_text
    assert "--secret-key" not in help_text
    assert "--dsn" not in help_text
    assert "--password" not in help_text
    assert "--age-recipient" not in help_text


def test_cli_missing_configuration_is_redacted(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    monkeypatch.setenv("HC_BACKUP_OBJECT_SECRET_KEY", "do-not-print-this-secret")
    monkeypatch.setattr(
        sys,
        "argv",
        ["hc-object-backup", "snapshot-create", "--staging-directory", str(staging)],
    )

    with pytest.raises(SystemExit) as captured:
        main()

    assert captured.value.code == 2
    output = capsys.readouterr()
    error = json.loads(output.err)
    assert error == {
        "code": "BACKUP_OBJECT_CONFIGURATION_INVALID",
        "status": "failed",
    }
    assert "do-not-print-this-secret" not in output.err
    assert output.out == ""


def test_receipt_is_private_and_contains_no_source_connection_material(tmp_path: Path) -> None:
    staging = tmp_path / "receipt"
    staging.mkdir(mode=0o700)
    artifact = _snapshot_artifact(staging / "inventory.jsonl.zst")

    receipt = _write_receipt(
        artifact,
        staging_directory=staging,
        receipt_name="object-receipt.json",
    )

    payload = receipt.read_text(encoding="utf-8")
    assert receipt.stat().st_mode & 0o077 == 0
    assert "access_key" not in payload
    assert "secret_key" not in payload
    assert "endpoint_url" not in payload
    assert "source-bucket" not in payload
    assert "postgres" not in payload.lower()


def test_age_encryptor_rejects_non_x25519_recipient_before_execution() -> None:
    with pytest.raises(ObjectBackupError) as captured:
        AgeCliEncryptor("not-an-age-recipient")  # type: ignore[arg-type]
    assert captured.value.code == "BACKUP_OBJECT_AGE_RECIPIENT_INVALID"

    with pytest.raises(ObjectBackupError) as wrong_length:
        AgeCliEncryptor("age1" + "q" * 57)  # type: ignore[arg-type]
    assert wrong_length.value.code == "BACKUP_OBJECT_AGE_RECIPIENT_INVALID"


def test_dockerfile_has_pinned_nonroot_object_maintenance_target() -> None:
    dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text(encoding="utf-8")
    assert "AS object-maintenance" in dockerfile
    assert "AS object-maintenance-builder" in dockerfile
    assert "--extra backup" in dockerfile
    assert "bdc69c09cbdd6cf8b1f333d372a1f58247b3a33146406333e30c0f26e8f51377" in dockerfile
    assert "age-v1.3.1-linux-amd64.tar.gz" in dockerfile
    assert 'org.opencontainers.image.component="object-maintenance"' in dockerfile
    assert 'USER 65532:65532\nENTRYPOINT ["hc-object-backup"]' in dockerfile
