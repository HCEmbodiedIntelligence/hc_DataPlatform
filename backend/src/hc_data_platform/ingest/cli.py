from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .models import RolloutManifestV1, raw_object_key
from .ports import crc64_ecma

DEFAULT_PART_SIZE = 64 * 1024 * 1024
MIN_MULTIPART_PART_SIZE = 5 * 1024 * 1024
MAX_MULTIPART_PARTS = 10_000
DEFAULT_MAX_PART_RETRIES = 5
DEFAULT_RETRY_BASE_SECONDS = 1.0
_RETRYABLE_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


class UploadTransportError(RuntimeError):
    """A direct API or object-storage request has no reliable HTTP result."""


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)


class UploadHttpPort(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
    ) -> HttpResponse: ...


class UrllibUploadHttpClient:
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
    ) -> HttpResponse:
        request = Request(url, data=body, headers=dict(headers or {}), method=method)
        try:
            with urlopen(request) as response:  # noqa: S310 - caller supplies the API/storage URLs
                return HttpResponse(
                    status=response.status,
                    headers=dict(response.headers.items()),
                    body=response.read(),
                )
        except HTTPError as exc:
            return HttpResponse(
                status=exc.code,
                headers=dict(exc.headers.items()),
                body=exc.read(),
            )
        except (OSError, TimeoutError, URLError) as exc:
            raise UploadTransportError("upload transport did not return a response") from exc


def effective_part_size(*, file_size: int, requested_part_size: int) -> int:
    """Choose a legal multipart size without ever exceeding 10,000 parts.

    The old fixed 64 MiB default is valid for routine bundles but produces more
    than 10,000 parts for a multi-TiB MCAP.  This calculation is local and
    deterministic, so a restarted importer selects exactly the same boundaries.
    """

    if file_size < 1:
        raise ValueError("file_size must be positive")
    if requested_part_size < MIN_MULTIPART_PART_SIZE:
        raise ValueError("part_size must be at least 5 MiB for OSS/S3 multipart uploads")
    return max(
        requested_part_size,
        MIN_MULTIPART_PART_SIZE,
        math.ceil(file_size / MAX_MULTIPART_PARTS),
    )


def inspect_offline_bundle(mcap_path: Path, manifest_path: Path) -> dict[str, Any]:
    if mcap_path.suffix.lower() != ".mcap":
        raise ValueError("offline raw file must use the .mcap extension")
    manifest = RolloutManifestV1.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    sha256 = hashlib.sha256()
    crc64 = 0
    size = 0
    with mcap_path.open("rb") as source:
        while chunk := source.read(8 * 1024 * 1024):
            size += len(chunk)
            sha256.update(chunk)
            crc64 = crc64_ecma(chunk, crc64)
    return {
        "valid": size == manifest.file_size
        and sha256.hexdigest() == manifest.sha256
        and crc64 == manifest.crc64,
        "object_key": raw_object_key(manifest),
        "size": size,
        "sha256": sha256.hexdigest(),
        "crc64": crc64,
    }


def import_offline_bundle(
    mcap_path: Path,
    manifest_path: Path,
    *,
    api_base_url: str,
    region_code: str,
    access_token: str,
    idempotency_key: str,
    part_size: int = DEFAULT_PART_SIZE,
    max_part_retries: int = DEFAULT_MAX_PART_RETRIES,
    retry_base_seconds: float = DEFAULT_RETRY_BASE_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    http: UploadHttpPort | None = None,
) -> dict[str, Any]:
    """Import an offline bundle with resumable, bounded multipart recovery.

    The server remains the source of truth for which parts reached object
    storage.  After any indeterminate direct PUT failure we reconcile that
    state before sending the bytes again, so a lost response never requires a
    human to restart a multi-hundred-GB bundle.
    """

    _validate_retry_policy(max_part_retries, retry_base_seconds)
    inspection = inspect_offline_bundle(mcap_path, manifest_path)
    if not inspection["valid"]:
        raise ValueError("offline MCAP bytes do not match the manifest integrity fields")
    part_size = effective_part_size(
        file_size=int(inspection["size"]), requested_part_size=part_size
    )
    manifest = RolloutManifestV1.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    client = http or UrllibUploadHttpClient()
    base = api_base_url.rstrip("/")
    session_root = (
        f"{base}/api/v1/projects/{quote(manifest.project_id, safe='')}"
        f"/regions/{quote(region_code, safe='')}/upload-sessions"
    )
    api_headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    created = _json_request(
        client,
        "POST",
        session_root,
        headers={**api_headers, "Idempotency-Key": idempotency_key},
        payload={"manifest": manifest.model_dump(mode="json"), "part_numbers": []},
        expected={200, 201},
        max_retries=max_part_retries,
        retry_base_seconds=retry_base_seconds,
        sleep=sleep,
    )
    session = created["session"]
    session_id = str(session["session_id"])
    session_url = f"{session_root}/{quote(session_id, safe='')}"
    status = str(session["status"])

    if status == "PAUSED":
        resumed = _json_request(
            client,
            "POST",
            f"{session_url}:resume",
            headers=api_headers,
            payload={"part_numbers": []},
            expected={200},
            max_retries=max_part_retries,
            retry_base_seconds=retry_base_seconds,
            sleep=sleep,
        )
        status = str(resumed["session"]["status"])
    if status == "RAW_COMMITTED":
        return _commit_manifest(
            client,
            session_url,
            api_headers,
            manifest,
            max_retries=max_part_retries,
            retry_base_seconds=retry_base_seconds,
            sleep=sleep,
        )
    if status == "MULTIPART_COMPLETED":
        return _commit_manifest(
            client,
            session_url,
            api_headers,
            manifest,
            max_retries=max_part_retries,
            retry_base_seconds=retry_base_seconds,
            sleep=sleep,
        )
    if status != "UPLOADING":
        raise RuntimeError(f"offline import cannot continue session in state {status}")

    uploaded = _json_request(
        client,
        "GET",
        f"{session_url}/parts",
        headers=api_headers,
        payload=None,
        expected={200},
        max_retries=max_part_retries,
        retry_base_seconds=retry_base_seconds,
        sleep=sleep,
    )
    uploaded_numbers = {
        int(part["part_number"]) for part in uploaded if part.get("status") == "UPLOADED"
    }
    part_number = 0
    with mcap_path.open("rb") as source:
        while body := source.read(part_size):
            part_number += 1
            if part_number in uploaded_numbers:
                continue
            authorization = _renew_part_authorization(
                client,
                session_url=session_url,
                api_headers=api_headers,
                part_number=part_number,
                max_retries=max_part_retries,
                retry_base_seconds=retry_base_seconds,
                sleep=sleep,
            )
            _put_part_with_recovery(
                client,
                session_url=session_url,
                api_headers=api_headers,
                part_number=part_number,
                body=body,
                authorization=authorization,
                max_part_retries=max_part_retries,
                retry_base_seconds=retry_base_seconds,
                sleep=sleep,
            )

    uploaded = _json_request(
        client,
        "GET",
        f"{session_url}/parts",
        headers=api_headers,
        payload=None,
        expected={200},
        max_retries=max_part_retries,
        retry_base_seconds=retry_base_seconds,
        sleep=sleep,
    )
    completion_parts: list[dict[str, Any]] = []
    for part in uploaded:
        if part.get("status") != "UPLOADED":
            continue
        if not part.get("etag"):
            raise RuntimeError("storage part listing omitted an ETag; refusing completion")
        completion_parts.append(
            {"part_number": int(part["part_number"]), "etag": str(part["etag"])}
        )
    if [part["part_number"] for part in completion_parts] != list(range(1, part_number + 1)):
        raise RuntimeError("storage part listing is incomplete; refusing multipart completion")
    _json_request(
        client,
        "POST",
        f"{session_url}:complete",
        headers=api_headers,
        payload={"parts": completion_parts},
        expected={200},
        max_retries=max_part_retries,
        retry_base_seconds=retry_base_seconds,
        sleep=sleep,
    )
    return _commit_manifest(
        client,
        session_url,
        api_headers,
        manifest,
        max_retries=max_part_retries,
        retry_base_seconds=retry_base_seconds,
        sleep=sleep,
    )


def _commit_manifest(
    client: UploadHttpPort,
    session_url: str,
    headers: Mapping[str, str],
    manifest: RolloutManifestV1,
    *,
    max_retries: int,
    retry_base_seconds: float,
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    result = _json_request(
        client,
        "POST",
        f"{session_url}:commit-manifest",
        headers=headers,
        payload=manifest.model_dump(mode="json"),
        expected={200},
        max_retries=max_retries,
        retry_base_seconds=retry_base_seconds,
        sleep=sleep,
    )
    return dict(result)


def _json_request(
    client: UploadHttpPort,
    method: str,
    url: str,
    *,
    headers: Mapping[str, str],
    payload: Any,
    expected: set[int],
    max_retries: int = DEFAULT_MAX_PART_RETRIES,
    retry_base_seconds: float = DEFAULT_RETRY_BASE_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    response = _request_with_retry(
        client,
        method,
        url,
        headers=headers,
        body=body,
        max_retries=max_retries,
        retry_base_seconds=retry_base_seconds,
        sleep=sleep,
    )
    _raise_for_status(response, expected)
    return response.json()


def _put_part_with_recovery(
    client: UploadHttpPort,
    *,
    session_url: str,
    api_headers: Mapping[str, str],
    part_number: int,
    body: bytes,
    authorization: Mapping[str, Any],
    max_part_retries: int,
    retry_base_seconds: float,
    sleep: Callable[[float], None],
) -> None:
    """PUT one part, reconciling durable server state before any retransmit."""

    current_authorization = authorization
    for attempt in range(max_part_retries + 1):
        try:
            response = client.request(
                "PUT",
                str(current_authorization["url"]),
                headers={"Content-Length": str(len(body))},
                body=body,
            )
        except UploadTransportError:
            response = None

        if response is not None and response.status in {200, 201, 204}:
            return
        if response is not None and response.status not in {
            401,
            403,
            *_RETRYABLE_HTTP_STATUSES,
        }:
            _raise_for_status(response, {200, 201, 204})

        # A response can disappear after S3/MinIO has already persisted the
        # bytes.  Always ask the durable session state before retransmitting.
        if _part_is_uploaded(
            client,
            session_url=session_url,
            api_headers=api_headers,
            part_number=part_number,
            max_retries=max_part_retries,
            retry_base_seconds=retry_base_seconds,
            sleep=sleep,
        ):
            return
        if attempt == max_part_retries:
            status = "no response" if response is None else f"HTTP {response.status}"
            raise RuntimeError(
                f"part {part_number} did not upload after {max_part_retries} recovery attempts "
                f"({status}); rerun the same command to resume from server state"
            )

        if response is not None and response.status in {401, 403}:
            current_authorization = _renew_part_authorization(
                client,
                session_url=session_url,
                api_headers=api_headers,
                part_number=part_number,
                max_retries=max_part_retries,
                retry_base_seconds=retry_base_seconds,
                sleep=sleep,
            )
        else:
            current_authorization = _record_failed_part_and_renew(
                client,
                session_url=session_url,
                api_headers=api_headers,
                part_number=part_number,
                failure_code=_failure_code(response),
                max_retries=max_part_retries,
                retry_base_seconds=retry_base_seconds,
                sleep=sleep,
            )
        sleep(_retry_delay_seconds(attempt, retry_base_seconds))


def _part_is_uploaded(
    client: UploadHttpPort,
    *,
    session_url: str,
    api_headers: Mapping[str, str],
    part_number: int,
    max_retries: int,
    retry_base_seconds: float,
    sleep: Callable[[float], None],
) -> bool:
    parts = _json_request(
        client,
        "GET",
        f"{session_url}/parts",
        headers=api_headers,
        payload=None,
        expected={200},
        max_retries=max_retries,
        retry_base_seconds=retry_base_seconds,
        sleep=sleep,
    )
    return any(
        int(part["part_number"]) == part_number and part.get("status") == "UPLOADED"
        for part in parts
    )


def _record_failed_part_and_renew(
    client: UploadHttpPort,
    *,
    session_url: str,
    api_headers: Mapping[str, str],
    part_number: int,
    failure_code: str,
    max_retries: int,
    retry_base_seconds: float,
    sleep: Callable[[float], None],
) -> Mapping[str, Any]:
    """Record a known failed PUT once, then get its fresh authorization.

    `retry-parts` intentionally increments a durable retry counter and has no
    idempotency key.  If its HTTP response is lost, renewing is safer than
    replaying that mutation: it avoids consuming a second retry allowance.
    """

    payload = {"failures": [{"part_number": part_number, "failure_code": failure_code}]}
    try:
        response = client.request(
            "POST",
            f"{session_url}:retry-parts",
            headers=api_headers,
            body=json.dumps(payload, separators=(",", ":")).encode(),
        )
    except UploadTransportError:
        response = None
    if response is not None and response.status == 200:
        result = response.json()
        return _single_authorization(result, part_number)
    if response is not None and response.status not in _RETRYABLE_HTTP_STATUSES:
        _raise_for_status(response, {200})
    return _renew_part_authorization(
        client,
        session_url=session_url,
        api_headers=api_headers,
        part_number=part_number,
        max_retries=max_retries,
        retry_base_seconds=retry_base_seconds,
        sleep=sleep,
    )


def _renew_part_authorization(
    client: UploadHttpPort,
    *,
    session_url: str,
    api_headers: Mapping[str, str],
    part_number: int,
    max_retries: int,
    retry_base_seconds: float,
    sleep: Callable[[float], None],
) -> Mapping[str, Any]:
    result = _json_request(
        client,
        "POST",
        f"{session_url}:renew",
        headers=api_headers,
        payload={"part_numbers": [part_number]},
        expected={200},
        max_retries=max_retries,
        retry_base_seconds=retry_base_seconds,
        sleep=sleep,
    )
    return _single_authorization(result, part_number)


def _single_authorization(result: Any, part_number: int) -> Mapping[str, Any]:
    if not isinstance(result, list) or len(result) != 1:
        raise RuntimeError("upload protocol returned an invalid part authorization batch")
    authorization = result[0]
    if (
        not isinstance(authorization, Mapping)
        or int(authorization.get("part_number", -1)) != part_number
    ):
        raise RuntimeError("upload protocol returned an authorization for the wrong part")
    return authorization


def _request_with_retry(
    client: UploadHttpPort,
    method: str,
    url: str,
    *,
    headers: Mapping[str, str],
    body: bytes | None,
    max_retries: int,
    retry_base_seconds: float,
    sleep: Callable[[float], None],
) -> HttpResponse:
    for attempt in range(max_retries + 1):
        try:
            response = client.request(method, url, headers=headers, body=body)
        except UploadTransportError:
            if attempt == max_retries:
                raise
        else:
            if response.status not in _RETRYABLE_HTTP_STATUSES or attempt == max_retries:
                return response
        sleep(_retry_delay_seconds(attempt, retry_base_seconds))
    raise AssertionError("bounded request retry unexpectedly exhausted without a result")


def _validate_retry_policy(max_part_retries: int, retry_base_seconds: float) -> None:
    if not 0 <= max_part_retries <= 10:
        raise ValueError("max_part_retries must be between 0 and 10")
    if not 0 < retry_base_seconds <= 60:
        raise ValueError("retry_base_seconds must be greater than 0 and at most 60")


def _retry_delay_seconds(attempt: int, retry_base_seconds: float) -> float:
    delay = retry_base_seconds * float(2**attempt)
    return 60.0 if delay > 60.0 else delay


def _failure_code(response: HttpResponse | None) -> str:
    if response is None:
        return "NETWORK_INTERRUPTED"
    return f"HTTP_{response.status}"


def _raise_for_status(response: HttpResponse, expected: set[int]) -> None:
    if response.status in expected:
        return
    detail = response.body.decode(errors="replace")[:1000]
    raise RuntimeError(f"upload protocol returned HTTP {response.status}: {detail}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate an offline MCAP/manifest bundle and import it via the upload API"
    )
    parser.add_argument("mcap", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--api-base-url")
    parser.add_argument("--region-code")
    parser.add_argument("--access-token", default=os.environ.get("HC_DATA_ACCESS_TOKEN"))
    parser.add_argument("--idempotency-key")
    parser.add_argument(
        "--part-size",
        type=int,
        default=DEFAULT_PART_SIZE,
        help=(
            "preferred multipart size in bytes (minimum 5 MiB); the importer automatically "
            "raises it if needed to keep a bundle within the 10,000-part S3 limit"
        ),
    )
    parser.add_argument(
        "--max-part-retries",
        type=int,
        default=DEFAULT_MAX_PART_RETRIES,
        help="automatic recovery attempts for each failed multipart PUT (0-10; default: 5)",
    )
    parser.add_argument(
        "--retry-base-seconds",
        type=float,
        default=DEFAULT_RETRY_BASE_SECONDS,
        help="initial exponential-backoff delay in seconds (greater than 0 and at most 60)",
    )
    args = parser.parse_args()

    if args.validate_only:
        print(json.dumps(inspect_offline_bundle(args.mcap, args.manifest), indent=2))
        return
    required = {
        "--api-base-url": args.api_base_url,
        "--region-code": args.region_code,
        "--access-token or HC_DATA_ACCESS_TOKEN": args.access_token,
        "--idempotency-key": args.idempotency_key,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        parser.error(f"missing required import options: {', '.join(missing)}")
    result = import_offline_bundle(
        args.mcap,
        args.manifest,
        api_base_url=args.api_base_url,
        region_code=args.region_code,
        access_token=args.access_token,
        idempotency_key=args.idempotency_key,
        part_size=args.part_size,
        max_part_retries=args.max_part_retries,
        retry_base_seconds=args.retry_base_seconds,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
