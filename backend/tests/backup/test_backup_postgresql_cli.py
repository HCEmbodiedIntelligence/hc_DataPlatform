from __future__ import annotations

import json
from pathlib import Path

import pytest

from hc_data_platform.backup.postgresql import PostgresBackupError
from hc_data_platform.backup.postgresql_cli import _endpoint, _parser

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("command", ["logical-create", "physical-create"])
def test_cli_exposes_no_dsn_or_password_argument(
    command: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = _parser()
    with pytest.raises(SystemExit) as stopped:
        parser.parse_args([command, "--help"])
    assert stopped.value.code == 0
    help_text = capsys.readouterr().out
    lowered = help_text.lower()
    assert "password" not in lowered
    assert "dsn" not in lowered
    assert "database-url" not in lowered


def test_missing_endpoint_environment_is_stable_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "must-never-appear"
    monkeypatch.setenv("HC_BACKUP_POSTGRES_PASSWORD", secret)
    monkeypatch.delenv("HC_BACKUP_POSTGRES_HOST", raising=False)

    with pytest.raises(PostgresBackupError) as rejected:
        _endpoint()
    assert rejected.value.code == "BACKUP_POSTGRES_CONFIGURATION_INVALID"
    assert secret not in str(rejected.value)


def test_installed_cli_help_runs_without_loading_credentials(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as stopped:
        _parser().parse_args(["--help"])
    assert stopped.value.code == 0
    assert "logical-create" in capsys.readouterr().out


def test_cli_receipt_json_shape_has_no_connection_material(tmp_path: Path) -> None:
    # A static guard: receipts are artifact models, never endpoint/lease composites.
    from hc_data_platform.backup.postgresql import PhysicalBackupFile, PhysicalBaselineArtifact
    from hc_data_platform.backup.postgresql_cli import _write_receipt

    tmp_path.chmod(0o700)
    artifact = PhysicalBaselineArtifact(
        path=tmp_path / "physical",
        server_major=16,
        system_identifier=123,
        timeline_id=1,
        start_lsn="0/10",
        end_lsn="0/20",
        manifest_sha256="1" * 64,
        total_bytes=1,
        files=(
            PhysicalBackupFile(
                relative_path="backup_manifest",
                size_bytes=1,
                sha256="2" * 64,
            ),
        ),
        aggregate_sha256="3" * 64,
    )
    receipt = _write_receipt(
        artifact,
        staging_directory=tmp_path,
        receipt_name="physical-receipt.json",
    )
    parsed = json.loads(receipt.read_text(encoding="utf-8"))
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert "password" not in json.dumps(parsed).lower()
    assert "dsn" not in json.dumps(parsed).lower()
    assert parsed["aggregate_sha256"] == "3" * 64


def test_postgres_tools_are_isolated_in_a_nonroot_dedicated_image() -> None:
    dockerfile = (ROOT / "backend/Dockerfile").read_text(encoding="utf-8")
    pyproject = (ROOT / "backend/pyproject.toml").read_text(encoding="utf-8")
    stage = dockerfile.split("FROM runtime-base AS postgres-maintenance", maxsplit=1)[1].split(
        "FROM runtime-media AS worker", maxsplit=1
    )[0]
    assert (
        "postgres:16.10-bookworm@sha256:"
        "61c57e5eeda4d69232c97cb23ae5d95d170a1a2fd0b40a6b4f34fb56819b056d"
    ) in dockerfile
    for program in (
        "pg_basebackup",
        "pg_controldata",
        "pg_dump",
        "pg_restore",
        "pg_verifybackup",
        "pg_waldump",
    ):
        assert program in stage
    assert 'USER 65532:65532\nENTRYPOINT ["hc-postgres-backup"]' in stage
    assert 'hc-postgres-backup = "hc_data_platform.backup.postgresql_cli:main"' in pyproject
