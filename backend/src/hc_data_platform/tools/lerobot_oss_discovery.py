"""Watch an OSS inbox and automatically import stable Unitree G1 LeRobot roots.

The robot or a smoke-test uploader can copy an untouched LeRobot directory into
the configured prefix.  Once its canonical objects stop changing, this worker
uses the normal upload-session API so all existing verification, visualization,
dataset, and annotation workflows are triggered.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from hc_data_platform.storage.oss_client import build_oss_bucket

from . import hf_unitree_g1_to_mcap as converter
from . import lerobot_unitree_g1_import as importer

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DiscoveredRoot:
    prefix: str
    signature: str
    object_count: int


@dataclass(frozen=True, slots=True)
class DiscoveryConfig:
    bucket_name: str
    source_prefix: str
    project_id: str
    collection_task_id: str
    robot_id: str
    episode_count: int
    api_base_url: str
    region_code: str
    access_token_env: str
    cache_dir: Path
    output_dir: Path
    stability_seconds: float


@dataclass(slots=True)
class _Observation:
    signature: str
    first_seen_at: float


def _list_objects(bucket: Any, prefix: str) -> tuple[tuple[str, int], ...]:
    continuation = ""
    objects: list[tuple[str, int]] = []
    while True:
        result = bucket.list_objects_v2(
            prefix=f"{prefix.strip('/')}/",
            continuation_token=continuation,
            max_keys=1000,
        )
        objects.extend((str(item.key), int(item.size)) for item in result.object_list)
        if not result.is_truncated:
            return tuple(objects)
        continuation = str(result.next_continuation_token or "")
        if not continuation:
            raise RuntimeError("OSS returned a truncated listing without a continuation token")


def discover_lerobot_roots(bucket: Any, source_prefix: str) -> tuple[DiscoveredRoot, ...]:
    objects = _list_objects(bucket, source_prefix)
    marker = "meta/info.json"
    roots = sorted(
        {key[: -len(marker)].rstrip("/") for key, _size in objects if key.endswith(marker)}
    )
    discovered: list[DiscoveredRoot] = []
    for root in roots:
        root_with_separator = f"{root}/"
        canonical = sorted(
            (key, size)
            for key, size in objects
            if key.startswith(root_with_separator)
            and importer.is_canonical_lerobot_object(key[len(root_with_separator) :])
        )
        relatives = {key[len(root_with_separator) :] for key, _size in canonical}
        camera_features = {
            relative.split("/", 2)[1]
            for relative in relatives
            if relative.startswith("videos/") and relative.endswith(".mp4")
        }
        if (
            "meta/info.json" not in relatives
            or not any(relative.startswith("meta/episodes/") for relative in relatives)
            or not any(relative.startswith("data/") for relative in relatives)
            or camera_features != {camera.feature_key for camera in converter.CAMERAS}
        ):
            continue
        signature_payload = "\n".join(f"{key}\0{size}" for key, size in canonical)
        discovered.append(
            DiscoveredRoot(
                prefix=root,
                signature=hashlib.sha256(signature_payload.encode()).hexdigest(),
                object_count=len(canonical),
            )
        )
    return tuple(discovered)


class LeRobotOssDiscovery:
    def __init__(
        self,
        bucket: Any,
        config: DiscoveryConfig,
        *,
        import_root: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        if not 1 <= config.episode_count <= 100:
            raise ValueError("episode_count must be between 1 and 100")
        if config.stability_seconds < 0:
            raise ValueError("stability_seconds must not be negative")
        self._bucket = bucket
        self._config = config
        self._observations: dict[str, _Observation] = {}
        self._completed: set[tuple[str, str]] = set()
        self._import_root = import_root or self._run_import

    def run_cycle(self, *, now: float | None = None) -> tuple[dict[str, Any], ...]:
        observed_at = time.monotonic() if now is None else now
        roots = discover_lerobot_roots(self._bucket, self._config.source_prefix)
        current_prefixes = {root.prefix for root in roots}
        self._observations = {
            prefix: observation
            for prefix, observation in self._observations.items()
            if prefix in current_prefixes
        }
        imported: list[dict[str, Any]] = []
        for root in roots:
            identity = (root.prefix, root.signature)
            if identity in self._completed:
                continue
            observation = self._observations.get(root.prefix)
            if observation is None or observation.signature != root.signature:
                observation = _Observation(root.signature, observed_at)
                self._observations[root.prefix] = observation
            if observed_at - observation.first_seen_at < self._config.stability_seconds:
                continue
            result = self._import_root(root.prefix)
            self._completed.add(identity)
            imported.append(result)
        return tuple(imported)

    def _run_import(self, root_prefix: str) -> dict[str, Any]:
        values = [
            "--oss-uri",
            f"oss://{self._config.bucket_name}/{root_prefix}",
            "--project-id",
            self._config.project_id,
            "--collection-task-id",
            self._config.collection_task_id,
            "--robot-id",
            self._config.robot_id,
            "--all-episodes",
            "--episode-count",
            str(self._config.episode_count),
            "--cache-dir",
            str(self._config.cache_dir),
            "--output-dir",
            str(self._config.output_dir),
            "--api-base-url",
            self._config.api_base_url,
            "--region-code",
            self._config.region_code,
            "--access-token-env",
            self._config.access_token_env,
            "--env-file",
            "",
        ]
        args = importer.build_parser().parse_args(values)
        return importer.run(args)


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or not value.strip():
        raise ValueError(f"environment variable {name} is required")
    return value.strip()


def _build_bucket() -> Any:
    return build_oss_bucket(
        endpoint=_env("HC_OBJECT_STORE_ENDPOINT"),
        bucket=_env("HC_OBJECT_STORE_BUCKET"),
        access_key=_env("HC_OBJECT_STORE_ACCESS_KEY"),
        secret_key=_env("HC_OBJECT_STORE_SECRET_KEY"),
        connect_timeout=30,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-prefix", default="lerobot-inbox")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--collection-task-id", required=True)
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--episode-count", type=int, default=10)
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--region-code",
        required=True,
        help="platform project region code; this is not the Alibaba OSS bucket region",
    )
    parser.add_argument("--access-token-env", default="HC_DATA_ACCESS_TOKEN")
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/lerobot-discovery"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/lerobot-discovery"))
    parser.add_argument("--interval-seconds", type=float, default=30)
    parser.add_argument("--stability-seconds", type=float, default=60)
    parser.add_argument("--once", action="store_true")
    return parser


def run(args: argparse.Namespace) -> None:
    if args.interval_seconds <= 0:
        raise ValueError("--interval-seconds must be positive")
    config = DiscoveryConfig(
        bucket_name=_env("HC_OBJECT_STORE_BUCKET"),
        source_prefix=cast(str, args.source_prefix).strip("/"),
        project_id=cast(str, args.project_id),
        collection_task_id=cast(str, args.collection_task_id),
        robot_id=cast(str, args.robot_id),
        episode_count=int(args.episode_count),
        api_base_url=cast(str, args.api_base_url),
        region_code=cast(str, args.region_code),
        access_token_env=cast(str, args.access_token_env),
        cache_dir=cast(Path, args.cache_dir).expanduser().resolve(),
        output_dir=cast(Path, args.output_dir).expanduser().resolve(),
        stability_seconds=float(args.stability_seconds),
    )
    discovery = LeRobotOssDiscovery(_build_bucket(), config)
    if cast(bool, args.once):
        # A one-shot administrative run explicitly asserts that upload is complete.
        discovery._config = replace(  # noqa: SLF001 - intentional CLI override
            config, stability_seconds=0
        )
        results = discovery.run_cycle()
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return
    while True:
        try:
            results = discovery.run_cycle()
            for result in results:
                print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception:
            logger.exception("LeRobot OSS discovery cycle failed")
        time.sleep(float(args.interval_seconds))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        run(args)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
