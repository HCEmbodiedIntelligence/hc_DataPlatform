from __future__ import annotations

import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.lance_catalog.models import StepRecord
from hc_data_platform.lance_catalog.ports import FakeStepReader
from hc_data_platform.preview.adapters import (
    FFmpegHlsEncoder,
    FilePreviewMediaReader,
    LanceStepReaderAdapter,
    S3ImageRefResolver,
)
from hc_data_platform.preview.memory import (
    HmacUrlSigner,
    InMemoryExclusionReader,
    InMemoryPreviewCache,
)
from hc_data_platform.preview.models import EncodingProfileV1, PreviewRequestV1, ViewMode
from hc_data_platform.preview.router import router
from hc_data_platform.preview.service import PreviewService

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

    object_key = f"integration/previews/{uuid4().hex}/camera-front.ppm"
    image = b"P6\n32 24\n255\n" + bytes((32, 96, 224)) * (32 * 24)
    client.put_object(
        Bucket=bucket, Key=object_key, Body=image, ContentType="image/x-portable-pixmap"
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
            segment = re.search(
                r"(?P<url>/api/v1/previews/.+/segment_[0-9]{5}\.m4s\?[^\n]+)",
                playlist.text,
            )
            assert segment is not None
            streamed = gateway.get(segment.group("url"), headers={"Range": "bytes=0-31"})

        assert streamed.status_code == 206
        assert streamed.headers["content-type"].startswith("video/iso.segment")
        assert re.fullmatch(r"bytes 0-31/[1-9][0-9]*", streamed.headers["content-range"])
        assert len(streamed.content) == 32
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
    service = PreviewService(
        step_reader=LanceStepReaderAdapter(
            FakeStepReader("dataset-1", 1, records, project_id="project-1"),
            image_ref_resolver=S3ImageRefResolver(s3_client, bucket),  # type: ignore[arg-type]
        ),
        exclusions=InMemoryExclusionReader(),
        encoder=FFmpegHlsEncoder(media_root),
        cache=InMemoryPreviewCache(),
        signer=HmacUrlSigner(b"preview-minio-integration-test"),
        media_reader=FilePreviewMediaReader(media_root),
        clock=lambda: _NOW,
    )
    descriptor = service.create(
        PreviewRequestV1(
            project_id="project-1",
            dataset_id="dataset-1",
            rollout_id="rollout-1",
            lance_version="v1",
            annotation_revision=1,
            camera_id="front",
            view_mode=ViewMode.ORIGINAL,
            frequency_hz=4,
            encoding_profile=EncodingProfileV1(
                width=64,
                height=48,
                video_bitrate_kbps=128,
                segment_duration_seconds=0.5,
                preset="ultrafast",
            ),
        )
    )
    monkeypatch.setattr(preview_router, "_service", service)
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
