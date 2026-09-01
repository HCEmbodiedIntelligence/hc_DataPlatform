from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from hc_data_platform.core import migrations as migration_module
from hc_data_platform.core.migrations import (
    Migration,
    apply_migrations,
    load_migrations,
    migration_status,
)


class _RecordingTransaction:
    def __init__(self, connection: _RecordingConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> None:
        assert not self._connection.in_transaction
        self._connection.in_transaction = True

    async def __aexit__(self, *args: object) -> None:
        del args
        self._connection.in_transaction = False


class _RecordingConnection:
    def __init__(self) -> None:
        self.in_transaction = False
        self.executed: list[tuple[bool, str, tuple[object, ...]]] = []

    def transaction(self) -> _RecordingTransaction:
        return _RecordingTransaction(self)

    async def execute(self, query: str, *args: object) -> None:
        self.executed.append((self.in_transaction, query, args))


HISTORICAL_CHECKSUMS = {
    "annotation/0002_tag_schema_revisions.sql": (
        "3f033d4af5fbb53140c8e98e30b5c3d88e4c9ddf71157ecbc28d678a8a8a1ccd"
    ),
    "storage/0001_storage_governance.sql": (
        "4f07ef2d609958203a2af30a7ed37cebff3eb5cb8d21755a4c22b90fb36f5e8f"
    ),
    "security/005_account_profile_and_credentials.sql": (
        "703ed47c307abd85ad876b91c5e4de755850568f786f0e68570de51b26fe9e73"
    ),
    "security/006_session_lifecycle.sql": (
        "510db7b18bc9fbfa71904bdc97a5a611c4445e7c923af1806ec2c7fbdf4bbb7d"
    ),
    "security/007_auth_abuse_protection.sql": (
        "7d4cdb817da4ff5fe937df11fb39aa786699f8c6a24f72f31e873abcef0c9311"
    ),
}

EXPECTED_MANIFEST = (
    "security/001_core.sql",
    "security/002_access_control.sql",
    "ingest/001_ingest.sql",
    "ingest/002_package_manifest_uploads.sql",
    "workflow/001_jobs.sql",
    "workflow/002_job_recovery.sql",
    "verification/0001_verification_reports.sql",
    "verification/0002_runtime_scope.sql",
    "quality/0001_quality_reports.sql",
    "alignment/0001_alignment_fragments.sql",
    "alignment/0002_runtime_manifest.sql",
    "lance_catalog/0001_lance_catalog.sql",
    "annotation/0001_annotation.sql",
    "publishing/0001_publishing.sql",
    "verification/0003_tenant_report_identity.sql",
    "quality/0002_tenant_report_identity.sql",
    "alignment/0003_ready_attempt_immutable.sql",
    "collection_tasks/0001_collection_tasks.sql",
    "storage/0001_storage_governance.sql",
    "annotation/0002_tag_schema_revisions.sql",
    "dashboard/0001_dashboard_query_indexes.sql",
    "security/003_outbox_dispatch.sql",
    "ingest/003_automatic_workflow_trigger.sql",
    "annotation/0003_automatic_tasks.sql",
    "publishing/0002_rollout_publication_region_lineage.sql",
    "dashboard/0002_dashboard_business_feed_indexes.sql",
    "storage/0002_seal_inventory_snapshots.sql",
    "annotation/0004_backfill_task_base_step_count.sql",
    "registry/0001_robot_model_registry.sql",
    "registry/0002_robot_model_assets.sql",
    "robotics/0001_robot_directory.sql",
    "calibrations/0001_calibration_reads.sql",
    "calibrations/0002_publish_preflights.sql",
    "data_schemas/0001_data_schema_reads.sql",
    "data_schemas/0002_schema_authoring.sql",
    "data_sources/0001_data_source_registry.sql",
    "dataset_registry/0001_dataset_page_registry.sql",
    "dataset_registry/0002_dataset_detail_projections.sql",
    "dataset_registry/0003_dataset_version_review_and_delivery.sql",
    "manual_cleaning/0001_manual_issue_registry.sql",
    "manual_cleaning/0002_cleaning_draft_read_projections.sql",
    "manual_cleaning/0003_cleaning_workbench.sql",
    "manual_cleaning/0004_current_cleaning_draft_cutover.sql",
    "security/004_audit_read_projection.sql",
    "security/005_account_profile_and_credentials.sql",
    "security/006_session_lifecycle.sql",
    "security/007_auth_abuse_protection.sql",
    "annotation/0005_revision_thread_index.sql",
    "collection_tasks/0002_task_rules.sql",
    "collection_tasks/0003_positive_target_dimensions.sql",
    "registry/0003_robot_model_publication_controls.sql",
    "registry/0004_robot_model_command_receipts.sql",
    "robotics/0002_robot_management.sql",
    "robotics/0003_component_management.sql",
    "calibrations/0003_version_documents.sql",
    "calibrations/0004_publish_preflight_blockers.sql",
    "calibrations/0005_recalibration_versions.sql",
    "calibrations/0006_dataset_version_associations.sql",
    "calibrations/0007_organization_scoped_calibrations.sql",
    "data_schemas/0003_dataset_version_references.sql",
    "security/008_audit_integrity_chain.sql",
    "security/009_account_notifications.sql",
    "security/010_audit_integrity_trigger_privileges.sql",
    "security/011_organization_scoped_access.sql",
    "security/012_organization_aware_rls.sql",
    "robotics/0004_organization_scoped_robotics.sql",
    "security/013_account_notification_resources.sql",
    "collection_tasks/0004_organization_scoped_collection_tasks.sql",
    "security/014_account_recovery.sql",
    "security/015_platform_account_administration.sql",
    "storage/0003_object_operations_and_execution.sql",
    "storage/0004_approval_bound_production_execution.sql",
    "storage/0005_schedule_execution_lineage.sql",
    "security/016_audit_governance.sql",
    "annotation/0006_auto_annotation_jobs.sql",
    "security/017_audit_governance_organization_scope.sql",
    "annotation/0007_annotation_restore_origin.sql",
    "security/018_core_organization_scope.sql",
    "security/019_product_organization_scope.sql",
    "annotation/0008_scoped_legacy_cleaning_import.sql",
    "security/020_platform_super_admin.sql",
    "ingest/004_device_capture_facts.sql",
    "security/021_personal_account_access.sql",
    "registry/0005_robot_model_drafts.sql",
    "registry/0006_robot_model_creation.sql",
    "registry/0007_robot_binding_scope_contract.sql",
    "dataset_registry/0004_collection_task_dataset_identity.sql",
    "dataset_registry/0005_dataset_folders_and_task_assignment.sql",
    "dataset_registry/0006_dataset_task_membership_lookup.sql",
    "dataset_registry/0007_cross_region_dataset_reassignment_guard.sql",
    "ingest/005_source_recording_identity.sql",
    "ingest/006_grandfather_completed_source_recordings.sql",
    "preview/0001_durable_preview_artifacts.sql",
    "annotation/0009_frame_selection_manifests.sql",
    "dataset_registry/0008_dataset_version_semantics.sql",
    "annotation/0010_episode_review_versions.sql",
    "publishing/0003_episode_version_finalization.sql",
    "preview/0002_publication_leases_and_timeline.sql",
    "annotation/0011_frame_selection_lifecycle.sql",
    "preview/0003_global_media_capacity.sql",
    "annotation/0012_frame_selection_reference_guards.sql",
    "platform/001_platform_instances.sql",
    "platform/002_maintenance_operations.sql",
    "security/022_platform_operation_capabilities.sql",
    "security/023_default_admin.sql",
    "platform/003_backup_catalog.sql",
    "platform/004_backup_catalog_verification_and_query.sql",
    "platform/005_runtime_config_revisions.sql",
    "platform/006_platform_audit_integrity.sql",
    "platform/007_release_control.sql",
    "platform/008_platform_task_leases.sql",
    "preview/0004_canonical_aligned_media.sql",
    "ingest/007_continuous_recording_slices.sql",
    "ingest/008_recording_video_assets.sql",
    "preview/0005_commit_fence_and_media_retirement.sql",
    "platform/009_object_store_configuration.sql",
    "registry/0008_project_display_names.sql",
    "ingest/009_continuous_episode_workflows.sql",
    "preview/0006_dataset_version_media_retirement.sql",
    "robotics/0005_organization_robot_assets.sql",
    "ingest/010_raw_sources.sql",
    "collection_tasks/0005_robot_upload_target.sql",
    "ingest/012_robot_ingest.sql",
)


def test_manifest_is_ordered_unique_and_checksum_bound() -> None:
    migrations = load_migrations()
    assert tuple(migration.version for migration in migrations) == EXPECTED_MANIFEST
    assert len({migration.version for migration in migrations}) == len(migrations)
    for migration in migrations:
        assert migration.checksum_sha256 == hashlib.sha256(migration.path.read_bytes()).hexdigest()


def test_phase_manifest_assigns_every_current_migration_to_expand() -> None:
    migrations = load_migrations()

    assert migrations
    assert {migration.phase for migration in migrations} == {"expand"}


@pytest.mark.asyncio
async def test_contract_phase_rejects_missing_or_invalid_approval_before_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected_connect(dsn: str) -> object:
        del dsn
        raise AssertionError("database connection must not be attempted")

    monkeypatch.setattr(migration_module.asyncpg, "connect", unexpected_connect)
    for approval_digest in (None, "", "sha256:not-a-digest", "a" * 64):
        with pytest.raises(RuntimeError, match="signed approval digest"):
            await apply_migrations(
                "postgresql://localhost/test",
                phase="contract",
                contract_approval_digest=approval_digest,
            )


def test_applied_history_bytes_are_immutable() -> None:
    migrations = {migration.version: migration for migration in load_migrations()}
    for version, checksum in HISTORICAL_CHECKSUMS.items():
        assert migrations[version].checksum_sha256 == checksum


def test_manifest_rejects_path_escape(tmp_path: Path) -> None:
    (tmp_path / "manifest.txt").write_text("../outside.sql\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="invalid migration path"):
        load_migrations(tmp_path)


@pytest.mark.asyncio
async def test_migration_status_rejects_unknown_applied_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeConnection:
        async def fetchval(self, query: str) -> bool:
            del query
            return True

        async def fetch(self, query: str) -> list[dict[str, str]]:
            del query
            return [
                {"version": "known.sql", "checksum_sha256": "known-checksum"},
                {"version": "unknown.sql", "checksum_sha256": "unknown-checksum"},
            ]

        async def close(self) -> None:
            return None

    async def connect(dsn: str) -> FakeConnection:
        del dsn
        return FakeConnection()

    monkeypatch.setattr(migration_module.asyncpg, "connect", connect)
    monkeypatch.setattr(
        migration_module,
        "expected_migration_checksums",
        lambda: {"known.sql": "known-checksum"},
    )

    status = await migration_status("postgresql://localhost/test")

    assert status["status"] == "not_current"
    assert status["unknown"] == ["unknown.sql"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("applied", "error"),
    (
        (
            {"security/001_core.sql": "wrong-checksum"},
            "applied migration checksum drift: security/001_core.sql",
        ),
        ({"unknown.sql": "unknown-checksum"}, "database contains migrations absent"),
    ),
)
async def test_upgrade_rejects_checksum_drift_and_unknown_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    applied: dict[str, str],
    error: str,
) -> None:
    class FakeTransaction:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *args: object) -> None:
            del args

    class FakeConnection:
        def transaction(self) -> FakeTransaction:
            return FakeTransaction()

        async def execute(self, query: str, *args: object) -> None:
            del query, args

        async def fetchval(self, query: str) -> bool:
            del query
            return True

        async def fetch(self, query: str) -> list[dict[str, str]]:
            del query
            return [
                {"version": version, "checksum_sha256": checksum}
                for version, checksum in applied.items()
            ]

        async def close(self) -> None:
            return None

    async def connect(dsn: str) -> FakeConnection:
        del dsn
        return FakeConnection()

    migration = Migration(
        version="security/001_core.sql",
        path=tmp_path / "security-001.sql",
        checksum_sha256="expected-checksum",
        sql="SELECT 1",
    )
    monkeypatch.setattr(migration_module.asyncpg, "connect", connect)
    monkeypatch.setattr(migration_module, "load_migrations", lambda: (migration,))

    with pytest.raises(RuntimeError, match=error):
        await apply_migrations("postgresql://localhost/test")


def _expected_session_lifecycle_index_state() -> migration_module._SessionLifecycleIndexState:
    return migration_module._SessionLifecycleIndexState(
        target_relation=True,
        valid=True,
        ready=True,
        unique=False,
        access_method="btree",
        key_count=3,
        total_count=5,
        columns=(
            "principal_id",
            "issued_at",
            "session_id",
            "last_seen_at",
            "credential_revision",
        ),
        predicate="(revoked_at IS NULL)",
    )


@pytest.mark.parametrize(
    "state",
    (
        replace(_expected_session_lifecycle_index_state(), target_relation=False),
        replace(_expected_session_lifecycle_index_state(), unique=True),
        replace(_expected_session_lifecycle_index_state(), access_method="hash"),
        replace(_expected_session_lifecycle_index_state(), key_count=4),
        replace(
            _expected_session_lifecycle_index_state(),
            columns=(
                "principal_id",
                "issued_at",
                "session_id",
                "credential_revision",
                "last_seen_at",
            ),
        ),
        replace(
            _expected_session_lifecycle_index_state(),
            predicate="((revoked_at IS NULL) AND (issued_at IS NOT NULL))",
        ),
    ),
)
def test_session_lifecycle_online_index_requires_exact_catalog_shape(
    state: migration_module._SessionLifecycleIndexState,
) -> None:
    assert migration_module._session_lifecycle_index_matches(
        _expected_session_lifecycle_index_state()
    )
    assert not migration_module._session_lifecycle_index_matches(state)


@pytest.mark.asyncio
async def test_session_lifecycle_index_is_created_concurrently_outside_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _RecordingConnection()
    created = False

    async def index_state(
        _: object,
    ) -> migration_module._SessionLifecycleIndexState | None:
        return _expected_session_lifecycle_index_state() if created else None

    original_execute = connection.execute

    async def execute(query: str, *args: object) -> None:
        nonlocal created
        if "CREATE INDEX CONCURRENTLY" in query:
            assert not connection.in_transaction
            created = True
        await original_execute(query, *args)

    connection.execute = execute  # type: ignore[method-assign]
    monkeypatch.setattr(migration_module, "_session_lifecycle_index_state", index_state)

    await migration_module._create_session_lifecycle_index(connection)  # type: ignore[arg-type]

    create_commands = [
        item for item in connection.executed if "CREATE INDEX CONCURRENTLY" in item[1]
    ]
    assert len(create_commands) == 1
    assert create_commands[0][0] is False


@pytest.mark.asyncio
async def test_session_lifecycle_online_index_never_drops_decoy_relation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _RecordingConnection()
    decoy = replace(
        _expected_session_lifecycle_index_state(),
        target_relation=False,
        valid=False,
        ready=False,
    )

    async def index_state(_: object) -> migration_module._SessionLifecycleIndexState:
        return decoy

    monkeypatch.setattr(migration_module, "_session_lifecycle_index_state", index_state)

    with pytest.raises(RuntimeError, match="unexpected target relation"):
        await migration_module._create_session_lifecycle_index(connection)  # type: ignore[arg-type]
    assert not any("DROP INDEX" in command for _, command, _ in connection.executed)


@pytest.mark.asyncio
async def test_session_lifecycle_online_prepare_removes_leftover_helper_before_backfill() -> None:
    connection = _RecordingConnection()

    await migration_module._prepare_session_lifecycle_column(connection)  # type: ignore[arg-type]

    ddl = " ".join(command for _, command, _ in connection.executed)
    assert "DROP CONSTRAINT IF EXISTS sessions_last_seen_at_online_not_null" in ddl
    assert "ADD CONSTRAINT sessions_last_seen_at_online_not_null" not in ddl


@pytest.mark.asyncio
async def test_session_lifecycle_online_install_rejects_wrong_helper_constraint() -> None:
    class FakeConnection(_RecordingConnection):
        async def fetchrow(self, query: str, *args: object) -> dict[str, object]:
            del query, args
            return {"is_check": True, "expression": "last_seen_at IS NULL"}

    connection = FakeConnection()

    with pytest.raises(RuntimeError, match="helper constraint has an unexpected definition"):
        await migration_module._install_session_lifecycle_not_null_check(connection)  # type: ignore[arg-type]
    assert connection.in_transaction is False


@pytest.mark.asyncio
async def test_session_lifecycle_schema_verifier_rejects_leftover_helper_constraint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeConnection(_RecordingConnection):
        async def fetchrow(self, query: str, *args: object) -> dict[str, object]:
            del query, args
            return {
                "attnotnull": True,
                "data_type": "timestamp with time zone",
                "default_expression": "now()",
                "fully_backfilled": True,
                "helper_absent": False,
            }

    async def index_state(
        _: object,
    ) -> migration_module._SessionLifecycleIndexState:
        return _expected_session_lifecycle_index_state()

    connection = FakeConnection()
    monkeypatch.setattr(migration_module, "_session_lifecycle_index_state", index_state)

    with pytest.raises(RuntimeError, match="did not reach the expected schema"):
        await migration_module._verify_session_lifecycle_schema(connection)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_session_lifecycle_finalizer_keeps_helper_during_set_not_null() -> None:
    class CountingConnection(_RecordingConnection):
        def __init__(self) -> None:
            super().__init__()
            self.transaction_calls = 0

        def transaction(self) -> _RecordingTransaction:
            self.transaction_calls += 1
            return super().transaction()

    connection = CountingConnection()

    await migration_module._finalize_session_lifecycle_column(connection)  # type: ignore[arg-type]

    alter_commands = [
        (in_transaction, " ".join(command.split()))
        for in_transaction, command, _ in connection.executed
        if "ALTER TABLE access_control.sessions" in command
    ]
    assert connection.transaction_calls == 3
    assert len(alter_commands) == 3
    assert all(in_transaction for in_transaction, _ in alter_commands)
    assert "VALIDATE CONSTRAINT sessions_last_seen_at_online_not_null" in alter_commands[0][1]
    assert "ALTER COLUMN last_seen_at SET NOT NULL" in alter_commands[1][1]
    assert "DROP CONSTRAINT" not in alter_commands[1][1]
    assert (
        "DROP CONSTRAINT IF EXISTS sessions_last_seen_at_online_not_null" in (alter_commands[2][1])
    )


@pytest.mark.asyncio
async def test_session_lifecycle_online_index_recovers_invalid_concurrent_leftover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _RecordingConnection()
    state: migration_module._SessionLifecycleIndexState | None = replace(
        _expected_session_lifecycle_index_state(),
        valid=False,
        ready=False,
    )

    async def index_state(_: object) -> migration_module._SessionLifecycleIndexState | None:
        return state

    original_execute = connection.execute

    async def execute(query: str, *args: object) -> None:
        nonlocal state
        if "DROP INDEX CONCURRENTLY" in query:
            assert not connection.in_transaction
            state = None
        elif "CREATE INDEX CONCURRENTLY" in query:
            assert not connection.in_transaction
            state = _expected_session_lifecycle_index_state()
        await original_execute(query, *args)

    connection.execute = execute  # type: ignore[method-assign]
    monkeypatch.setattr(migration_module, "_session_lifecycle_index_state", index_state)

    await migration_module._create_session_lifecycle_index(connection)  # type: ignore[arg-type]

    concurrent_commands = [
        command
        for in_transaction, command, _ in connection.executed
        if "INDEX CONCURRENTLY" in command and not in_transaction
    ]
    assert len(concurrent_commands) == 2
    assert "DROP INDEX CONCURRENTLY" in concurrent_commands[0]
    assert "CREATE INDEX CONCURRENTLY" in concurrent_commands[1]


@pytest.mark.asyncio
async def test_session_lifecycle_online_executor_is_resumable_and_records_ledger_last(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    migration = Migration(
        version=migration_module._SESSION_LIFECYCLE_VERSION,
        path=tmp_path / "006.sql",
        checksum_sha256=migration_module._SESSION_LIFECYCLE_CHECKSUM,
        sql="IMMUTABLE_006_MUST_NOT_BE_EXECUTED_VERBATIM",
    )
    connection = _RecordingConnection()
    phases: list[str] = []
    fail_backfill = True

    async def prepare(_: object) -> None:
        phases.append("prepare")

    async def backfill(_: object) -> None:
        nonlocal fail_backfill
        phases.append("backfill")
        if fail_backfill:
            raise RuntimeError("injected online backfill interruption")

    async def create_index(_: object) -> None:
        phases.append("index")

    async def install_check(_: object) -> None:
        phases.append("check")

    async def finalize(_: object) -> None:
        phases.append("finalize")

    async def verify(_: object) -> None:
        phases.append("verify")

    monkeypatch.setattr(migration_module, "_prepare_session_lifecycle_column", prepare)
    monkeypatch.setattr(migration_module, "_backfill_session_lifecycle_column", backfill)
    monkeypatch.setattr(migration_module, "_create_session_lifecycle_index", create_index)
    monkeypatch.setattr(
        migration_module,
        "_install_session_lifecycle_not_null_check",
        install_check,
    )
    monkeypatch.setattr(migration_module, "_finalize_session_lifecycle_column", finalize)
    monkeypatch.setattr(migration_module, "_verify_session_lifecycle_schema", verify)

    with pytest.raises(RuntimeError, match="injected online backfill interruption"):
        await migration_module._apply_pending_migration(connection, migration)  # type: ignore[arg-type]
    assert connection.executed == []

    fail_backfill = False
    await migration_module._apply_pending_migration(connection, migration)  # type: ignore[arg-type]

    assert phases == [
        "prepare",
        "backfill",
        "prepare",
        "backfill",
        "index",
        "check",
        "finalize",
        "verify",
    ]
    assert len(connection.executed) == 1
    in_transaction, ledger_sql, ledger_args = connection.executed[0]
    assert in_transaction is True
    assert "INSERT INTO core.schema_migrations" in ledger_sql
    assert ledger_args == (migration.version, migration.checksum_sha256)
    assert migration.sql not in ledger_sql


@pytest.mark.asyncio
async def test_session_lifecycle_online_executor_rejects_unexpected_immutable_hash(
    tmp_path: Path,
) -> None:
    migration = Migration(
        version=migration_module._SESSION_LIFECYCLE_VERSION,
        path=tmp_path / "006.sql",
        checksum_sha256="0" * 64,
        sql="SELECT 1",
    )
    connection = _RecordingConnection()

    with pytest.raises(RuntimeError, match="does not match its online executor checksum"):
        await migration_module._apply_pending_migration(connection, migration)  # type: ignore[arg-type]
    assert connection.executed == []


@pytest.mark.asyncio
async def test_already_applied_session_lifecycle_skips_online_executor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    migrations = (
        Migration("security/001_core.sql", tmp_path / "001.sql", "1" * 64, "SELECT 1"),
        Migration("security/002_access_control.sql", tmp_path / "002.sql", "2" * 64, "SELECT 2"),
        Migration(
            "security/012_organization_aware_rls.sql",
            tmp_path / "012.sql",
            "3" * 64,
            "SELECT 3",
        ),
        Migration(
            "security/019_product_organization_scope.sql",
            tmp_path / "019.sql",
            "4" * 64,
            "SELECT 4",
        ),
        Migration(
            "security/020_platform_super_admin.sql",
            tmp_path / "020.sql",
            "5" * 64,
            "SELECT 5",
        ),
        Migration(
            migration_module._SESSION_LIFECYCLE_VERSION,
            tmp_path / "006.sql",
            migration_module._SESSION_LIFECYCLE_CHECKSUM,
            "IMMUTABLE_006_MUST_NOT_BE_EXECUTED_VERBATIM",
        ),
    )
    applied = {migration.version: migration.checksum_sha256 for migration in migrations}

    class FakeConnection(_RecordingConnection):
        async def fetchval(self, query: str) -> bool:
            del query
            return True

        async def fetch(self, query: str) -> list[dict[str, str]]:
            del query
            return [
                {"version": version, "checksum_sha256": checksum}
                for version, checksum in applied.items()
            ]

        async def close(self) -> None:
            return None

    connection = FakeConnection()

    async def connect(_: str) -> FakeConnection:
        return connection

    async def unexpected_executor(*_: object) -> None:
        raise AssertionError("already-applied 006 must skip its online executor")

    monkeypatch.setattr(migration_module.asyncpg, "connect", connect)
    monkeypatch.setattr(migration_module, "load_migrations", lambda: migrations)
    monkeypatch.setattr(migration_module, "_apply_session_lifecycle_online", unexpected_executor)

    assert await apply_migrations("postgresql://localhost/test") == []
    assert all("IMMUTABLE_006" not in command[1] for command in connection.executed)
    assert "pg_advisory_lock" in connection.executed[0][1]
    assert connection.executed[0][0] is False
    assert "pg_advisory_unlock" in connection.executed[-1][1]
    assert connection.executed[-1][0] is False
    assert all("pg_advisory_xact_lock" not in command[1] for command in connection.executed)


@pytest.mark.asyncio
async def test_session_advisory_lock_is_released_when_upgrade_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    migration = Migration(
        "security/001_core.sql",
        tmp_path / "001.sql",
        "1" * 64,
        "SELECT 1",
    )

    class FakeConnection(_RecordingConnection):
        async def fetchval(self, query: str) -> bool:
            del query
            return True

        async def fetch(self, query: str) -> list[dict[str, str]]:
            del query
            return [
                {
                    "version": migration.version,
                    "checksum_sha256": "different-checksum",
                }
            ]

        async def close(self) -> None:
            return None

    connection = FakeConnection()

    async def connect(_: str) -> FakeConnection:
        return connection

    monkeypatch.setattr(migration_module.asyncpg, "connect", connect)
    monkeypatch.setattr(migration_module, "load_migrations", lambda: (migration,))

    with pytest.raises(RuntimeError, match="applied migration checksum drift"):
        await apply_migrations("postgresql://localhost/test")

    assert "pg_advisory_lock" in connection.executed[0][1]
    assert "pg_advisory_unlock" in connection.executed[-1][1]
    assert connection.executed[0][0] is connection.executed[-1][0] is False
    assert all("pg_advisory_xact_lock" not in command[1] for command in connection.executed)
