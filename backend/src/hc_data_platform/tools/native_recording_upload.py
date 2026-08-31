"""Upload a prepared native recording through the platform's v2 OSS flow."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

from hc_data_platform.continuous_recordings.asset_models import (
    CreateRecordingUploadCommand,
    RecordingAssetStatus,
)
from hc_data_platform.ingest.cli import (
    DEFAULT_MAX_PART_RETRIES,
    DEFAULT_RETRY_BASE_SECONDS,
    HttpResponse,
    UploadHttpPort,
    UrllibUploadHttpClient,
    _json_request,
    _raise_for_status,
    _request_with_retry,
)

from .native_unitree_g1_recording import UPLOAD_COMMAND_PATH


def _load_env_file(path: Path | None) -> None:
    if path is None or not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and name not in os.environ:
            os.environ[name] = value


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"environment variable {name} is required")
    return value


def _header(headers: Mapping[str, str], name: str) -> str | None:
    lowered = name.lower()
    return next((value for key, value in headers.items() if key.lower() == lowered), None)


def _put_part(
    client: UploadHttpPort,
    *,
    url: str,
    body: bytes,
    max_retries: int,
    retry_base_seconds: float,
    sleep: Callable[[float], None],
) -> str:
    response: HttpResponse = _request_with_retry(
        client,
        "PUT",
        url,
        headers={"Content-Length": str(len(body))},
        body=body,
        max_retries=max_retries,
        retry_base_seconds=retry_base_seconds,
        sleep=sleep,
    )
    _raise_for_status(response, {200, 201, 204})
    etag = _header(response.headers, "ETag")
    if etag is None or not etag.strip():
        raise RuntimeError("OSS upload response omitted the required ETag")
    return etag.strip()


def upload_native_recording(
    bundle_dir: Path,
    *,
    project_id: str,
    organization_id: str,
    region_code: str,
    api_base_url: str,
    access_token: str,
    max_part_retries: int = DEFAULT_MAX_PART_RETRIES,
    retry_base_seconds: float = DEFAULT_RETRY_BASE_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    http: UploadHttpPort | None = None,
) -> dict[str, Any]:
    bundle = bundle_dir.expanduser().resolve()
    command_path = bundle / UPLOAD_COMMAND_PATH
    if not command_path.is_file():
        raise ValueError(f"native recording has no {UPLOAD_COMMAND_PATH}: {bundle}")
    command = CreateRecordingUploadCommand.model_validate_json(
        command_path.read_text(encoding="utf-8")
    )
    local_files = {asset.path: bundle / asset.path for asset in command.assets}
    for relative, path in local_files.items():
        if not path.is_file():
            raise ValueError(f"recording asset is missing: {relative}")

    client = http or UrllibUploadHttpClient()
    root = (
        f"{api_base_url.rstrip('/')}/api/v1/projects/{quote(project_id, safe='')}"
        f"/regions/{quote(region_code, safe='')}/continuous-recordings/uploads"
    )
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Organization-Id": organization_id,
    }
    grant = _json_request(
        client,
        "POST",
        root,
        headers=headers,
        payload=command.model_dump(mode="json"),
        expected={200, 201},
        max_retries=max_part_retries,
        retry_base_seconds=retry_base_seconds,
        sleep=sleep,
    )
    upload_id = str(grant["upload"]["upload_id"])
    upload_url = f"{root}/{quote(upload_id, safe='')}"
    grants_by_path = {
        str(item["asset"]["path"]): item for item in cast(list[dict[str, Any]], grant["assets"])
    }
    for manifest in command.assets:
        asset_grant = grants_by_path.get(manifest.path)
        if asset_grant is None:
            raise RuntimeError(f"platform omitted upload grant for {manifest.path}")
        asset = cast(dict[str, Any], asset_grant["asset"])
        if asset.get("status") == RecordingAssetStatus.COMMITTED.value:
            continue
        asset_id = str(asset["asset_id"])
        authorize_url = f"{upload_url}/assets/{quote(asset_id, safe='')}:authorize-parts"
        complete_url = f"{upload_url}/assets/{quote(asset_id, safe='')}:complete"
        part_size = math.ceil(manifest.size / manifest.part_count)
        completed_parts: list[dict[str, Any]] = []
        with local_files[manifest.path].open("rb") as source:
            for part_number in range(1, manifest.part_count + 1):
                body = source.read(part_size)
                if not body:
                    raise RuntimeError(f"asset ended before part {part_number}: {manifest.path}")
                authorization = _json_request(
                    client,
                    "POST",
                    authorize_url,
                    headers=headers,
                    payload={"part_numbers": [part_number]},
                    expected={200},
                    max_retries=max_part_retries,
                    retry_base_seconds=retry_base_seconds,
                    sleep=sleep,
                )
                parts = cast(list[dict[str, Any]], authorization["parts"])
                if len(parts) != 1 or int(parts[0]["part_number"]) != part_number:
                    raise RuntimeError("platform returned the wrong part authorization")
                print(
                    f"upload {manifest.path} part {part_number}/{manifest.part_count}",
                    file=sys.stderr,
                )
                etag = _put_part(
                    client,
                    url=str(parts[0]["url"]),
                    body=body,
                    max_retries=max_part_retries,
                    retry_base_seconds=retry_base_seconds,
                    sleep=sleep,
                )
                completed_parts.append({"part_number": part_number, "etag": etag})
            if source.read(1):
                raise RuntimeError(f"asset exceeds its declared part count: {manifest.path}")
        _json_request(
            client,
            "POST",
            complete_url,
            headers=headers,
            payload={"parts": completed_parts},
            expected={200},
            max_retries=max_part_retries,
            retry_base_seconds=retry_base_seconds,
            sleep=sleep,
        )
    committed = _json_request(
        client,
        "POST",
        f"{upload_url}:commit",
        headers=headers,
        payload=None,
        expected={200},
        max_retries=max_part_retries,
        retry_base_seconds=retry_base_seconds,
        sleep=sleep,
    )
    return cast(dict[str, Any], committed)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--organization-id", required=True)
    parser.add_argument(
        "--region-code",
        required=True,
        help="platform project region code; this is not the Alibaba OSS bucket region",
    )
    parser.add_argument("--api-base-url", required=True)
    parser.add_argument("--access-token-env", default="HC_DATA_ACCESS_TOKEN")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--max-part-retries", type=int, default=DEFAULT_MAX_PART_RETRIES)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _load_env_file(cast(Path | None, args.env_file))
    try:
        result = upload_native_recording(
            cast(Path, args.bundle_dir),
            project_id=cast(str, args.project_id),
            organization_id=cast(str, args.organization_id),
            region_code=cast(str, args.region_code),
            api_base_url=cast(str, args.api_base_url),
            access_token=_required_env(cast(str, args.access_token_env)),
            max_part_retries=int(args.max_part_retries),
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
