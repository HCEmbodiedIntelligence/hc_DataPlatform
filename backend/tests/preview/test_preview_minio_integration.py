from __future__ import annotations

import asyncio
import os
import re
import shutil
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from PIL import Image

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.lance_catalog.models import StepRecord
from hc_data_platform.lance_catalog.ports import FakeStepReader
from hc_data_platform.preview.adapters import (
    FFmpegHlsEncoder,
    LanceStepReaderAdapter,
    S3ImageRefResolver,
)
from hc_data_platform.preview.artifact_store import S3PreviewArtifactStore
from hc_data_platform.preview.memory import (
    HmacUrlSigner,
    InMemoryExclusionReader,
    InMemoryPreviewRepository,
)
from hc_data_platform.preview.models import (
    PreviewDescriptorV1,
    PreviewPendingDescriptorV1,
    PreviewRequestV1,
    PreviewScopeV1,
)
from hc_data_platform.preview.router import router
from hc_data_platform.preview.service import (
    PreviewControlPlaneService,
    PreviewGenerationService,
)

_NOW = datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)


def _minio_settings() -> tuple[str, str, str, str] | None:
    """Accept the standalone integration aliases and normal runtime settings."""

    endpoint = os.getenv("HC_MINIO_ENDPOINT") or os.getenv("HC_OBJECT_STORE_ENDPOINT")
    bucket = os.getenv("HC_MINIO_BUCKET") or os.getenv("HC_OBJECT_STORE_BUCKET")
    access_key = os.getenv("HC_MINIO_ACCESS_KEY") or os.getenv("HC_OBJECT_STORE_ACCESS_KEY")
    secret_key = os.getenv("HC_MINIO_SECRET_KEY") or os.getenv("HC_OBJECT_STORE_SECRET_KEY")
    if not all((endpoint, bucket, access_key, secret_key)):
        return None
    return endpoint, bucket, access_key, secret_key


@pytest.mark.integration
@pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="FFmpeg runtime dependency is unavailable",
)
def test_minio_s3_frame_is_transcoded_and_streamed_only_through_signed_gateway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _minio_settings()
    if settings is None:
        pytest.skip("object-store endpoint, bucket, and credentials are required")
    endpoint, bucket, access_key, secret_key = settings
    boto3 = pytest.importorskip("boto3")
    botocore_config = pytest.importorskip("botocore.config")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
        config=botocore_config.Config(signature_version="s3v4"),
    )
    try:
        client.head_bucket(Bucket=bucket)
    except Exception:
        client.create_bucket(Bucket=bucket)

    object_key = f"integration/previews/{uuid4().hex}/camera-front.jpg"
    buffer = BytesIO()
    Image.new("RGB", (32, 24), (32, 96, 224)).save(buffer, "JPEG")
    image = buffer.getvalue()
    client.put_object(
        Bucket=bucket, Key=object_key, Body=image, ContentType="image/jpeg"
    )
    try:
        descriptor, gateway = _create_signed_gateway(
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
            source_uri=f"s3://{bucket}/{object_key}",
            s3_client=client,
            bucket=bucket,
        )
        with gateway:
            playlist = gateway.get(descriptor.playlist_url)
            assert playlist.status_code == 200
            assert f"s3://{bucket}" not in playlist.text
            assert object_key not in playlist.text
            segment = re.search(r"(?P<url>https?://[^\n]+segment_[^\n]+)", playlist.text)
            assert segment is not None
            assert object_key not in segment.group("url")
    finally:
        client.delete_object(Bucket=bucket, Key=object_key)


def _create_signed_gateway(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_uri: str,
    s3_client: object,
    bucket: str,
) -> tuple[object, TestClient]:
    import hc_data_platform.preview.router as preview_router

    records = [
        StepRecord(
            rollout_id="rollout-1",
            step_index=index,
            timestamp_ns=index * 250_000_000,
            modalities={"camera.front": source_uri},
        )
        for index in range(4)
    ]
    media_root = tmp_path / "media"
    repository = InMemoryPreviewRepository()
    store = S3PreviewArtifactStore(s3_client, bucket)  # type: ignore[arg-type]
    control = PreviewControlPlaneService(
        repository=repository,
        store=store,
        signer=HmacUrlSigner(b"preview-minio-integration-test"),
        clock=lambda: _NOW,
    )
    generation = PreviewGenerationService(
        step_reader=LanceStepReaderAdapter(
            FakeStepReader("dataset-1", 1, records, project_id="project-1"),
            image_ref_resolver=S3ImageRefResolver(s3_client, bucket),  # type: ignore[arg-type]
        ),
        exclusions=InMemoryExclusionReader(),
        encoder=FFmpegHlsEncoder(media_root),
        repository=repository,
        store=store,
        clock=lambda: _NOW,
    )
    request = PreviewRequestV1(
        project_id="project-1",
        dataset_id="dataset-1",
        rollout_id="rollout-1",
        lance_version="v1",
        camera_id="front",
        frequency_hz=4,
    )
    scope = PreviewScopeV1(
        organization_id="organization-1",
        project_id="project-1",
        region_code="cn-test",
    )
    pending = asyncio.run(control.create_session(scope, request))
    assert isinstance(pending, PreviewPendingDescriptorV1)
    generation.generate(scope, request, job_id=pending.job_id)
    # Reconstruct the API control plane after the media worker has removed its
    # local staging. Only the shared repository and MinIO store cross the boundary.
    control = PreviewControlPlaneService(
        repository=repository,
        store=store,
        signer=HmacUrlSigner(b"preview-minio-integration-test"),
        clock=lambda: _NOW,
    )
    descriptor = asyncio.run(control.create_session(scope, request))
    assert isinstance(descriptor, PreviewDescriptorV1)
    monkeypatch.setattr(preview_router, "_service", control)
    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(ProblemException)
    async def problem_handler(_: Request, exc: ProblemException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.problem.status,
            content=exc.problem.model_dump(mode="json", exclude_none=True),
            media_type="application/problem+json",
        )

    return descriptor, TestClient(app)
