from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from hc_data_platform.backup.whole import WholeBackupError
from hc_data_platform.backup.whole_cli import InProcessStageInvoker, _parser, main, run


def test_whole_cli_exposes_backup_and_restore_plan_without_secret_arguments() -> None:
    parser = _parser()
    help_text = parser.format_help()
    backup = parser._subparsers._group_actions[0].choices["backup"]
    help_text += backup.format_help()
    commands = backup._subparsers._group_actions[0].choices
    for name in ("create", "list", "verify"):
        help_text += commands[name].format_help()
    restore = parser._subparsers._group_actions[0].choices["restore"]
    help_text += restore.format_help()
    help_text += restore._subparsers._group_actions[0].choices["plan"].format_help()
    help_text += restore._subparsers._group_actions[0].choices["execute"].format_help()
    help_text += restore._subparsers._group_actions[0].choices["reconcile"].format_help()
    assert "create" in help_text
    assert "list" in help_text
    assert "verify" in help_text
    assert "restore" in help_text
    assert "--dry-run" in help_text
    for forbidden in (
        "--password",
        "--token",
        "--secret-key",
        "--access-key",
        "--kms-key-id",
        "--age-recipient",
        "--dsn",
    ):
        assert forbidden not in help_text


def test_whole_cli_dispatches_all_backup_operations(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    plan = tmp_path / "plan.json"
    staging = tmp_path / "staging"
    helm = tmp_path / "helm.yaml"
    seen: list[str] = []

    def create(*_: object, **__: object) -> dict[str, object]:
        seen.append("create")
        return {"status": "created", "backup_id": "backup-1"}

    def listing(*_: object, **__: object) -> dict[str, object]:
        seen.append("list")
        return {"status": "listed", "count": 0}

    def verify(*_: object, **__: object) -> dict[str, object]:
        seen.append("verify")
        return {"status": "verified", "backup_id": "backup-1"}

    def restore_plan(*_: object, **__: object) -> dict[str, object]:
        seen.append("restore-plan")
        return {"status": "PREFLIGHT_PASSED", "backup_id": "backup-1"}

    def restore_execute(*_: object, **__: object) -> dict[str, object]:
        seen.append("restore-execute")
        return {"status": "READ_ONLY_READY", "backup_id": "backup-1"}

    def restore_reconcile(*_: object, **__: object) -> dict[str, object]:
        seen.append("restore-reconcile")
        return {"status": "RECONCILIATION_PASSED", "backup_id": "backup-1"}

    monkeypatch.setattr("hc_data_platform.backup.whole_cli._create", create)
    monkeypatch.setattr("hc_data_platform.backup.whole_cli._list_backups", listing)
    monkeypatch.setattr("hc_data_platform.backup.whole_cli._verify", verify)
    monkeypatch.setattr("hc_data_platform.backup.restore_cli.plan", restore_plan)
    monkeypatch.setattr("hc_data_platform.backup.restore_cli.execute", restore_execute)
    monkeypatch.setattr("hc_data_platform.backup.restore_cli.reconcile", restore_reconcile)

    assert (
        run(
            [
                "backup",
                "create",
                "--plan",
                str(plan),
                "--staging-directory",
                str(staging),
                "--helm-rendered-manifest",
                str(helm),
            ]
        )
        == 0
    )
    assert run(["backup", "list"]) == 0
    assert (
        run(
            [
                "backup",
                "verify",
                "--plan",
                str(plan),
                "--operation-id",
                "verify-1",
                "--verified-at",
                "2026-08-29T00:00:00Z",
                "--staging-directory",
                str(staging),
            ]
        )
        == 0
    )
    assert (
        run(
            [
                "restore",
                "plan",
                "backup-1",
                "--target",
                "rehearsal",
                "--backup-plan",
                str(plan),
                "--target-document",
                str(tmp_path / "restore-target.json"),
                "--staging-directory",
                str(staging),
                "--dry-run",
            ]
        )
        == 0
    )
    assert (
        run(
            [
                "restore",
                "execute",
                "backup-1",
                "--target",
                "rehearsal",
                "--backup-plan",
                str(plan),
                "--target-document",
                str(tmp_path / "restore-target.json"),
                "--restore-plan",
                str(tmp_path / "restore-plan.json"),
                "--approval",
                str(tmp_path / "restore-approval.json"),
                "--approval-signature",
                str(tmp_path / "restore-approval.sig"),
                "--checkpoint",
                str(tmp_path / "restore-checkpoint.json"),
                "--staging-directory",
                str(staging),
            ]
        )
        == 0
    )
    assert (
        run(
            [
                "restore",
                "reconcile",
                "backup-1",
                "--target",
                "rehearsal",
                "--backup-plan",
                str(plan),
                "--target-document",
                str(tmp_path / "restore-target.json"),
                "--restore-plan",
                str(tmp_path / "restore-plan.json"),
                "--checkpoint",
                str(tmp_path / "restore-checkpoint.json"),
                "--staging-directory",
                str(staging),
                "--report",
                str(tmp_path / "restore-reconciliation.json"),
                "--reconciled-at",
                "2026-08-29T06:00:00Z",
            ]
        )
        == 0
    )
    assert seen == [
        "create",
        "list",
        "verify",
        "restore-plan",
        "restore-execute",
        "restore-reconcile",
    ]
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [item["status"] for item in output] == [
        "created",
        "listed",
        "verified",
        "PREFLIGHT_PASSED",
        "READ_ONLY_READY",
        "RECONCILIATION_PASSED",
    ]


def test_in_process_stage_invoker_rejects_secret_shaped_or_duplicate_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def secret_stage(_: object) -> int:
        print('{"status":"verified","api_token":"must-not-cross-boundary"}')
        return 0

    monkeypatch.setitem(
        __import__("hc_data_platform.backup.whole_cli", fromlist=["_STAGE_RUNNERS"])._STAGE_RUNNERS,
        "objects",
        secret_stage,
    )
    with pytest.raises(WholeBackupError) as captured:
        InProcessStageInvoker().invoke("objects", ("verify",))
    assert captured.value.code == "BACKUP_WHOLE_STAGE_RESPONSE_INVALID"
    assert "must-not-cross-boundary" not in str(captured.value)


def test_whole_cli_failure_is_code_only_and_redacts_environment_secret(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "catalog-password-that-must-never-print"
    monkeypatch.setenv("HC_BACKUP_CATALOG_DSN", secret)
    monkeypatch.setattr(
        sys,
        "argv",
        ["hc-platform", "backup", "list", "--source-environment-id", "acceptance"],
    )
    monkeypatch.delenv("HC_BACKUP_SOURCE_ENVIRONMENT_ID", raising=False)

    with pytest.raises(SystemExit) as captured:
        main()

    assert captured.value.code in {2, 3}
    output = capsys.readouterr()
    assert secret not in output.err
    assert output.out == ""


def test_dockerfile_has_pinned_nonroot_whole_backup_target() -> None:
    dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text(encoding="utf-8")
    assert "AS backup-maintenance-builder" in dockerfile
    assert "AS backup-maintenance" in dockerfile
    assert "--extra backup" in dockerfile
    assert "age-v1.3.1-linux-amd64.tar.gz" in dockerfile
    assert "/usr/lib/postgresql/16/bin/pg_dump" in dockerfile
    assert "/usr/lib/postgresql/16/bin/pg_restore" in dockerfile
    assert 'org.opencontainers.image.component="backup-maintenance"' in dockerfile
    assert 'USER 65532:65532\nENTRYPOINT ["hc-platform"]' in dockerfile
