"""Real isolated PostgreSQL/MinIO fault tests; synthetic data is not G1 E2E proof."""

import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import uuid4

import boto3
import numpy as np
import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.episode_store.commit import EpisodePackageCommitter
from hc_data_platform.episode_store.models import EpisodeIdentity, PackageInput, canonical
from hc_data_platform.episode_store.postgres import PackageConflict, PostgresEpisodeStore
from hc_data_platform.episode_store.validation import validate_package
from hc_data_platform.ingest.adapters import S3ObjectStorage
from hc_data_platform.publishing.depth_evidence import DepthEvidenceVideo
from hc_data_platform.publishing.holobrain_depth import DEPTH_INFO
from hc_data_platform.publishing.lerobot_export import DATA_PATH, EPISODES_PATH, VIDEO_PATH


@contextmanager
def scoped(source):
    identity = source.identity
    token = bind_request_context(
        RequestContext(
            organization_id=identity.organization_id,
            project_id=identity.project_id,
            region_code=identity.region_code,
            subject_id="g1-service-test",
            service_identity=True,
        )
    )
    try:
        yield
    finally:
        reset_request_context(token)


@pytest.fixture
def services():
    dsn = os.environ.get("HC_G1_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("explicit HC_G1_TEST_POSTGRES_DSN required; no production fallback")
    client = boto3.client(
        "s3",
        endpoint_url="http://minio:9000",
        aws_access_key_id="minio",
        aws_secret_access_key="minio-local-only",
        region_name="us-east-1",
    )
    bucket = "hc-data-local"
    return PostgresEpisodeStore(psycopg_connection_factory(dsn)), S3ObjectStorage(client, bucket)


@pytest.fixture
def package(tmp_path):
    source = PackageInput(
        identity=EpisodeIdentity(
            organization_id="g1-" + uuid4().hex,
            project_id="p",
            region_code="r",
            dataset_id="d",
            episode_id="episode-1",
            rollout_id="explicit:rollout:identity",
        ),
        source_sha256="a" * 64,
        processor_version="fault-fixture/1",
        configuration_sha256="b" * 64,
        schema_snapshot_id="schema-1",
        required_features=("observation.state",),
    )
    root = tmp_path / "package"
    for directory in ("meta/episodes/chunk-000", "data/chunk-000", "hc"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    table = pa.table(
        {
            "index": [0, 1, 2],
            "frame_index": [0, 1, 2],
            "episode_index": [0, 0, 0],
            "task_index": [0, 0, 0],
            "timestamp": pa.array([0, 1 / 30, 2 / 30], type=pa.float32()),
            "observation.state": pa.array([[1.0], [2.0], [3.0]], type=pa.list_(pa.float64(), 1)),
        }
    )
    pq.write_table(table, root / DATA_PATH)
    pq.write_table(
        pa.table({"task_index": [0], "task": ["fault probe"]}), root / "meta/tasks.parquet"
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "episode_index": 0,
                    "length": 3,
                    "dataset_from_index": 0,
                    "dataset_to_index": 3,
                    "data/chunk_index": 0,
                    "data/file_index": 0,
                }
            ]
        ),
        root / EPISODES_PATH,
    )
    info = {
        "codebase_version": "v3.0",
        "fps": 30,
        "total_episodes": 1,
        "total_frames": 3,
        "total_tasks": 1,
        "features": {
            k: {
                "dtype": "float32"
                if k == "timestamp"
                else "float64"
                if k == "observation.state"
                else "int64",
                "shape": [1],
            }
            for k in table.column_names
        },
    }
    for path, value in {
        "meta/info.json": info,
        "meta/stats.json": {},
        "hc/modalities.json": {"depth": {}},
        "hc/source.json": {
            "identity": source.identity.model_dump(mode="json"),
            "source_sha256": source.source_sha256,
        },
        "hc/calibration.json": {"status": "not_collected"},
        "hc/conversion_report.json": {"scope": "synthetic-fault-test"},
        "hc/quality.json": {"status": "UNKNOWN"},
    }.items():
        (root / path).write_bytes(canonical(value))
    return root, source


@pytest.mark.integration
@pytest.mark.parametrize("fault", ["file_verified", "objects_verified", "database_committed"])
def test_interruption_recovery_and_lost_response(services, package, fault):
    repository, storage = services
    root, source = package

    def interrupt(stage):
        if stage == fault:
            raise InterruptedError("simulated interruption")

    with scoped(source):
        with pytest.raises(InterruptedError):
            EpisodePackageCommitter(repository, storage, checkpoint=interrupt).commit(root, source)
        visible = repository.current(source.identity)
        assert (visible is not None) == (fault == "database_committed")
        receipt = EpisodePackageCommitter(repository, storage).commit(root, source)
        assert repository.current(source.identity) == receipt
        assert EpisodePackageCommitter(repository, storage).commit(root, source) == receipt
        assert "lance_version" not in receipt.model_dump_json()
        with repository.transaction() as cursor:
            cursor.execute(
                "SELECT count(*) FROM episode_store.events WHERE organization_id=%s",
                (source.identity.organization_id,),
            )
            assert cursor.fetchone()[0] == 1


@pytest.mark.integration
def test_concurrent_same_package_is_one_version_and_event(services, package):
    repository, storage = services
    root, source = package

    def commit():
        with scoped(source):
            return EpisodePackageCommitter(repository, storage).commit(root, source)

    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda _: commit(), range(4)))
    assert all(r == receipts[0] for r in receipts)
    with scoped(source), repository.transaction() as cursor:
        cursor.execute(
            "SELECT count(*) FROM episode_store.versions WHERE organization_id=%s",
            (source.identity.organization_id,),
        )
        assert cursor.fetchone()[0] == 1


@pytest.mark.integration
def test_corruption_or_missing_file_cannot_be_reported_ready(services, package):
    repository, storage = services
    root, source = package
    # Structurally absent modality: rejected before any visible/reserved version.
    broken = source.model_copy(update={"required_features": ("observation.images.missing",)})
    with scoped(source):
        with pytest.raises(ValueError, match="modality missing"):
            EpisodePackageCommitter(repository, storage).commit(root, broken)
        assert repository.current(source.identity) is None
        # Pre-existing bytes at the immutable destination are not silently overwritten.
        manifest = validate_package(root, source)
        entry = manifest.files[0]
        key = f"{source.package_prefix}/{entry.path}"
        upload = storage.create_multipart(key)
        part = storage.upload_part_stream(key, upload, 1, io.BytesIO(b"bad"), 3)
        from hc_data_platform.ingest.models import CompletedPart

        storage.complete_multipart(key, upload, [CompletedPart(part_number=1, etag=part.etag)])
        with pytest.raises(PackageConflict, match="wrong size"):
            EpisodePackageCommitter(repository, storage).commit(root, source)
        assert repository.current(source.identity) is None
        assert b"".join(storage.read_chunks(key)) == b"bad"


@pytest.mark.integration
def test_different_content_same_processing_key_conflicts(services, package):
    repository, storage = services
    root, source = package
    with scoped(source):
        first = EpisodePackageCommitter(repository, storage).commit(root, source)
        (root / "hc/quality.json").write_bytes(canonical({"status": "PASS"}))
        with pytest.raises(PackageConflict, match="different content"):
            EpisodePackageCommitter(repository, storage).commit(root, source)
        assert repository.current(source.identity) == first
        # A changed configuration gets a real new data identity/version.
        second_source = source.model_copy(update={"configuration_sha256": "c" * 64})
        second = EpisodePackageCommitter(repository, storage).commit(root, second_source)
        assert second.data.version == 2
        assert first.data.data_version_id != second.data.data_version_id
        assert repository.current(source.identity) == second


@pytest.mark.integration
def test_tenant_guard_and_database_rls_fail_closed(services, package):
    repository, storage = services
    root, source = package
    with scoped(source):
        receipt = EpisodePackageCommitter(repository, storage).commit(root, source)
    foreign = source.model_copy(
        update={
            "identity": source.identity.model_copy(update={"organization_id": "other-organization"})
        }
    )
    with scoped(foreign), pytest.raises(PermissionError):
        repository.current(source.identity)
    # Exercise PostgreSQL RLS using a non-superuser, independent of the Python guard.
    dsn = os.environ["HC_G1_TEST_POSTGRES_DSN"]
    role = "g1_rls_" + uuid4().hex
    with psycopg.connect(dsn) as connection:
        from psycopg import sql

        connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
        connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA episode_store,core TO {}").format(sql.Identifier(role))
        )
        connection.execute(
            sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA episode_store TO {}").format(
                sql.Identifier(role)
            )
        )
        connection.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
        for name, value in zip(
            ("organization_id", "project_id", "region_code"), source.identity.scope, strict=True
        ):
            connection.execute("SELECT set_config(%s,%s,true)", ("app." + name, value))
        query = "SELECT receipt_document FROM episode_store.versions WHERE data_version_id=%s"
        assert connection.execute(query, (source.data_version_id,)).fetchone()[
            0
        ] == receipt.model_dump(mode="json")
        for name in ("organization_id", "region_code", "project_id"):
            connection.execute("SELECT set_config(%s,%s,true)", ("app." + name, "other"))
            assert connection.execute(query, (source.data_version_id,)).fetchone() is None
            connection.execute(
                "SELECT set_config(%s,%s,true)", ("app." + name, getattr(source.identity, name))
            )
        connection.rollback()  # including the test role; no persistent privileges


def test_depth_video_profile_mask_and_source_are_required_together(package):
    root, source = package
    key = "observation.images.head_depth"
    source = source.model_copy(
        update={
            "required_features": (*source.required_features, key),
            "required_depth_features": (key,),
        }
    )
    path = root / VIDEO_PATH.format(video_key=key, chunk_index=0, file_index=0)
    evidence = root / "hc/depth/head"
    writer = DepthEvidenceVideo(path, evidence, 30, (16, 16))
    for index in range(3):
        writer.write(
            np.full((16, 16), 700, dtype=np.uint16),
            0.001,
            source_id="head",
            source_seq=index,
            source_timestamp_ns=index * 33_333_333,
            target_timestamp_ns=index * 33_333_333,
        )
    writer.close()
    info = json.loads((root / "meta/info.json").read_bytes())
    info["features"][key] = {
        "dtype": "video",
        "shape": [16, 16, 1],
        "info": {**DEPTH_INFO, "video.fps": 30},
    }
    (root / "meta/info.json").write_bytes(canonical(info))
    (root / "hc/modalities.json").write_bytes(
        canonical({"depth": {key: {"evidence_path": "hc/depth/head"}}})
    )
    episode = pq.read_table(root / EPISODES_PATH).to_pylist()[0]
    for suffix, value in {
        "chunk_index": 0,
        "file_index": 0,
        "from_timestamp": 0.0,
        "to_timestamp": 0.1,
    }.items():
        episode[f"videos/{key}/{suffix}"] = value
    pq.write_table(pa.Table.from_pylist([episode]), root / EPISODES_PATH)
    manifest = validate_package(root, source)
    assert path.relative_to(root).as_posix() in {entry.path for entry in manifest.files}
    rows = pq.read_table(evidence / "frames.parquet").to_pylist()
    rows[1]["mask_sha256"] = "f" * 64
    pq.write_table(pa.Table.from_pylist(rows), evidence / "frames.parquet")
    with pytest.raises(ValueError, match="frame mapping mismatch"):
        validate_package(root, source)


@pytest.mark.integration
def test_database_failure_rolls_back_pointer_and_event_then_recovers(services, package):
    from psycopg import sql

    repository, storage = services
    root, source = package
    dsn = os.environ["HC_G1_TEST_POSTGRES_DSN"]
    trigger = "g1_fault_" + uuid4().hex
    with psycopg.connect(dsn) as connection:
        connection.execute(
            sql.SQL("""CREATE FUNCTION episode_store.{}() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'G1 injected database failure'; END $$""").format(
                sql.Identifier(trigger)
            )
        )
        connection.execute(
            sql.SQL("""CREATE TRIGGER {} BEFORE INSERT ON episode_store.events
            FOR EACH ROW WHEN (NEW.organization_id={})
            EXECUTE FUNCTION episode_store.{}()""").format(
                sql.Identifier(trigger),
                sql.Literal(source.identity.organization_id),
                sql.Identifier(trigger),
            )
        )
    try:
        with scoped(source):
            with pytest.raises(psycopg.Error, match="injected database failure"):
                EpisodePackageCommitter(repository, storage).commit(root, source)
            assert repository.current(source.identity) is None
            assert storage.head(f"{source.package_prefix}/hc/commit.json") is not None
            # Even direct SQL cannot make the reserved package visible.
            with (
                pytest.raises(psycopg.Error, match="committed package"),
                repository.transaction() as c,
            ):
                c.execute(
                    """UPDATE episode_store.bindings SET current_data_version_id=%s
                    WHERE organization_id=%s""",
                    (source.data_version_id, source.identity.organization_id),
                )
    finally:
        with psycopg.connect(dsn) as connection:
            connection.execute(
                sql.SQL("DROP TRIGGER {} ON episode_store.events").format(sql.Identifier(trigger))
            )
            connection.execute(
                sql.SQL("DROP FUNCTION episode_store.{}()").format(sql.Identifier(trigger))
            )
    with scoped(source):
        receipt = EpisodePackageCommitter(repository, storage).commit(root, source)
        assert repository.current(source.identity) == receipt
        with pytest.raises(psycopg.Error, match="immutable"), repository.transaction() as c:
            c.execute(
                """UPDATE episode_store.versions SET content_hash=%s
                WHERE organization_id=%s""",
                ("0" * 64, source.identity.organization_id),
            )
