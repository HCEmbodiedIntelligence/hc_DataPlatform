from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from urllib.request import urlopen
from uuid import uuid4

import pytest

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.publishing.memory import InMemoryExportSource
from hc_data_platform.publishing.models import (
    ExportFormat,
    ExportStepV1,
    PublishedDatasetManifestV1,
    PublishedRolloutV1,
    StepRangeV1,
)
from hc_data_platform.publishing.s3 import S3ArtifactSink
from hc_data_platform.publishing.service import ExportCoordinator


@pytest.mark.integration
def test_minio_export_promotion_fresh_download_and_integrity_recheck() -> None:
    endpoint = os.environ.get("HC_MINIO_ENDPOINT")
    bucket = os.environ.get("HC_MINIO_BUCKET")
    access_key = os.environ.get("HC_MINIO_ACCESS_KEY")
    secret_key = os.environ.get("HC_MINIO_SECRET_KEY")
    if not all((endpoint, bucket, access_key, secret_key)):
        pytest.skip("HC_MINIO_ENDPOINT/BUCKET/ACCESS_KEY/SECRET_KEY are required")
    assert endpoint is not None
    assert bucket is not None
    assert access_key is not None
    assert secret_key is not None
    boto3 = pytest.importorskip("boto3")
    botocore_config = pytest.importorskip("botocore.config")
    lance = pytest.importorskip("lance")
    del lance

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
        config=botocore_config.Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    try:
        client.head_bucket(Bucket=bucket)
    except Exception:
        client.create_bucket(Bucket=bucket)

    suffix = uuid4().hex
    project_id = f"export-{suffix}"
    prefix = f"integration/export/{suffix}"
    manifest = PublishedDatasetManifestV1(
        project_id=project_id,
        dataset_id="dataset-a",
        dataset_version="v1",
        base_lance_version="lance-v1",
        created_at=datetime.now(timezone.utc),
        content_hash="a" * 64,
        annotations_uri="published/annotations.lance",
        annotations_content_sha256="b" * 64,
        training_manifest_uri="published/training-manifest.json",
        training_manifest_content_sha256="c" * 64,
        rollouts=(
            PublishedRolloutV1(
                rollout_id="rollout-a",
                source_mcap_sha256="d" * 64,
                base_lance_version="lance-v1",
                annotation_revision=1,
                quality_profile_version="quality-v1",
                alignment_profile_version="alignment-v1",
                alignment_frequency_hz=30,
                converter_version="converter-v1",
                total_steps=1,
                included_step_ranges=(StepRangeV1(start_step=0, end_step=1),),
            ),
        ),
    )
    step = ExportStepV1(
        rollout_id="rollout-a",
        step_index=0,
        timestamp_ns=0,
        modalities={"camera.front": "frame-0", "action": [0.0]},
        source_timestamps_ns={"camera.front": (0,), "action": (0,)},
        time_error_ns={"camera.front": 0, "action": 0},
        valid={"camera.front": True, "action": True},
        repeated={"camera.front": False, "action": False},
    )
    from hc_data_platform.publishing.exporters import LanceSnapshotExporter

    sink = S3ArtifactSink(client, bucket, prefix=prefix)
    coordinator = ExportCoordinator(
        source=InMemoryExportSource([step]),
        sink=sink,
        exporters=[LanceSnapshotExporter()],
    )
    assert coordinator.preflight(manifest, format=ExportFormat.LANCE_SNAPSHOT) == 1
    result = coordinator.export(
        manifest,
        format=ExportFormat.LANCE_SNAPSHOT,
        attempt_id=f"attempt-{suffix}",
    )
    try:
        artifact = sink.get_published(result.artifact_uri)
        assert artifact is not None
        assert hashlib.sha256(artifact).hexdigest() == result.artifact_content_hash
        coordinator.verify_artifact(result)
        fresh_download = coordinator.authorize_download(result)
        assert fresh_download != ""
        with urlopen(fresh_download) as response:  # noqa: S310 - platform generated the presign
            downloaded = response.read()
        assert downloaded == artifact

        client.put_object(
            Bucket=bucket,
            Key=f"{prefix}/{result.artifact_uri}",
            Body=b"tampered-object",
        )
        with pytest.raises(ProblemException) as integrity:
            coordinator.authorize_download(result)
        assert integrity.value.problem.code == "EXPORT_ARTIFACT_HASH_MISMATCH"
    finally:
        listed = client.list_objects_v2(Bucket=bucket, Prefix=f"{prefix}/")
        for item in listed.get("Contents", []):
            client.delete_object(Bucket=bucket, Key=item["Key"])
