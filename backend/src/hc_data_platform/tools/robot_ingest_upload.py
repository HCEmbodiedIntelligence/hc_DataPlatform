"""Format-neutral robot-authenticated resumable Raw upload client."""

from __future__ import annotations

import hashlib
import json
import math
import mimetypes
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote
from uuid import uuid4

from hc_data_platform.ingest.cli import (
    DEFAULT_MAX_PART_RETRIES,
    DEFAULT_RETRY_BASE_SECONDS,
    HttpResponse,
    UploadHttpPort,
    UploadTransportError,
    UrllibUploadHttpClient,
    _json_request,
    _raise_for_status,
)
from hc_data_platform.ingest.ports import crc64_ecma

DEFAULT_PART_SIZE = 32 * 1024**2
MAX_MULTIPART_PARTS = 10_000
STATE_SCHEMA = "robot-ingest-client-state/v1"
DEFAULT_STATE_NAME = ".hc-robot-ingest-state.json"
_RECOVERABLE_PART_STATUSES = frozenset({401, 403, 408, 429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class RobotUploadAssetInput:
    path: str
    local_path: Path
    role: str = "RAW"
    media_type: str | None = None
    camera_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    sha256: str | None = None
    crc64: int | None = None


@dataclass(frozen=True, slots=True)
class InspectedRobotUploadAsset:
    asset_id: str
    path: str
    local_path: Path
    role: str
    media_type: str
    camera_id: str | None
    metadata: Mapping[str, Any]
    size_bytes: int
    sha256: str
    crc64: int


def inspect_robot_assets(
    assets: Sequence[RobotUploadAssetInput],
) -> tuple[InspectedRobotUploadAsset, ...]:
    """Validate local Raw files and freeze their full-object integrity facts."""

    inspected: list[InspectedRobotUploadAsset] = []
    paths: set[str] = set()
    for item in assets:
        _validate_relative_path(item.path)
        if item.path in paths:
            raise ValueError(f"duplicate robot upload asset path: {item.path}")
        paths.add(item.path)
        local_path = item.local_path.expanduser().resolve()
        if not local_path.is_file():
            raise ValueError(f"robot upload asset is missing: {item.path}")
        size = local_path.stat().st_size
        if size < 1:
            raise ValueError(f"robot upload asset is empty: {item.path}")
        if item.sha256 is None or item.crc64 is None:
            actual_sha256, actual_crc64 = _checksums(local_path)
        else:
            actual_sha256, actual_crc64 = item.sha256, item.crc64
        if len(actual_sha256) != 64 or any(
            value not in "0123456789abcdef" for value in actual_sha256
        ):
            raise ValueError(f"invalid SHA-256 for robot upload asset: {item.path}")
        if not 0 <= actual_crc64 <= 2**64 - 1:
            raise ValueError(f"invalid CRC64 for robot upload asset: {item.path}")
        asset_id = f"asset-{hashlib.sha256(item.path.encode()).hexdigest()[:24]}"
        media_type = item.media_type or mimetypes.guess_type(item.path)[0]
        inspected.append(
            InspectedRobotUploadAsset(
                asset_id=asset_id,
                path=item.path,
                local_path=local_path,
                role=item.role,
                media_type=media_type or "application/octet-stream",
                camera_id=item.camera_id,
                metadata=dict(item.metadata),
                size_bytes=size,
                sha256=actual_sha256,
                crc64=actual_crc64,
            )
        )
    if not inspected:
        raise ValueError("robot upload requires at least one Raw asset")
    return tuple(inspected)


def upload_robot_ingest(
    *,
    assets: Sequence[RobotUploadAssetInput],
    collection_task_id: str,
    robot_id: str,
    source_format: str,
    source_format_version: str,
    capture_mode: str,
    capture_started_at: str,
    capture_ended_at: str,
    api_base_url: str,
    robot_credential: str,
    declared_episode_count: int | None = None,
    cameras: Sequence[Mapping[str, Any]] = (),
    format_metadata: Mapping[str, Any] | None = None,
    organization_id: str | None = None,
    project_id: str | None = None,
    collection_job_id: str | None = None,
    state_path: Path | None = None,
    part_size: int = DEFAULT_PART_SIZE,
    max_part_retries: int = DEFAULT_MAX_PART_RETRIES,
    retry_base_seconds: float = DEFAULT_RETRY_BASE_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    http: UploadHttpPort | None = None,
) -> dict[str, Any]:
    """Upload every declared asset without proxying file bodies through the API.

    The state sidecar contains only a client UUID and a public manifest fingerprint;
    it never stores the robot credential or any object-storage authorization URL.
    Rerunning with the same sidecar asks storage for durable completed parts and skips
    them.  An indeterminate or expired PUT is reconciled before any retransmit.
    """

    if capture_mode not in {"PRESEGMENTED", "CONTINUOUS"}:
        raise ValueError("capture_mode must be PRESEGMENTED or CONTINUOUS")
    if not robot_credential.strip():
        raise ValueError("robot_credential is required")
    if part_size < 5 * 1024**2:
        raise ValueError("part_size must be at least 5 MiB")
    if not 0 <= max_part_retries <= 10:
        raise ValueError("max_part_retries must be between 0 and 10")
    if not 0 < retry_base_seconds <= 60:
        raise ValueError("retry_base_seconds must be greater than 0 and at most 60")

    inspected = inspect_robot_assets(assets)
    public_fingerprint = _public_fingerprint(
        inspected,
        collection_task_id=collection_task_id,
        robot_id=robot_id,
        source_format=source_format,
        source_format_version=source_format_version,
        capture_mode=capture_mode,
        capture_started_at=capture_started_at,
        capture_ended_at=capture_ended_at,
        declared_episode_count=declared_episode_count,
        cameras=cameras,
        format_metadata=format_metadata or {},
    )
    resolved_state_path = state_path or inspected[0].local_path.parent / DEFAULT_STATE_NAME
    client_upload_id = _load_or_create_client_upload_id(
        resolved_state_path,
        public_fingerprint=public_fingerprint,
        collection_task_id=collection_task_id,
        robot_id=robot_id,
        source_format=source_format,
    )
    manifest: dict[str, Any] = {
        "schema_version": "robot-ingest/v1",
        "client_upload_id": client_upload_id,
        "collection_task_id": collection_task_id,
        "collection_job_id": collection_job_id,
        "robot_id": robot_id,
        "capture_mode": capture_mode,
        "source_format": source_format,
        "source_format_version": source_format_version,
        "capture_started_at": capture_started_at,
        "capture_ended_at": capture_ended_at,
        "declared_episode_count": declared_episode_count,
        "assets": [
            {
                "asset_id": item.asset_id,
                "path": item.path,
                "role": item.role,
                "media_type": item.media_type,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
                "crc64": str(item.crc64),
                "camera_id": item.camera_id,
                "metadata": dict(item.metadata),
            }
            for item in inspected
        ],
        "cameras": [dict(camera) for camera in cameras],
        "format_metadata": dict(format_metadata or {}),
    }
    # Legacy-compatible scope declarations are sent only when explicitly supplied;
    # robot mode needs only collection_task_id, and the server never authorizes from
    # these values.
    if organization_id is not None:
        manifest["organization_id"] = organization_id
    if project_id is not None:
        manifest["project_id"] = project_id
    client = http or UrllibUploadHttpClient()
    normalized_base = api_base_url.strip().rstrip("/")
    if normalized_base.endswith("/api/v1"):
        normalized_base = normalized_base[: -len("/api/v1")]
    root = f"{normalized_base}/api/v1/robot-ingest/uploads"
    headers = {
        "Authorization": f"Bearer {robot_credential}",
        "Content-Type": "application/json",
    }
    created = cast(
        dict[str, Any],
        _json_request(
            client,
            "POST",
            root,
            headers=headers,
            payload=manifest,
            expected={200, 201},
            max_retries=max_part_retries,
            retry_base_seconds=retry_base_seconds,
            sleep=sleep,
        ),
    )
    upload = cast(dict[str, Any], created["data"])
    upload_id = str(upload["upload_id"])
    upload_url = f"{root}/{quote(upload_id, safe='')}"
    state = str(upload["state"])
    if state == "COMMITTED":
        return created
    if state == "PAUSED":
        resumed = cast(
            dict[str, Any],
            _json_request(
                client,
                "POST",
                f"{upload_url}:resume",
                headers=headers,
                payload=None,
                expected={200},
                max_retries=max_part_retries,
                retry_base_seconds=retry_base_seconds,
                sleep=sleep,
            ),
        )
        upload = cast(dict[str, Any], resumed["data"])
        state = str(upload["state"])
    if state in {"FAILED", "CANCELLED"}:
        raise RuntimeError(f"robot upload cannot resume from terminal state {state}")

    uploaded_assets = {
        str(item["asset_id"]): item for item in cast(list[dict[str, Any]], upload["assets"])
    }
    for item in inspected:
        remote = uploaded_assets.get(item.asset_id)
        if remote is None:
            raise RuntimeError(f"platform omitted robot upload asset {item.path}")
        if str(remote["state"]) == "COMPLETED":
            continue
        _upload_asset(
            client,
            upload_url=upload_url,
            headers=headers,
            asset=item,
            requested_part_size=part_size,
            max_part_retries=max_part_retries,
            retry_base_seconds=retry_base_seconds,
            sleep=sleep,
        )

    return cast(
        dict[str, Any],
        _json_request(
            client,
            "POST",
            f"{upload_url}:commit",
            headers=headers,
            payload=None,
            expected={200},
            max_retries=max_part_retries,
            retry_base_seconds=retry_base_seconds,
            sleep=sleep,
        ),
    )


def _upload_asset(
    client: UploadHttpPort,
    *,
    upload_url: str,
    headers: Mapping[str, str],
    asset: InspectedRobotUploadAsset,
    requested_part_size: int,
    max_part_retries: int,
    retry_base_seconds: float,
    sleep: Callable[[float], None],
) -> None:
    part_size = max(
        requested_part_size,
        math.ceil(asset.size_bytes / MAX_MULTIPART_PARTS),
    )
    part_count = math.ceil(asset.size_bytes / part_size)
    authorize_url = f"{upload_url}/assets/{quote(asset.asset_id, safe='')}:authorize-parts"
    completed: dict[int, str] = {}
    with asset.local_path.open("rb") as source:
        for part_number in range(1, part_count + 1):
            grant = _authorize_part(
                client,
                authorize_url=authorize_url,
                headers=headers,
                part_number=part_number,
                max_retries=max_part_retries,
                retry_base_seconds=retry_base_seconds,
                sleep=sleep,
            )
            completed.update(_uploaded_etags(grant))
            if part_number in completed:
                continue
            authorization = _authorization_for(grant, part_number)
            source.seek((part_number - 1) * part_size)
            body = source.read(min(part_size, asset.size_bytes - source.tell()))
            if not body:
                raise RuntimeError(f"asset ended before part {part_number}: {asset.path}")
            print(
                f"upload {asset.path} part {part_number}/{part_count}",
                file=__import__("sys").stderr,
            )
            etag = _put_part_with_reconciliation(
                client,
                authorize_url=authorize_url,
                headers=headers,
                part_number=part_number,
                body=body,
                authorization=authorization,
                max_part_retries=max_part_retries,
                retry_base_seconds=retry_base_seconds,
                sleep=sleep,
            )
            completed[part_number] = etag

    final_grant = _authorize_part(
        client,
        authorize_url=authorize_url,
        headers=headers,
        part_number=1,
        max_retries=max_part_retries,
        retry_base_seconds=retry_base_seconds,
        sleep=sleep,
    )
    completed.update(_uploaded_etags(final_grant))
    expected_numbers = list(range(1, part_count + 1))
    if sorted(completed) != expected_numbers:
        raise RuntimeError("storage part listing is incomplete; refusing asset completion")
    _json_request(
        client,
        "POST",
        f"{upload_url}/assets/{quote(asset.asset_id, safe='')}:complete",
        headers=headers,
        payload={
            "parts": [
                {"part_number": number, "etag": completed[number]} for number in expected_numbers
            ]
        },
        expected={200},
        max_retries=max_part_retries,
        retry_base_seconds=retry_base_seconds,
        sleep=sleep,
    )


def _put_part_with_reconciliation(
    client: UploadHttpPort,
    *,
    authorize_url: str,
    headers: Mapping[str, str],
    part_number: int,
    body: bytes,
    authorization: Mapping[str, Any],
    max_part_retries: int,
    retry_base_seconds: float,
    sleep: Callable[[float], None],
) -> str:
    current = authorization
    for attempt in range(max_part_retries + 1):
        response: HttpResponse | None
        try:
            response = client.request(
                "PUT",
                str(current["url"]),
                headers={"Content-Length": str(len(body))},
                body=body,
            )
        except UploadTransportError:
            response = None
        if response is not None and response.status in {200, 201, 204}:
            etag = _header(response.headers, "ETag")
            if etag is not None and etag.strip():
                return etag.strip().strip('"')
        if response is not None and response.status not in _RECOVERABLE_PART_STATUSES:
            _raise_for_status(response, {200, 201, 204})

        grant = _authorize_part(
            client,
            authorize_url=authorize_url,
            headers=headers,
            part_number=part_number,
            max_retries=max_part_retries,
            retry_base_seconds=retry_base_seconds,
            sleep=sleep,
        )
        durable = _uploaded_etags(grant).get(part_number)
        if durable:
            return durable
        if attempt == max_part_retries:
            status = "no response" if response is None else f"HTTP {response.status}"
            raise RuntimeError(
                f"part {part_number} did not upload after {max_part_retries} recovery "
                f"attempts ({status}); rerun with the same state file to resume"
            )
        current = _authorization_for(grant, part_number)
        sleep(min(60.0, retry_base_seconds * float(2**attempt)))
    raise AssertionError("bounded robot part recovery unexpectedly exhausted")


def _authorize_part(
    client: UploadHttpPort,
    *,
    authorize_url: str,
    headers: Mapping[str, str],
    part_number: int,
    max_retries: int,
    retry_base_seconds: float,
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        _json_request(
            client,
            "POST",
            authorize_url,
            headers=headers,
            payload={"part_numbers": [part_number]},
            expected={200},
            max_retries=max_retries,
            retry_base_seconds=retry_base_seconds,
            sleep=sleep,
        ),
    )


def _uploaded_etags(grant: Mapping[str, Any]) -> dict[int, str]:
    uploaded: dict[int, str] = {}
    for raw in cast(list[dict[str, Any]], grant.get("uploaded_parts", [])):
        etag = str(raw.get("etag") or "").strip().strip('"')
        if etag:
            uploaded[int(raw["part_number"])] = etag
    return uploaded


def _authorization_for(grant: Mapping[str, Any], part_number: int) -> Mapping[str, Any]:
    matches = [
        raw
        for raw in cast(list[dict[str, Any]], grant.get("authorizations", []))
        if int(raw.get("part_number", -1)) == part_number
    ]
    if len(matches) != 1 or not matches[0].get("url"):
        raise RuntimeError("platform omitted a required robot part authorization")
    return matches[0]


def _load_or_create_client_upload_id(
    path: Path,
    *,
    public_fingerprint: str,
    collection_task_id: str,
    robot_id: str,
    source_format: str,
) -> str:
    if path.exists():
        try:
            document = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"robot upload state file is invalid: {path}") from exc
        expected = {
            "schema_version": STATE_SCHEMA,
            "public_manifest_fingerprint": public_fingerprint,
            "collection_task_id": collection_task_id,
            "robot_id": robot_id,
            "source_format": source_format,
        }
        if any(document.get(key) != value for key, value in expected.items()):
            raise ValueError(
                "robot upload state belongs to different files or task; choose a new state path"
            )
        client_upload_id = str(document.get("client_upload_id") or "")
        if client_upload_id:
            return client_upload_id
        raise ValueError(f"robot upload state omitted client_upload_id: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    client_upload_id = str(uuid4())
    document = {
        "schema_version": STATE_SCHEMA,
        "client_upload_id": client_upload_id,
        "public_manifest_fingerprint": public_fingerprint,
        "collection_task_id": collection_task_id,
        "robot_id": robot_id,
        "source_format": source_format,
    }
    if _publish_state(path, document):
        return client_upload_id
    # Another uploader won the atomic publish. Its UUID is authoritative; validate
    # the complete document rather than allowing two local sessions for one capture.
    return _load_or_create_client_upload_id(
        path,
        public_fingerprint=public_fingerprint,
        collection_task_id=collection_task_id,
        robot_id=robot_id,
        source_format=source_format,
    )


def _publish_state(path: Path, document: Mapping[str, Any]) -> bool:
    body = json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(body)
            target.flush()
            os.fsync(target.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def _public_fingerprint(
    assets: Sequence[InspectedRobotUploadAsset],
    **facts: Any,
) -> str:
    document = {
        **facts,
        "assets": [
            {
                "path": item.path,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
                "crc64": str(item.crc64),
            }
            for item in assets
        ],
    }
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _checksums(path: Path) -> tuple[str, int]:
    sha256 = hashlib.sha256()
    crc64 = 0
    with path.open("rb") as source:
        while chunk := source.read(8 * 1024**2):
            sha256.update(chunk)
            crc64 = crc64_ecma(chunk, crc64)
    return sha256.hexdigest(), crc64


def _header(headers: Mapping[str, str], name: str) -> str | None:
    lowered = name.casefold()
    return next(
        (value for key, value in headers.items() if key.casefold() == lowered),
        None,
    )


def _validate_relative_path(value: str) -> None:
    parts = value.replace("\\", "/").split("/")
    if value.startswith(("/", "\\")) or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"robot upload path must be normalized and relative: {value}")
