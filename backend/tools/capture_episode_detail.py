"""Exercise normal isolated HTTP sessions against previously committed ingress fixtures."""

import argparse
import json
import os
from pathlib import Path

import httpx

from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.episode_store.models import PackageManifest
from hc_data_platform.episode_store.postgres import PostgresEpisodeStore
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_http_services import register_and_grant


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entrances", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repository = PostgresEpisodeStore(
        psycopg_connection_factory(os.environ["HC_G1_TEST_POSTGRES_DSN"])
    )
    results = []
    for item in json.loads(args.entrances.read_text())["entrances"]:
        root = Path(item["package"])
        manifest_path = root / "hc/manifest.json"
        if not manifest_path.exists():
            manifest_path = root.parent / "manifest.json"
        manifest = PackageManifest.model_validate_json(manifest_path.read_bytes())
        source = manifest.input
        ident = source.identity
        with (
            scoped(source),
            httpx.Client(base_url="http://gateway:8080", timeout=90, trust_env=False) as client,
        ):
            _, headers = register_and_grant(
                client,
                repository,
                ident,
                capabilities=["dataset_version.read", "annotation_task.read"],
            )
            try:
                path = (
                    f"/api/v1/projects/{ident.project_id}/regions/{ident.region_code}/datasets/"
                    f"{ident.dataset_id}/episodes/{ident.episode_id}/data-versions/{source.data_version_id}/overview"
                )
                response = client.get(path, headers=headers)
                response.raise_for_status()
                view = response.json()
                assert view["data"]["content_hash"] == manifest.content_hash
                assert view["data"]["identity"] == ident.model_dump(mode="json")
                assert view["data"]["data_version_id"] == item["data_version_id"]
                depths = [camera for camera in view["cameras"] if camera["kind"] == "DEPTH"]
                assert len(depths) == len(source.required_depth_features)
                for camera in depths:
                    assert camera["depth"]["target_frame_count"] == manifest.frame_count
                    assert camera["depth"]["profile"]["pixel_format"] == "gray12le"
                    assert camera["depth"]["video_encoding"]["lossless_codes"]
                results.append(
                    {
                        "ingress": item["ingress"],
                        "data": view["data"],
                        "source": {k: v for k, v in view["source"].items() if k != "document"},
                        "duration_seconds": view["duration_seconds"],
                        "camera_stream_count": view["camera_stream_count"],
                        "depth_cameras": depths,
                        "quality": view["quality"]["status"],
                        "annotation": view["annotation"],
                        "data_review_status": view["data_review_status"],
                        "subtasks": view["subtasks"],
                        "file_count": view["files"]["total"],
                        "task": view["task"],
                    }
                )
            finally:
                assert (
                    client.post("/api/v1/auth/session:logout", headers=headers).status_code == 204
                )
    args.output.write_text(
        json.dumps(
            {
                "scope": "normal isolated HTTP sessions over synthetic ingress outputs",
                "real_recording": False,
                "human_acceptance": False,
                "entrances": results,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
