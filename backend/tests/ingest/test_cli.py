from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel

from hc_data_platform.ingest.cli import (
    MAX_MULTIPART_PARTS,
    HttpResponse,
    UploadTransportError,
    effective_part_size,
    import_offline_bundle,
)
from hc_data_platform.ingest.models import (
    CompletedPart,
    FailedPartV1,
    ManifestFileV1,
    RolloutManifestV1,
)
from hc_data_platform.ingest.ports import InMemoryObjectStorage, crc64_ecma
from hc_data_platform.ingest.service import UploadSessionService


def manifest_for(body: bytes) -> RolloutManifestV1:
    start = datetime(2026, 8, 14, 8, tzinfo=timezone.utc)
    return RolloutManifestV1(
        project_id="p1",
        task_id="t1",
        collection_job_id="j1",
        rollout_id="r1",
        collection_session_id="session1",
        recording_request_id="request1",
        data_package_id="package1",
        sequence_no=1,
        robot_id="robot1",
        start_time=start,
        end_time=start + timedelta(seconds=1),
        expected_topics=[],
        actual_topics=[],
        cameras=[],
        topics=[],
        files=[
            ManifestFileV1(
                path="recording.mcap",
                size=len(body),
                sha256=hashlib.sha256(body).hexdigest(),
                crc64=crc64_ecma(body),
            )
        ],
        file_size=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
        crc64=crc64_ecma(body),
        compression="none",
        recorder_version="test",
    )


class ServiceBackedHttp:
    """Exercises the same service protocol while simulating direct signed-URL PUTs."""

    def __init__(self, service: UploadSessionService, storage: InMemoryObjectStorage) -> None:
        self.service = service
        self.storage = storage

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Any = None,
        body: bytes | None = None,
    ) -> HttpResponse:
        if url.startswith("memory://"):
            parsed = urlparse(url)
            upload_id, raw_part = parsed.path.strip("/").split("/")
            key = parse_qs(parsed.query)["key"][0]
            part = self.storage.upload_part(upload_id, int(raw_part), body or b"", key=key)
            return HttpResponse(200, {"ETag": part.etag}, b"")

        payload = None if body is None else json.loads(body)
        if method == "POST" and url.endswith("/upload-sessions"):
            assert isinstance(payload, dict)
            manifest = RolloutManifestV1.model_validate(payload["manifest"])
            result = self.service.create_upload(
                manifest=manifest,
                region_code="cn-hz",
                idempotency_key="offline-import",
                part_numbers=payload["part_numbers"],
            )
            return response_json(201, result)

        session_id = url.split("/upload-sessions/", maxsplit=1)[1].split(":", maxsplit=1)[0]
        session_id = session_id.split("/", maxsplit=1)[0]
        if method == "GET" and url.endswith("/parts"):
            return response_json(200, self.service.list_parts(session_id))
        if method == "POST" and url.endswith(":renew"):
            assert isinstance(payload, dict)
            return response_json(
                200,
                self.service.renew_part_authorizations(session_id, payload["part_numbers"]),
            )
        if method == "POST" and url.endswith(":retry-parts"):
            assert isinstance(payload, dict)
            return response_json(
                200,
                self.service.retry_failed_parts(
                    session_id,
                    [FailedPartV1.model_validate(item) for item in payload["failures"]],
                ),
            )
        if method == "POST" and url.endswith(":complete"):
            assert isinstance(payload, dict)
            parts = [CompletedPart.model_validate(value) for value in payload["parts"]]
            return response_json(200, self.service.complete_upload(session_id, parts))
        if method == "POST" and url.endswith(":commit-manifest"):
            assert isinstance(payload, dict)
            manifest = RolloutManifestV1.model_validate(payload)
            return response_json(
                200,
                self.service.commit_manifest(session_id=session_id, manifest=manifest),
            )
        raise AssertionError(f"unexpected protocol request: {method} {url}")


def response_json(status: int, value: Any) -> HttpResponse:
    def serialize(item: Any) -> Any:
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        if isinstance(item, list):
            return [serialize(child) for child in item]
        return item

    return HttpResponse(
        status=status,
        headers={"Content-Type": "application/json"},
        body=json.dumps(serialize(value), separators=(",", ":")).encode(),
    )


def test_offline_import_uses_robot_protocol_and_produces_identical_key_and_manifest(
    tmp_path: Path,
) -> None:
    body = b"offline-mcap-placeholder"
    manifest = manifest_for(body)
    mcap_path = tmp_path / "recording.mcap"
    manifest_path = tmp_path / "rollout_manifest.json"
    mcap_path.write_bytes(body)
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")

    offline_storage = InMemoryObjectStorage()
    offline_service = UploadSessionService(offline_storage)
    result = import_offline_bundle(
        mcap_path,
        manifest_path,
        api_base_url="https://api.invalid",
        region_code="cn-hz",
        access_token="not-logged",
        idempotency_key="offline-import",
        part_size=5 * 1024 * 1024,
        http=ServiceBackedHttp(offline_service, offline_storage),
    )

    robot_storage = InMemoryObjectStorage()
    robot_service = UploadSessionService(robot_storage)
    robot_session = robot_service.create_session(
        manifest=manifest,
        region_code="cn-hz",
        idempotency_key="robot-upload",
    )
    uploaded = robot_storage.upload_part(
        robot_session.multipart_upload_id,
        1,
        body,
        key=robot_session.object_key,
    )
    robot_service.complete_upload(
        robot_session.session_id,
        [CompletedPart(part_number=1, etag=uploaded.etag)],
    )
    robot_event = robot_service.commit_manifest(
        session_id=robot_session.session_id,
        manifest=manifest,
    )

    assert result["object_key"] == robot_event.object_key
    assert result["manifest_key"] == robot_event.manifest_key
    assert (
        offline_storage.objects[result["manifest_key"]]
        == robot_storage.objects[robot_event.manifest_key]
    )


def test_offline_manifest_integrity_is_checked_before_any_http_call(tmp_path: Path) -> None:
    body = b"actual"
    manifest = manifest_for(b"different")
    mcap_path = tmp_path / "recording.mcap"
    manifest_path = tmp_path / "manifest.json"
    mcap_path.write_bytes(body)
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    called = False

    class MustNotCall:
        def request(self, *_args: Any, **_kwargs: Any) -> HttpResponse:
            nonlocal called
            called = True
            raise AssertionError("HTTP must not be called for an invalid local bundle")

    try:
        import_offline_bundle(
            mcap_path,
            manifest_path,
            api_base_url="https://api.invalid",
            region_code="cn-hz",
            access_token="token",
            idempotency_key="invalid",
            http=MustNotCall(),
        )
    except ValueError as exc:
        assert "do not match" in str(exc)
    else:
        raise AssertionError("invalid offline bundle was accepted")
    assert not called


def test_effective_part_size_keeps_a_five_tib_import_within_s3_part_limit() -> None:
    five_tib = 5 * 1024**4

    part_size = effective_part_size(file_size=five_tib, requested_part_size=64 * 1024**2)

    assert part_size == math.ceil(five_tib / MAX_MULTIPART_PARTS)
    assert math.ceil(five_tib / part_size) == MAX_MULTIPART_PARTS


def test_offline_import_reuses_a_part_after_the_put_response_is_lost(tmp_path: Path) -> None:
    body = b"lost-put-response"
    manifest = manifest_for(body)
    mcap_path = tmp_path / "recording.mcap"
    manifest_path = tmp_path / "rollout_manifest.json"
    mcap_path.write_bytes(body)
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")

    class LostResponseHttp(ServiceBackedHttp):
        direct_puts = 0

        def request(self, method: str, url: str, **kwargs: Any) -> HttpResponse:
            if url.startswith("memory://"):
                self.direct_puts += 1
                response = super().request(method, url, **kwargs)
                if self.direct_puts == 1:
                    raise UploadTransportError(
                        "the object store accepted bytes but the connection reset"
                    )
                return response
            return super().request(method, url, **kwargs)

    storage = InMemoryObjectStorage()
    http = LostResponseHttp(UploadSessionService(storage), storage)

    result = import_offline_bundle(
        mcap_path,
        manifest_path,
        api_base_url="https://api.invalid",
        region_code="cn-hz",
        access_token="token",
        idempotency_key="lost-response",
        part_size=5 * 1024 * 1024,
        retry_base_seconds=0.01,
        sleep=lambda _: None,
        http=http,
    )

    assert http.direct_puts == 1
    assert result["object_key"] in storage.objects


def test_offline_import_records_a_failed_part_and_retries_after_disconnect(tmp_path: Path) -> None:
    body = b"retry-after-disconnect"
    manifest = manifest_for(body)
    mcap_path = tmp_path / "recording.mcap"
    manifest_path = tmp_path / "rollout_manifest.json"
    mcap_path.write_bytes(body)
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")

    class DisconnectBeforePutHttp(ServiceBackedHttp):
        direct_puts = 0

        def request(self, method: str, url: str, **kwargs: Any) -> HttpResponse:
            if url.startswith("memory://"):
                self.direct_puts += 1
                if self.direct_puts == 1:
                    raise UploadTransportError(
                        "connection reset before object store received the bytes"
                    )
            return super().request(method, url, **kwargs)

    storage = InMemoryObjectStorage()
    service = UploadSessionService(storage)
    http = DisconnectBeforePutHttp(service, storage)

    result = import_offline_bundle(
        mcap_path,
        manifest_path,
        api_base_url="https://api.invalid",
        region_code="cn-hz",
        access_token="token",
        idempotency_key="disconnect-before-put",
        part_size=5 * 1024 * 1024,
        retry_base_seconds=0.01,
        sleep=lambda _: None,
        http=http,
    )

    assert http.direct_puts == 2
    assert result["object_key"] in storage.objects
    session = service.persistence.find_session_by_package("p1", "package1")
    assert session is not None
    parts = service.list_parts(session.session_id)
    assert parts[0].retry_count == 1
    assert parts[0].failure_code is None


def test_offline_import_restart_skips_parts_the_server_already_has(tmp_path: Path) -> None:
    body = b"previous-process-uploaded-this-part"
    manifest = manifest_for(body)
    mcap_path = tmp_path / "recording.mcap"
    manifest_path = tmp_path / "rollout_manifest.json"
    mcap_path.write_bytes(body)
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    storage = InMemoryObjectStorage()
    service = UploadSessionService(storage)
    session = service.create_session(
        manifest=manifest,
        region_code="cn-hz",
        idempotency_key="offline-import",
    )
    storage.upload_part(
        session.multipart_upload_id or "",
        1,
        body,
        key=session.object_key,
    )

    class CountingHttp(ServiceBackedHttp):
        direct_puts = 0

        def request(self, method: str, url: str, **kwargs: Any) -> HttpResponse:
            if url.startswith("memory://"):
                self.direct_puts += 1
            return super().request(method, url, **kwargs)

    http = CountingHttp(service, storage)
    result = import_offline_bundle(
        mcap_path,
        manifest_path,
        api_base_url="https://api.invalid",
        region_code="cn-hz",
        access_token="token",
        idempotency_key="offline-import",
        part_size=5 * 1024 * 1024,
        sleep=lambda _: None,
        http=http,
    )

    assert http.direct_puts == 0
    assert result["object_key"] == session.object_key
