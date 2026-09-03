import re
from pathlib import Path

from hc_data_platform.security.passwords import PasswordHasher


def test_security_migration_defines_unique_idempotency_and_forced_rls_contract() -> None:
    migration = (Path(__file__).parents[2] / "migrations" / "security" / "001_core.sql").read_text(
        encoding="utf-8"
    )
    assert "PRIMARY KEY (project_id, region_code, scope_key, idempotency_key)" in migration
    assert "core.reconcile_project_rls()" in migration
    assert "FORCE ROW LEVEL SECURITY" in migration
    assert "WITH CHECK (core.scope_matches" in migration
    assert "app.project_id" in migration
    assert "app.region_code" in migration


def test_access_migration_separates_empty_accounts_from_reviewed_project_grants() -> None:
    migration = (
        Path(__file__).parents[2] / "migrations" / "security" / "002_access_control.sql"
    ).read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS access_control.accounts" in migration
    assert "CREATE TABLE IF NOT EXISTS access_control.membership_requests" in migration
    assert "CREATE TABLE IF NOT EXISTS access_control.capability_requests" in migration
    assert "CREATE TABLE IF NOT EXISTS access_control.capability_grants" in migration
    assert "capability_revision" in migration
    assert "membership_requests_one_pending_idx" in migration
    assert "access_audit_no_update_delete" in migration
    assert "ALTER TABLE %s NO FORCE ROW LEVEL SECURITY" in migration
    assert "ALTER TABLE %s DISABLE ROW LEVEL SECURITY" in migration
    account_block = migration.split(
        "CREATE TABLE IF NOT EXISTS access_control.accounts", maxsplit=1
    )[1].split(");", maxsplit=1)[0]
    assert "project_id" not in account_block
    assert "PENDING" not in account_block


def test_platform_super_admin_migration_is_role_based_idempotent_and_rls_aware() -> None:
    migration = (
        Path(__file__).parents[2] / "migrations" / "security" / "020_platform_super_admin.sql"
    ).read_text(encoding="utf-8")
    assert "'platform.admin'" in migration
    assert "platform.account.manage" in migration
    assert "capability_revision = account.capability_revision + 1" in migration
    assert "revocation_reason = 'ADMIN_REVOKED'" in migration
    assert "ON CONFLICT (principal_id, capability_key) DO UPDATE" in migration
    assert "WHERE NOT platform_grant.active" in migration
    assert "app.platform_admin" in migration
    assert "core.scope_matches" in migration
    assert "core.organization_scope_matches" in migration
    assert "canonical_username" not in migration
    assert "hc-admin" not in migration


def test_default_admin_is_seeded_only_for_an_empty_account_store() -> None:
    migration = (
        Path(__file__).parents[2] / "migrations" / "security" / "023_default_admin.sql"
    ).read_text(encoding="utf-8")

    assert "'hc_admin'" in migration
    assert "WHERE NOT EXISTS" in migration
    assert "FROM access_control.accounts" in migration
    assert "ON CONFLICT" not in migration
    for capability in (
        "platform.admin",
        "platform.account.read",
        "platform.account.manage",
        "platform.account_security.manage",
    ):
        assert f"'{capability}'" in migration

    encoded_password = re.search(r"'(scrypt\$[^']+)'", migration)
    assert encoded_password is not None
    assert PasswordHasher().verify("12345678", encoded_password.group(1))


def test_auth_abuse_migration_persists_only_keyed_state_and_bounded_cleanup_indexes() -> None:
    migration = (
        Path(__file__).parents[2] / "migrations" / "security" / "007_auth_abuse_protection.sql"
    ).read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS access_control.auth_login_states" in migration
    assert "CREATE TABLE IF NOT EXISTS access_control.auth_rate_limit_buckets" in migration
    assert "subject_key_hash char(64) PRIMARY KEY" in migration
    assert "bucket_key_hash char(64) NOT NULL" in migration
    assert "auth_login_states_expiry_idx" in migration
    assert "auth_rate_limit_buckets_expiry_idx" in migration
    assert "client_ip" not in migration.lower()
    assert "username text" not in migration.lower()


def test_access_migration_is_ordered_immediately_after_core_security() -> None:
    manifest = (Path(__file__).parents[2] / "migrations" / "manifest.txt").read_text(
        encoding="utf-8"
    )
    versions = [line for line in manifest.splitlines() if line and not line.startswith("#")]
    assert versions[:2] == [
        "security/001_core.sql",
        "security/002_access_control.sql",
    ]
    manager = (
        Path(__file__).parents[2] / "src" / "hc_data_platform" / "core" / "migrations.py"
    ).read_text(encoding="utf-8")
    repeatable_runner = manager.split("for version in (", maxsplit=1)[1].split("):", maxsplit=1)[0]
    core_index = repeatable_runner.index('"security/001_core.sql",')
    organization_index = repeatable_runner.index('"security/012_organization_aware_rls.sql",')
    access_index = repeatable_runner.index('"security/002_access_control.sql",')
    assert core_index < organization_index < access_index


def test_organization_scoped_access_upgrade_fails_closed_and_uses_exact_identity() -> None:
    migration = (
        Path(__file__).parents[2] / "migrations" / "security" / "011_organization_scoped_access.sql"
    ).read_text(encoding="utf-8")
    assert "count(project.organization_id) <> 1" in migration
    assert "requires exactly one registry organization" in migration
    assert "FOREIGN KEY (organization_id, project_id)" in migration
    assert "PRIMARY KEY (principal_id, organization_id, project_id)" in migration
    assert "membership_requests_organization_project_queue_idx" in migration
    assert "capability_grants_effective_organization_project_idx" in migration


def test_organization_aware_rls_matches_organization_before_project() -> None:
    migration = (
        Path(__file__).parents[2] / "migrations" / "security" / "012_organization_aware_rls.sql"
    ).read_text(encoding="utf-8")
    assert "core.organization_scope_matches" in migration
    assert "app.organization_id" in migration
    assert "has_organization_id AND has_region_code" in migration
    assert "organization_scope_matches(organization_id, project_id, region_code)" in migration
    assert "SELECT core.reconcile_project_rls();" in migration


def test_core_organization_upgrade_rekeys_security_ledgers_and_integrity_chain() -> None:
    migration = (
        Path(__file__).parents[2] / "migrations" / "security" / "018_core_organization_scope.sql"
    ).read_text(encoding="utf-8")
    assert "count(DISTINCT project.organization_id) <> 1" in migration
    assert "requires exactly one registry organization" in migration
    assert "idempotency_records_organization_project_fk" in migration
    assert "outbox_events_organization_project_fk" in migration
    assert "audit_events_organization_project_fk" in migration
    assert "organization_id, project_id, region_code, scope_key, idempotency_key" in migration
    assert "organization_id, project_id, region_code, sequence_no" in migration
    assert "'organization_id', event_organization_id" in migration
    assert (
        "CREATE INDEX audit_events_project_occurred_idx\n"
        "ON core.audit_events (\n    organization_id, project_id"
    ) in migration
    assert (
        "CREATE INDEX outbox_events_pending_idx\nON core.outbox_events (organization_id"
    ) in migration
    assert "audit_events_p19_organization_scope_keyset_idx" in migration
    assert "core.apply_project_rls('core.audit_integrity_entries'::regclass)" in migration


def test_robotics_organization_upgrade_rekeys_foreign_keys_and_fails_closed() -> None:
    migration = (
        Path(__file__).parents[2]
        / "migrations"
        / "robotics"
        / "0004_organization_scoped_robotics.sql"
    ).read_text(encoding="utf-8")
    assert "count(project.organization_id) <> 1" in migration
    assert "requires exactly one registry organization" in migration
    assert "PRIMARY KEY (organization_id, project_id, region_code, robot_id)" in migration
    assert "robot_components_robot_organization_fk" in migration
    assert "component_frames_component_organization_fk" in migration
    assert "component_channels_component_organization_fk" in migration
    assert "robot_maintenance_records_robot_organization_fk" in migration
    assert "robot_command_receipts_organization_project_fk" in migration
    assert "core.apply_project_rls('robotics.robot_instances'::regclass)" in migration


def test_calibration_organization_upgrade_rekeys_every_identity_edge() -> None:
    migration = (
        Path(__file__).parents[2]
        / "migrations"
        / "calibrations"
        / "0007_organization_scoped_calibrations.sql"
    ).read_text(encoding="utf-8")
    assert "requires exactly one registry organization" in migration
    assert "calibration_sets_organization_project_fk" in migration
    assert "calibration_publish_preflight_set_organization_fk" in migration
    assert "calibration_version_document_set_organization_fk" in migration
    assert "calibration_validation_report_document_organization_fk" in migration
    assert "calibration_dataset_association_document_organization_fk" in migration
    assert "PRIMARY KEY (organization_id, project_id, region_code, set_id)" in migration
    assert (
        "core.apply_project_rls('calibrations.calibration_dataset_version_associations'::regclass)"
        in migration
    )


def test_outbox_dispatch_migration_adds_lease_and_safe_retry_code() -> None:
    migration = (
        Path(__file__).parents[2] / "migrations" / "security" / "003_outbox_dispatch.sql"
    ).read_text(encoding="utf-8")
    assert "claim_token uuid" in migration
    assert "claimed_until timestamptz" in migration
    assert "last_error_code text" in migration
    assert "outbox_claim_tuple_check" in migration
    assert "outbox_events_claimable_idx" in migration
    assert "core.apply_project_rls('core.outbox_events'::regclass)" in migration


def test_account_profile_migration_is_forward_only_and_binds_session_credentials() -> None:
    migration = (
        Path(__file__).parents[2]
        / "migrations"
        / "security"
        / "005_account_profile_and_credentials.sql"
    ).read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS display_name" in migration
    assert "account_revision bigint NOT NULL DEFAULT 1" in migration
    assert "credential_revision bigint NOT NULL DEFAULT 1" in migration
    assert "password_changed_at" in migration
    assert "revocation_reason" in migration
    assert "result_payload jsonb" in migration
    assert "WHERE display_name IS NULL" in migration
    manifest = (Path(__file__).parents[2] / "migrations" / "manifest.txt").read_text(
        encoding="utf-8"
    )
    assert "security/005_account_profile_and_credentials.sql" in manifest


def test_session_lifecycle_migration_backfills_last_seen_without_rewriting_account_migration() -> (
    None
):
    migrations = Path(__file__).parents[2] / "migrations" / "security"
    lifecycle = (migrations / "006_session_lifecycle.sql").read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS last_seen_at timestamptz" in lifecycle
    assert "SET last_seen_at = issued_at" in lifecycle
    assert "ALTER COLUMN last_seen_at SET NOT NULL" in lifecycle
    assert "access_sessions_active_lifecycle_idx" in lifecycle
    assert "WHERE revoked_at IS NULL" in lifecycle
    manifest = (migrations.parent / "manifest.txt").read_text(encoding="utf-8")
    assert "security/006_session_lifecycle.sql\nsecurity/007_auth_abuse_protection.sql" in manifest


def test_notification_resource_migration_preserves_safe_decision_history() -> None:
    migrations = Path(__file__).parents[2] / "migrations" / "security"
    migration = (migrations / "013_account_notification_resources.sql").read_text(encoding="utf-8")
    assert "resource_type = 'ACCESS_REQUEST'" in migration
    assert "resource_id = access_request_id::text" in migration
    assert "event_key = 'access-request:' || kind" in migration
    assert "account_notifications_recipient_event_key_key" in migration
    assert "COLLECTION_TASK_CLOSED" in migration
    assert "DATASET_VERSION_PUBLISHED" in migration
    manifest = (migrations.parent / "manifest.txt").read_text(encoding="utf-8")
    assert "security/013_account_notification_resources.sql" in manifest
