"""Import Unitree G1 LeRobot v3 data from local disk or OSS into the platform.

The source remains a compact LeRobot layout (Parquet telemetry plus MP4 camera
streams).  Each selected episode is converted into the platform's canonical MCAP
package and uploaded through the normal upload-session API, so verification,
quality, alignment, Lance publication, dataset projection, and annotation creation
continue through the existing durable workflow.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast
from urllib.parse import unquote, urlparse

from hc_data_platform.ingest.cli import DEFAULT_PART_SIZE, import_offline_bundle
from hc_data_platform.ingest.manifest import parse_manifest_bytes
from hc_data_platform.storage.oss_client import build_oss_bucket

from . import hf_unitree_g1_to_mcap as converter

_CANONICAL_DATA = re.compile(r"^data/chunk-\d{3}/file-\d{3}\.parquet$")
_CANONICAL_EPISODES = re.compile(r"^meta/episodes/chunk-\d{3}/file-\d{3}\.parquet$")
_CANONICAL_VIDEO = re.compile(r"^videos/([^/]+)/chunk-\d{3}/file-\d{3}\.mp4$")


@dataclass(frozen=True, slots=True)
class OssSource:
    bucket: str
    prefix: str


@dataclass(frozen=True, slots=True)
class SourceObject:
    key: str
    size: int


def parse_oss_source_uri(value: str) -> OssSource:
    parsed = urlparse(value.strip())
    prefix = unquote(parsed.path.lstrip("/")).rstrip("/")
    if (
        parsed.scheme != "oss"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or not prefix
        or parsed.params
        or parsed.query
        or parsed.fragment
        or "\\" in prefix
        or any(part in {"", ".", ".."} for part in prefix.split("/"))
    ):
        raise ValueError("OSS source must use oss://<bucket>/<non-empty-prefix>")
    return OssSource(bucket=parsed.hostname, prefix=prefix)


def is_canonical_lerobot_object(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return False
    normalized = path.as_posix()
    if normalized == "meta/info.json" or _CANONICAL_EPISODES.fullmatch(normalized):
        return True
    if _CANONICAL_DATA.fullmatch(normalized):
        return True
    video_match = _CANONICAL_VIDEO.fullmatch(normalized)
    if video_match is None:
        return False
    return video_match.group(1) in {camera.feature_key for camera in converter.CAMERAS}


def _list_source_objects(bucket: Any, prefix: str) -> list[SourceObject]:
    token = ""
    objects: list[SourceObject] = []
    while True:
        result = bucket.list_objects_v2(
            prefix=f"{prefix.rstrip('/')}/",
            continuation_token=token,
            max_keys=1000,
        )
        objects.extend(
            SourceObject(key=str(item.key), size=int(item.size)) for item in result.object_list
        )
        if not result.is_truncated:
            return objects
        token = str(result.next_continuation_token or "")
        if not token:
            raise RuntimeError("OSS returned a truncated listing without a continuation token")


def _dataset_root(objects: list[SourceObject]) -> str:
    marker = "meta/info.json"
    roots = sorted({item.key[: -len(marker)] for item in objects if item.key.endswith(marker)})
    if not roots:
        raise ValueError("the OSS prefix does not contain a LeRobot meta/info.json")
    if len(roots) != 1:
        raise ValueError("the OSS prefix contains multiple LeRobot revision roots")
    return roots[0]


def _download_object(bucket: Any, source: SourceObject, destination: Path) -> None:
    if destination.is_file() and destination.stat().st_size == source.size:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.oss-download")
    result = bucket.get_object(source.key)
    try:
        with temporary.open("wb") as output:
            while chunk := result.read(8 * 1024 * 1024):
                output.write(chunk)
    finally:
        close = getattr(result, "close", None)
        if close is not None:
            close()
    if temporary.stat().st_size != source.size:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"OSS object size changed while downloading: {source.key}")
    os.replace(temporary, destination)


def materialize_oss_source(
    bucket: Any,
    *,
    source: OssSource,
    cache_dir: Path,
) -> Path:
    objects = _list_source_objects(bucket, source.prefix)
    root_prefix = _dataset_root(objects)
    selected: list[tuple[SourceObject, str]] = []
    for item in objects:
        if not item.key.startswith(root_prefix):
            continue
        relative = item.key[len(root_prefix) :]
        if is_canonical_lerobot_object(relative):
            selected.append((item, relative))
    if not any(relative == "meta/info.json" for _, relative in selected):
        raise ValueError("the OSS prefix has no canonical LeRobot metadata")

    identity = re.sub(r"[^A-Za-z0-9._-]+", "-", root_prefix.strip("/"))[-160:]
    local_root = (cache_dir / identity).resolve()
    cache_root = cache_dir.resolve()
    try:
        local_root.relative_to(cache_root)
    except ValueError as exc:
        raise RuntimeError(
            "resolved OSS source cache escaped the selected cache directory"
        ) from exc
    for index, (item, relative) in enumerate(selected, start=1):
        print(f"sync OSS source [{index}/{len(selected)}] {relative}", file=sys.stderr)
        _download_object(bucket, item, local_root / relative)
    validate_source_profile(local_root)
    return local_root


def find_local_source_root(value: Path) -> Path:
    raw_value = str(value)
    windows_match = re.match(r"^([A-Za-z]):[\\/](.*)$", raw_value)
    if windows_match:
        candidate = (
            Path("/mnt")
            / windows_match.group(1).lower()
            / windows_match.group(2).replace("\\", "/")
        ).resolve()
    else:
        candidate = value.expanduser().resolve()
    if (candidate / "meta/info.json").is_file():
        validate_source_profile(candidate)
        return candidate
    matches = sorted(candidate.glob("*/meta/info.json"))
    if len(matches) != 1:
        raise ValueError(
            "local source must be a LeRobot revision root, or contain exactly one revision root"
        )
    root = matches[0].parent.parent
    validate_source_profile(root)
    return root


def validate_source_profile(source_root: Path) -> dict[str, Any]:
    try:
        info = json.loads((source_root / "meta/info.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("LeRobot meta/info.json is missing or invalid") from exc
    if not isinstance(info, dict):
        raise ValueError("LeRobot meta/info.json must contain an object")
    if info.get("codebase_version") != "v3.0":
        raise ValueError("this importer currently requires LeRobotDataset v3.0")
    if info.get("robot_type") != "unitree_g1":
        raise ValueError("this importer currently requires the Unitree G1 source profile")
    features = info.get("features")
    if not isinstance(features, dict):
        raise ValueError("LeRobot metadata has no feature inventory")
    required = {
        "observation.state.ee_state",
        "observation.state.hand_state",
        "observation.state.robot_q_current",
        "action.ee_action",
        "action.hand_cmd",
        "action.robot_q_desired",
        *(camera.feature_key for camera in converter.CAMERAS),
    }
    missing = sorted(required - set(features))
    if missing:
        raise ValueError(f"LeRobot Unitree G1 profile is missing features: {', '.join(missing)}")
    return info


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


def _oss_bucket(source: OssSource) -> Any:
    configured_bucket = _required_env("HC_OBJECT_STORE_BUCKET")
    if configured_bucket != source.bucket:
        raise ValueError("OSS URI bucket must equal HC_OBJECT_STORE_BUCKET")
    return build_oss_bucket(
        endpoint=_required_env("HC_OBJECT_STORE_ENDPOINT"),
        bucket=configured_bucket,
        access_key=_required_env("HC_OBJECT_STORE_ACCESS_KEY"),
        secret_key=_required_env("HC_OBJECT_STORE_SECRET_KEY"),
        connect_timeout=30,
    )


def _converter_arguments(args: argparse.Namespace, source_root: Path) -> argparse.Namespace:
    values = [
        "--project-id",
        cast(str, args.project_id),
        "--collection-task-id",
        cast(str, args.collection_task_id),
        "--robot-id",
        cast(str, args.robot_id),
        "--repository",
        cast(str, args.repository),
        "--revision",
        cast(str, args.revision),
        "--source-dir",
        str(source_root),
        "--cache-dir",
        str(cast(Path, args.cache_dir)),
        "--output-dir",
        str(cast(Path, args.output_dir)),
    ]
    if cast(bool, args.all_episodes):
        values.extend(["--all-episodes", "--episode-count", str(args.episode_count)])
    else:
        values.extend(["--episode", str(args.episode)])
    if args.capture_start is not None:
        values.extend(["--capture-start", cast(str, args.capture_start)])
    return converter.build_parser().parse_args(values)


def _upload_packages(args: argparse.Namespace, packages: tuple[Path, ...]) -> list[dict[str, Any]]:
    if cast(bool, args.convert_only):
        return []
    api_base_url = cast(str | None, args.api_base_url)
    if not api_base_url:
        raise ValueError("--api-base-url is required unless --convert-only is used")
    if not args.region_code:
        raise ValueError("--region-code (the platform region, not the OSS region) is required")
    token = _required_env(cast(str, args.access_token_env))
    results: list[dict[str, Any]] = []
    for package in packages:
        manifest_path = package / converter.MANIFEST_FILE_NAME
        mcap_path = package / converter.RAW_FILE_NAME
        manifest = parse_manifest_bytes(manifest_path.read_bytes()).manifest
        print(f"upload platform package {manifest.data_package_id}", file=sys.stderr)
        result = import_offline_bundle(
            mcap_path,
            manifest_path,
            api_base_url=api_base_url,
            region_code=cast(str, args.region_code),
            access_token=token,
            idempotency_key=f"lerobot-import-{manifest.data_package_id}",
            part_size=int(args.part_size_mib) * 1024 * 1024,
            max_part_retries=int(args.max_part_retries),
        )
        results.append(
            {
                "data_package_id": manifest.data_package_id,
                "rollout_id": manifest.rollout_id,
                "package": str(package),
                "platform": result,
            }
        )
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source-dir", type=Path)
    source.add_argument("--oss-uri")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--collection-task-id", required=True)
    parser.add_argument("--robot-id", required=True)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--episode", type=int, default=0)
    selection.add_argument("--all-episodes", action="store_true")
    parser.add_argument("--episode-count", type=int, default=10)
    parser.add_argument("--repository", default=converter.DEFAULT_REPOSITORY)
    parser.add_argument("--revision", default=converter.DEFAULT_REVISION)
    parser.add_argument("--capture-start")
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/lerobot-import"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/lerobot-unitree-g1-import"),
    )
    parser.add_argument("--convert-only", action="store_true")
    parser.add_argument("--api-base-url")
    parser.add_argument(
        "--region-code",
        default=os.environ.get("HC_PLATFORM_REGION_CODE"),
        help="platform project region code; this is not the Alibaba OSS bucket region",
    )
    parser.add_argument("--access-token-env", default="HC_DATA_ACCESS_TOKEN")
    parser.add_argument("--part-size-mib", type=int, default=DEFAULT_PART_SIZE // 1024**2)
    parser.add_argument("--max-part-retries", type=int, default=5)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    _load_env_file(cast(Path | None, args.env_file))
    if args.episode < 0:
        raise ValueError("--episode must be non-negative")
    if not 1 <= args.episode_count <= 100:
        raise ValueError("--episode-count must be between 1 and 100")
    if args.part_size_mib < 5:
        raise ValueError("--part-size-mib must be at least 5")

    cache_dir = cast(Path, args.cache_dir).expanduser().resolve()
    if args.source_dir is not None:
        source_root = find_local_source_root(cast(Path, args.source_dir))
        source_kind = "local"
    else:
        oss_source = parse_oss_source_uri(cast(str, args.oss_uri))
        source_root = materialize_oss_source(
            _oss_bucket(oss_source),
            source=oss_source,
            cache_dir=cache_dir / "oss-sources",
        )
        source_kind = "oss"

    packages = converter.convert(_converter_arguments(args, source_root))
    platform = _upload_packages(args, packages)
    return {
        "source_kind": source_kind,
        "source_root": str(source_root),
        "packages": [str(package) for package in packages],
        "uploaded": platform,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
