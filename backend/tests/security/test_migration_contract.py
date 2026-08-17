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
