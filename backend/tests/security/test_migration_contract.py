from pathlib import Path


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
    assert '"security/001_core.sql", "security/002_access_control.sql"' in manager


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
