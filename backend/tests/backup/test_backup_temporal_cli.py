from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from hc_data_platform.backup.temporal import TemporalBackupError, TemporalPolicyArtifact
from hc_data_platform.backup.temporal_cli import (
    _artifact_from_receipt,
    _parser,
    _write_receipt,
    main,
)


def _artifact(staging: Path) -> TemporalPolicyArtifact:
    temporal = staging / "temporal"
    temporal.mkdir(mode=0o700)
    policy = temporal / "policy.json"
    policy.write_bytes(b'{"format_version":"hc-temporal-backup-policy/v1"}\n')
    policy.chmod(0o600)
    return TemporalPolicyArtifact(
        path=policy.resolve(),
        mode="snapshot",
        media_type="application/vnd.hc.temporal-policy.v1+json",
        client_side_encryption="repository_kms_only",
        size_bytes=policy.stat().st_size,
        sha256=hashlib.sha256(policy.read_bytes()).hexdigest(),
        schedule_count=2,
        open_workflow_count=3,
        schedule_inventory_sha256="a" * 64,
        open_workflow_inventory_sha256="b" * 64,
        consistency_coordinate="temporal-policy/v1:sha256:" + "c" * 64,
    )


def test_cli_exposes_no_temporal_or_postgres_credentials_in_arguments() -> None:
    parser = _parser()
    help_text = parser.format_help()
    for command in ("snapshot-create", "portable-create", "verify", "probe"):
        help_text += parser._subparsers._group_actions[0].choices[command].format_help()

    for command in ("snapshot-create", "portable-create", "verify", "probe"):
        assert command in help_text
    for forbidden in (
        "--temporal-target",
        "--api-key",
        "--tls-key",
        "--postgres-dsn",
        "--password",
        "--age-recipient",
        "--age-identity",
        "--provider-policy",
    ):
        assert forbidden not in help_text


def test_cli_configuration_failure_is_stable_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "temporal-api-key-that-must-never-be-printed"
    monkeypatch.setenv("HC_BACKUP_TEMPORAL_API_KEY", secret)
    monkeypatch.setattr(sys, "argv", ["hc-temporal-backup", "probe"])

    with pytest.raises(SystemExit) as captured:
        main()

    assert captured.value.code == 2
    output = capsys.readouterr()
    assert json.loads(output.err) == {
        "code": "BACKUP_TEMPORAL_JOB_CONFIG_INVALID",
        "status": "failed",
    }
    assert secret not in output.err
    assert output.out == ""


def test_receipt_is_private_strict_and_has_no_connection_material(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    artifact = _artifact(staging)

    receipt = _write_receipt(
        artifact,
        staging_directory=staging,
        receipt_name="temporal-receipt.json",
    )
    loaded = _artifact_from_receipt(receipt)

    assert loaded == artifact
    assert receipt.stat().st_mode & 0o077 == 0
    payload = receipt.read_text(encoding="utf-8")
    for forbidden in (
        "target",
        "api_key",
        "client_private_key",
        "postgres_password",
        "127.0.0.1",
    ):
        assert forbidden not in payload


def test_receipt_rejects_duplicate_json_member(tmp_path: Path) -> None:
    receipt = tmp_path / "duplicate.json"
    receipt.write_bytes(b'{"mode":"snapshot","mode":"portable"}\n')
    receipt.chmod(0o600)

    with pytest.raises(TemporalBackupError) as captured:
        _artifact_from_receipt(receipt)
    assert captured.value.code == "BACKUP_TEMPORAL_RECEIPT_INVALID"


def test_dockerfile_has_pinned_nonroot_temporal_maintenance_target() -> None:
    dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text(encoding="utf-8")
    target = dockerfile.split("FROM runtime-base AS temporal-maintenance", maxsplit=1)[1]
    target = target.split("FROM runtime-media AS worker", maxsplit=1)[0]
    assert "AS temporal-maintenance-builder" in dockerfile
    assert "COPY --from=age-tools /usr/local/bin/age /usr/local/bin/age" in target
    assert 'org.opencontainers.image.component="temporal-maintenance"' in target
    assert "RUN install -d -o 65532 -g 65532 -m 0700 /backup-staging" in target
    assert 'USER 65532:65532\nENTRYPOINT ["hc-temporal-backup"]' in target
