"""Real PostgreSQL/MinIO merge tests; only disposable databases and buckets are changed."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import boto3
import psycopg
import pytest
from botocore.config import Config
from psycopg import sql
from psycopg.conninfo import make_conninfo

from hc_data_platform.backup import merge_bundle
from hc_data_platform.backup.merge_bundle import (
    check_objects,
    copy_objects,
    export_bundle,
    load_bundle,
)
from hc_data_platform.backup.merge_database import MergeError, merge_database
from hc_data_platform.core.migrations import apply_migrations

ADMIN = "617856ca-3688-4393-a7a9-0318f4c27b7b"
KEY = "test-data-source-key"
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def template():
    dsn = os.environ.get("HC_MERGE_TEST_DSN")
    if not dsn:
        pytest.skip("HC_MERGE_TEST_DSN must point at a disposable PostgreSQL instance")
    name = "merge_template_" + uuid4().hex[:12]
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        asyncio.run(apply_migrations(urlunsplit(urlsplit(dsn)._replace(path="/" + name))))
        yield dsn, name
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def stores(template):
    dsn, template_name = template
    names = ["merge_test_" + uuid4().hex[:12] for _ in range(2)]
    connections = []
    bucket = "merge-" + uuid4().hex
    clients = []
    try:
        with psycopg.connect(dsn, autocommit=True) as admin:
            for name in names:
                admin.execute(
                    sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                        sql.Identifier(name), sql.Identifier(template_name)
                    )
                )
                connections.append(
                    psycopg.connect(make_conninfo(dsn, dbname=name), autocommit=True)
                )
        for var in ("HC_MERGE_TEST_S3_SOURCE", "HC_MERGE_TEST_S3_TARGET"):
            endpoint = os.environ.get(var)
            if not endpoint:
                pytest.skip(f"{var} must point at a disposable MinIO instance")
            client = boto3.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id="minio",
                aws_secret_access_key="merge-test-only",
                region_name="us-east-1",
                config=Config(signature_version="s3v4"),
            )
            client.create_bucket(Bucket=bucket)
            clients.append(client)
        yield (*connections, *clients, bucket)
    finally:
        for connection in connections:
            connection.close()
        for client in clients:
            for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
                for obj in page.get("Contents", []):
                    client.delete_object(Bucket=bucket, Key=obj["Key"])
            client.delete_bucket(Bucket=bucket)
        with psycopg.connect(dsn, autocommit=True) as admin:
            for name in names:
                admin.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
                )


def project(connection, name: str):
    connection.execute(
        "INSERT INTO registry.organization_projects VALUES (%s,%s,%s)", (name, name, name)
    )


def audit(connection, project_id: str):
    connection.execute(
        """INSERT INTO registry.audit_events
        (organization_id,project_id,actor_id,action,resource_type,resource_id,request_id,
         outcome,occurred_at) VALUES (%s,%s,%s,'created','project',%s,%s,'SUCCEEDED',now())""",
        (project_id, project_id, ADMIN, project_id, str(uuid4())),
    )
    connection.execute(
        """INSERT INTO core.audit_events
        (audit_id,organization_id,project_id,region_code,actor_id,action,resource_type,
         resource_id,request_id,occurred_at)
        VALUES (%s,%s,%s,'global',%s,'created','project',%s,%s,now())""",
        (uuid4(), project_id, project_id, ADMIN, project_id, str(uuid4())),
    )
    connection.execute(
        """INSERT INTO access_control.audit_events
        (event_id,scope_kind,actor_id,action,resource_type,resource_id,request_id,outcome)
        VALUES (%s,'PLATFORM',%s,'project.created','project',%s,%s,'SUCCEEDED')""",
        (uuid4(), ADMIN, project_id, str(uuid4())),
    )


def pack(stores, tmp_path: Path):
    source, _, s3, _, bucket = stores
    bundle = tmp_path / "bundle"
    export_bundle(source, s3, bucket, bundle, credential_key=KEY)
    return bundle, load_bundle(bundle, bucket=bucket, credential_key=KEY)


def test_merge_preserves_target_admin_projects_files_audits_and_is_repeatable(stores, tmp_path):
    source, target, s3a, s3b, bucket = stores
    project(source, "source-project")
    project(target, "existing-project")
    audit(source, "source-project")
    audit(target, "existing-project")
    target.execute(
        "UPDATE access_control.accounts SET password_hash='target-password', "
        "status='DISABLED' WHERE principal_id=%s",
        (ADMIN,),
    )
    target.execute(
        "UPDATE access_control.platform_capability_grants SET active=false, "
        "revoked_at=now(),revoked_by='test' WHERE principal_id=%s",
        (ADMIN,),
    )
    s3a.put_object(Bucket=bucket, Key="source/video.mp4", Body=b"source-video")
    s3a.put_object(Bucket=bucket, Key="shared", Body=b"identical")
    s3b.put_object(Bucket=bucket, Key="target/retained", Body=b"target-data")
    s3b.put_object(Bucket=bucket, Key="shared", Body=b"identical")
    bundle, manifest = pack(stores, tmp_path)
    expected_seq = target.execute(
        "SELECT last_value,is_called FROM registry.audit_events_event_id_seq"
    ).fetchone()
    plan = merge_database(target, bundle, manifest, dry_run=True)
    assert plan["project_count"] == 2 and plan["kept_target_accounts"] == 1
    assert target.execute("SELECT count(*) FROM registry.organization_projects").fetchone()[0] == 1
    assert (
        target.execute(
            "SELECT last_value,is_called FROM registry.audit_events_event_id_seq"
        ).fetchone()
        == expected_seq
    )
    assert check_objects(s3b, bucket, manifest) == {
        "existing_identical": 1,
        "to_copy": 1,
        "bytes_to_copy": len(b"source-video"),
    }
    report = merge_database(
        target,
        bundle,
        manifest,
        dry_run=False,
        before_commit=lambda: copy_objects(s3b, bucket, bundle, manifest),
    )
    assert report["project_count"] == 2
    assert target.execute(
        "SELECT password_hash,status FROM access_control.accounts WHERE principal_id=%s", (ADMIN,)
    ).fetchone() == ("target-password", "DISABLED")
    assert (
        target.execute(
            "SELECT count(*) FROM access_control.platform_capability_grants WHERE active"
        ).fetchone()[0]
        == 0
    )
    assert target.execute("SELECT count(*) FROM registry.audit_events").fetchone()[0] == 2
    assert (
        target.execute("SELECT count(*) FROM platform.platform_audit_integrity_entries").fetchone()[
            0
        ]
        == 2
    )
    assert s3b.get_object(Bucket=bucket, Key="target/retained")["Body"].read() == b"target-data"
    assert s3b.get_object(Bucket=bucket, Key="source/video.mp4")["Body"].read() == b"source-video"
    again = merge_database(target, bundle, manifest, dry_run=False)
    assert again["status"] == "already_imported"
    assert target.execute("SELECT count(*) FROM registry.audit_events").fetchone()[0] == 2


def test_same_login_different_identity_is_renamed_without_changing_actor_id(stores, tmp_path):
    source, target, _, _, _ = stores
    source_id, target_id = uuid4(), uuid4()
    for conn, principal in ((source, source_id), (target, target_id)):
        conn.execute(
            "INSERT INTO access_control.accounts "
            "(principal_id,canonical_username,display_username,display_name,"
            "password_hash,password_changed_at) "
            "VALUES (%s,'operator','operator','Operator','hash',now())",
            (principal,),
        )
    bundle, manifest = pack(stores, tmp_path)
    result = merge_database(target, bundle, manifest, dry_run=False)
    renamed = result["renamed_accounts"][0]
    assert renamed["principal_id"] == str(source_id)
    assert renamed["username"].startswith("operator_import_")
    assert (
        target.execute(
            "SELECT principal_id FROM access_control.accounts WHERE canonical_username='operator'"
        ).fetchone()[0]
        == target_id
    )
    # A fresh export has a new bundle ID but must recognize the previously renamed identity.
    next_bundle, next_manifest = pack(stores, tmp_path / "again")
    repeated = merge_database(target, next_bundle, next_manifest, dry_run=False)
    assert repeated["renamed_accounts"] == []
    assert target.execute(
        "SELECT canonical_username FROM access_control.accounts WHERE principal_id=%s",
        (source_id,),
    ).fetchone() == (renamed["username"],)
    assert target.execute("SELECT count(*) FROM access_control.accounts").fetchone()[0] == 3


def test_different_existing_row_rolls_back_all_inserts(stores, tmp_path):
    source, target, _, _, _ = stores
    project(source, "collision")
    project(source, "new-project")
    project(target, "collision")
    target.execute("UPDATE registry.organization_projects SET display_name='target-name'")
    for conn, content in ((source, "source"), (target, "target")):
        conn.execute("CREATE TABLE public.merge_test_items(id text PRIMARY KEY, content text)")
        conn.execute("INSERT INTO public.merge_test_items VALUES (%s,%s)", ("same", content))
    bundle, manifest = pack(stores, tmp_path)
    with pytest.raises(MergeError, match="MERGE_ROW_CONFLICT"):
        merge_database(target, bundle, manifest, dry_run=False)
    assert target.execute(
        "SELECT project_id,display_name FROM registry.organization_projects"
    ).fetchall() == [("collision", "target-name")]
    assert target.execute("SHOW session_replication_role").fetchone()[0] == "origin"


def test_object_content_conflict_does_not_overwrite_target(stores, tmp_path):
    _, target, s3a, s3b, bucket = stores
    s3a.put_object(Bucket=bucket, Key="same-key", Body=b"source")
    s3b.put_object(Bucket=bucket, Key="same-key", Body=b"target")
    bundle, manifest = pack(stores, tmp_path)
    with pytest.raises(MergeError, match="MERGE_OBJECT_CONTENT_CONFLICT"):
        check_objects(s3b, bucket, manifest)
    assert s3b.get_object(Bucket=bucket, Key="same-key")["Body"].read() == b"target"
    assert target.execute("SELECT count(*) FROM access_control.accounts").fetchone()[0] == 1


def test_invalid_foreign_key_rolls_back(stores, tmp_path):
    source, target, _, _, _ = stores
    source.execute("SET session_replication_role=replica")
    source.execute(
        "INSERT INTO access_control.organization_memberships "
        "(principal_id,organization_id,active,activated_by) VALUES (%s,'bad-org',true,'test')",
        (uuid4(),),
    )
    source.execute("SET session_replication_role=origin")
    bundle, manifest = pack(stores, tmp_path)
    with pytest.raises(MergeError, match="MERGE_FOREIGN_KEY_MISMATCH"):
        merge_database(target, bundle, manifest, dry_run=False)
    assert (
        target.execute("SELECT count(*) FROM access_control.organization_memberships").fetchone()[0]
        == 0
    )


def test_bundle_corruption_and_credential_key_mismatch_are_rejected(stores, tmp_path):
    _, _, _, _, bucket = stores
    bundle, manifest = pack(stores, tmp_path)
    with pytest.raises(MergeError, match="MERGE_DATA_SOURCE_CREDENTIAL_KEY_MISMATCH"):
        load_bundle(bundle, bucket=bucket, credential_key="wrong-key")
    path = bundle / manifest["tables"]["access_control.accounts"]["path"]
    path.write_bytes(path.read_bytes() + b"corruption")
    with pytest.raises(MergeError, match="MERGE_ARTIFACT_CHECKSUM_INVALID"):
        load_bundle(bundle, bucket=bucket, credential_key=KEY)


def test_failed_file_transfer_rolls_back_rows_and_sequences(stores, tmp_path):
    source, target, _, _, _ = stores
    project(source, "incoming")
    audit(source, "incoming")
    before = target.execute(
        "SELECT last_value,is_called FROM registry.audit_events_event_id_seq"
    ).fetchone()
    bundle, manifest = pack(stores, tmp_path)

    def failed_copy():
        raise OSError("simulated transfer disconnect")

    with pytest.raises(OSError, match="disconnect"):
        merge_database(target, bundle, manifest, dry_run=False, before_commit=failed_copy)
    assert target.execute("SELECT count(*) FROM registry.organization_projects").fetchone()[0] == 0
    assert (
        target.execute(
            "SELECT last_value,is_called FROM registry.audit_events_event_id_seq"
        ).fetchone()
        == before
    )


def test_shared_project_retains_target_name_and_extends_valid_audit_chains(stores, tmp_path):
    source, target, _, _, _ = stores
    project(source, "shared-project")
    project(target, "shared-project")
    audit(source, "shared-project")
    audit(target, "shared-project")
    target.execute("UPDATE registry.organization_projects SET display_name='Target name'")
    head = target.execute("SELECT last_event_hash FROM core.audit_integrity_heads").fetchone()[0]
    bundle, manifest = pack(stores, tmp_path)
    report = merge_database(target, bundle, manifest, dry_run=False)
    assert report["project_count"] == 1
    assert (
        target.execute("SELECT display_name FROM registry.organization_projects").fetchone()[0]
        == "Target name"
    )
    assert (
        target.execute(
            "SELECT previous_event_hash FROM core.audit_integrity_entries WHERE sequence_no=2"
        ).fetchone()[0]
        == head
    )


def test_conditional_put_race_never_replaces_existing_content(stores, tmp_path, monkeypatch):
    _, _, s3a, s3b, bucket = stores
    s3a.put_object(Bucket=bucket, Key="racing-key", Body=b"source")
    bundle, manifest = pack(stores, tmp_path)
    s3b.put_object(Bucket=bucket, Key="racing-key", Body=b"target")
    actual = merge_bundle._existing_object
    calls = 0

    def raced_check(*args):
        nonlocal calls
        calls += 1
        return False if calls == 1 else actual(*args)

    monkeypatch.setattr(merge_bundle, "_existing_object", raced_check)
    with pytest.raises(MergeError, match="MERGE_OBJECT_CONTENT_CONFLICT"):
        copy_objects(s3b, bucket, bundle, manifest)
    assert s3b.get_object(Bucket=bucket, Key="racing-key")["Body"].read() == b"target"


def test_corrupt_target_audit_chain_is_rejected_without_repair(stores, tmp_path):
    source, target, _, _, _ = stores
    project(source, "new-project")
    project(target, "existing-project")
    audit(target, "existing-project")
    target.execute("UPDATE core.audit_integrity_heads SET last_event_hash=repeat('0',64)")
    bundle, manifest = pack(stores, tmp_path)
    with pytest.raises(MergeError, match="MERGE_AUDIT_HEAD_INVALID"):
        merge_database(target, bundle, manifest, dry_run=False)
    assert target.execute("SELECT count(*) FROM registry.organization_projects").fetchone()[0] == 1
