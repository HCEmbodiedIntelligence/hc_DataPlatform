from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import yaml

IMAGE_PATTERN = re.compile(r"^(?P<repository>[^@\s]+)@(?P<digest>sha256:[0-9a-f]{64})$")


def image(value: str) -> tuple[str, str]:
    match = IMAGE_PATTERN.fullmatch(value)
    if match is None:
        raise argparse.ArgumentTypeError("image must use repository@sha256:<64 lowercase hex>")
    return match.group("repository"), match.group("digest")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Render one immutable platform release manifest")
    result.add_argument("--release-id", required=True)
    result.add_argument("--git-commit", required=True)
    result.add_argument("--frontend", required=True, type=image)
    result.add_argument("--api", required=True, type=image)
    result.add_argument("--worker", required=True, type=image)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--values-output", type=Path, required=True)
    return result


def main() -> None:
    args = parser().parse_args()
    if re.fullmatch(r"platform-v[A-Za-z0-9._-]+", args.release_id) is None:
        raise SystemExit("release ID must start with platform-v")
    if re.fullmatch(r"[0-9a-f]{40}", args.git_commit) is None:
        raise SystemExit("git commit must be a full lowercase SHA-1")

    frontend_repository, frontend_digest = args.frontend
    api_repository, api_digest = args.api
    worker_repository, worker_digest = args.worker
    repository_root = Path(__file__).resolve().parents[2]
    chart = yaml.safe_load(
        (repository_root / "deploy/helm/hc-data-platform/Chart.yaml").read_text(encoding="utf-8")
    )
    migration_manifest = repository_root / "backend/migrations/manifest.txt"
    migration_manifest_digest = (
        f"sha256:{hashlib.sha256(migration_manifest.read_bytes()).hexdigest()}"
    )
    manifest = {
        "apiVersion": "hc-data-platform.io/v1alpha1",
        "kind": "PlatformRelease",
        "metadata": {"releaseId": args.release_id},
        "spec": {
            "gitCommit": args.git_commit,
            "semanticVersion": chart["appVersion"],
            "chart": {"name": "hc-data-platform", "version": chart["version"]},
            "images": {
                "frontend": f"{frontend_repository}@{frontend_digest}",
                "api": f"{api_repository}@{api_digest}",
                "worker": f"{worker_repository}@{worker_digest}",
            },
            "database": {
                "migrationManifest": "backend/migrations/manifest.txt",
                "migrationManifestDigest": migration_manifest_digest,
                "strategy": "forward-only-expand-contract",
            },
            "promotion": {"buildOnce": True, "environments": ["staging", "production"]},
        },
    }
    rendered_manifest = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    release_manifest_digest = f"sha256:{hashlib.sha256(rendered_manifest.encode()).hexdigest()}"
    values = {
        "global": {
            "releaseId": args.release_id,
            "releaseManifestDigest": release_manifest_digest,
            "gitCommit": args.git_commit,
            "migrationManifestDigest": migration_manifest_digest,
        },
        "frontend": {"image": {"repository": frontend_repository, "digest": frontend_digest}},
        "backend": {
            "api": {"image": {"repository": api_repository, "digest": api_digest}},
            "worker": {"image": {"repository": worker_repository, "digest": worker_digest}},
            "mediaWorker": {"image": {"repository": worker_repository, "digest": worker_digest}},
        },
    }
    args.output.write_text(rendered_manifest, encoding="utf-8")
    args.values_output.write_text(
        json.dumps(values, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
