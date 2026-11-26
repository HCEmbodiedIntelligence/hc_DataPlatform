import asyncio
import os
from uuid import uuid4
import psycopg
import pytest
from hc_data_platform.core.migrations import apply_migrations
from hc_data_platform.storage.directory_cleanup import DirectoryCleanup


def ensure_schema(dsn):
    with psycopg.connect(dsn) as db:
        exists = db.execute("SELECT to_regclass('core.schema_migrations')").fetchone()[0]
        if (
            exists
            and db.execute(
                "SELECT 1 FROM core.schema_migrations WHERE version='security/029_retired_business_records.sql'"
            ).fetchone()
        ):
            return
    asyncio.run(apply_migrations(dsn))


class Objects:
    bucket = "qa-bucket"
    local_artifacts = None

    def __init__(self):
        self.keys = set()
        self.fail = False

    def list(self, prefix):
        return [(key, 1) for key in self.keys if key.startswith(prefix)]

    def purge(self, manifest):
        if self.fail:
            raise OSError("unavailable")
        self.keys.difference_update(manifest["objects"])


def test_cleanup_deadline_retries_and_shared_files():
    dsn = os.environ.get("HC_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("isolated PostgreSQL required")
    ensure_schema(dsn)
    org = "files-" + uuid4().hex
    project = "same-project"
    other = org + "-other"
    objects = Objects()
    prefix = "oss-smoke/" + org + "/"
    objects.keys = {prefix + "exclusive", prefix + "shared", prefix + "other"}
    cleanup = DirectoryCleanup(dsn, lambda organization: ("test-store", objects))
    with psycopg.connect(dsn) as db:
        for owner in (org, other):
            db.execute(
                "INSERT INTO registry.organization_projects (organization_id,project_id,display_name) VALUES(%s,%s,%s)",
                (owner, project, project),
            )
        for owner, key in (
            (org, "exclusive"),
            (org, "shared"),
            (other, "shared"),
            (other, "other"),
        ):
            db.execute(
                """INSERT INTO storage.cache_test_copies (organization_id,project_id,region_code,object_key,original_key,size_bytes,sha256)
                VALUES(%s,%s,'test',%s,%s,1,%s)""",
                (owner, project, prefix + key, "raw/" + key, "1" * 64),
            )
        db.execute(
            """INSERT INTO access_control.retired_scopes(organization_id,project_id,display_name,status,purge_after)
            VALUES (%s,%s,'等待期限','DELETED',now()+interval '30 days')""",
            (org, project),
        )

    # Drive only the requested test job. Tests share a database with other integration cases.
    def step():
        with psycopg.connect(dsn) as db:
            return cleanup._process(db, org, project)

    assert not step()
    assert prefix + "exclusive" in objects.keys
    with psycopg.connect(dsn) as db:
        db.execute(
            "UPDATE access_control.retired_scopes SET purge_after=now() WHERE organization_id=%s",
            (org,),
        )
    assert step()
    objects.fail = True
    with pytest.raises(OSError):
        step()
    objects.fail = False
    assert step()
    assert step()
    assert not step()
    assert objects.keys == {prefix + "shared", prefix + "other"}
    with psycopg.connect(dsn) as db:
        assert db.execute(
            "SELECT count(*) FROM storage.cache_test_copies WHERE organization_id=%s", (org,)
        ).fetchone() == (0,)
        assert db.execute(
            "SELECT count(*) FROM storage.cache_test_copies WHERE organization_id=%s", (other,)
        ).fetchone() == (2,)
        assert db.execute(
            "SELECT completed_at IS NOT NULL FROM access_control.retired_scopes WHERE organization_id=%s",
            (org,),
        ).fetchone() == (True,)


def test_only_due_scope_can_remove_published_tag_schema():
    dsn = os.environ.get("HC_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("isolated PostgreSQL required")
    ensure_schema(dsn)
    first, second = str(uuid4()), str(uuid4())
    marker = uuid4().hex
    with psycopg.connect(dsn) as db:
        db.execute(
            "SELECT set_config('app.platform_admin','true',true),set_config('app.is_admin','true',true)"
        )
        for org, identity in [(first, first), (second, second)]:
            db.execute(
                "INSERT INTO registry.organization_projects(organization_id,project_id,display_name) VALUES(%s,%s,'QA')",
                (org, marker),
            )
            db.execute(
                """INSERT INTO annotation.tag_schema_versions
                (organization_id,project_id,schema_id,version,name,status,document,content_hash,created_by,published_by,published_at)
                VALUES(%s,%s,%s,1,'QA','PUBLISHED','{}',%s,'tester','tester',now())""",
                (org, marker, identity, "1" * 64),
            )
        with pytest.raises(psycopg.Error), db.transaction():
            db.execute("DELETE FROM annotation.tag_schema_versions WHERE schema_id=%s", (first,))
        db.execute(
            """INSERT INTO access_control.retired_scopes(organization_id,project_id,display_name,status,purge_after)
            VALUES (%s,%s,'QA','DELETED',now())""",
            (first, marker),
        )
        from hc_data_platform.storage.directory_records import delete_records

        delete_records(db, first, marker)
        assert not db.execute(
            "SELECT 1 FROM annotation.tag_schema_versions WHERE schema_id=%s", (first,)
        ).fetchone()
        with pytest.raises(psycopg.Error), db.transaction():
            db.execute("DELETE FROM annotation.tag_schema_versions WHERE schema_id=%s", (second,))
        assert db.execute(
            "SELECT 1 FROM annotation.tag_schema_versions WHERE schema_id=%s", (second,)
        ).fetchone()
