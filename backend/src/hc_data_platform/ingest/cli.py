from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .models import RolloutManifestV1, raw_object_key
from .ports import crc64_ecma

DEFAULT_PART_SIZE = 64 * 1024 * 1024


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
    http: UploadHttpPort | None = None,
) -> dict[str, Any]:
    """Validate locally, then use the exact robot multipart HTTP contract."""

    if part_size < 5 * 1024 * 1024:
        raise ValueError("part_size must be at least 5 MiB for OSS/S3 multipart uploads")
    inspection = inspect_offline_bundle(mcap_path, manifest_path)
    if not inspection["valid"]:
        raise ValueError("offline MCAP bytes do not match the manifest integrity fields")
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
        )
        status = str(resumed["session"]["status"])
    if status == "RAW_COMMITTED":
        return _commit_manifest(client, session_url, api_headers, manifest)
    if status == "MULTIPART_COMPLETED":
        return _commit_manifest(client, session_url, api_headers, manifest)
    if status != "UPLOADING":
        raise RuntimeError(f"offline import cannot continue session in state {status}")

    uploaded = _json_request(
        client,
        "GET",
        f"{session_url}/parts",
        headers=api_headers,
        payload=None,
        expected={200},
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
            authorization = _json_request(
                client,
                "POST",
                f"{session_url}:renew",
                headers=api_headers,
                payload={"part_numbers": [part_number]},
                expected={200},
            )[0]
            response = client.request(
                "PUT",
                str(authorization["url"]),
                headers={"Content-Length": str(len(body))},
                body=body,
            )
            if response.status in {401, 403}:
                authorization = _json_request(
                    client,
                    "POST",
                    f"{session_url}:renew",
                    headers=api_headers,
                    payload={"part_numbers": [part_number]},
                    expected={200},
                )[0]
                response = client.request(
                    "PUT",
                    str(authorization["url"]),
                    headers={"Content-Length": str(len(body))},
                    body=body,
                )
            _raise_for_status(response, {200, 201, 204})

    uploaded = _json_request(
        client,
        "GET",
        f"{session_url}/parts",
        headers=api_headers,
        payload=None,
        expected={200},
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
    )
    return _commit_manifest(client, session_url, api_headers, manifest)


def _commit_manifest(
    client: UploadHttpPort,
    session_url: str,
    headers: Mapping[str, str],
    manifest: RolloutManifestV1,
) -> dict[str, Any]:
    result = _json_request(
        client,
        "POST",
        f"{session_url}:commit-manifest",
        headers=headers,
        payload=manifest.model_dump(mode="json"),
        expected={200},
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
) -> Any:
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    response = client.request(method, url, headers=headers, body=body)
    _raise_for_status(response, expected)
    return response.json()


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
    parser.add_argument("--part-size", type=int, default=DEFAULT_PART_SIZE)
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
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
