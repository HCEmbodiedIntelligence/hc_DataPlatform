from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from hc_data_platform.tools.hf_unitree_g1_to_mcap import CAMERAS
from hc_data_platform.tools.lerobot_oss_discovery import (
    DiscoveryConfig,
    LeRobotOssDiscovery,
    discover_lerobot_roots,
)


class _Bucket:
    def __init__(self, objects: dict[str, int]) -> None:
        self.objects = objects

    def list_objects_v2(self, *, prefix: str, continuation_token: str, max_keys: int):
        assert continuation_token == ""
        assert max_keys == 1000
        return SimpleNamespace(
            object_list=[
                SimpleNamespace(key=key, size=size)
                for key, size in sorted(self.objects.items())
                if key.startswith(prefix)
            ],
            is_truncated=False,
            next_continuation_token="",
        )


def _objects(root: str) -> dict[str, int]:
    result = {
        f"{root}/meta/info.json": 100,
        f"{root}/meta/episodes/chunk-000/file-000.parquet": 200,
        f"{root}/data/chunk-000/file-000.parquet": 300,
        f"{root}/.cache/download.lock": 0,
    }
    result.update(
        {
            f"{root}/videos/{camera.feature_key}/chunk-000/file-000.mp4": 400 + index
            for index, camera in enumerate(CAMERAS)
        }
    )
    return result


def _config(tmp_path: Path, *, stability_seconds: float = 60) -> DiscoveryConfig:
    return DiscoveryConfig(
        bucket_name="bucket-a",
        source_prefix="lerobot-inbox",
        project_id="project-a",
        collection_task_id="task-a",
        robot_id="robot-a",
        episode_count=10,
        api_base_url="http://api:8000",
        region_code="cn-beijing",
        access_token_env="HC_DATA_ACCESS_TOKEN",
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "output",
        stability_seconds=stability_seconds,
    )


def test_discovers_only_complete_canonical_lerobot_roots() -> None:
    complete = _objects("lerobot-inbox/repository/revision")
    incomplete = _objects("lerobot-inbox/incomplete/revision")
    del incomplete[
        "lerobot-inbox/incomplete/revision/"
        "videos/observation.images.wrist_right/chunk-000/file-000.mp4"
    ]

    roots = discover_lerobot_roots(_Bucket({**complete, **incomplete}), "lerobot-inbox")

    assert [root.prefix for root in roots] == ["lerobot-inbox/repository/revision"]
    assert roots[0].object_count == 7


def test_waits_for_stability_then_imports_each_signature_once(tmp_path: Path) -> None:
    bucket = _Bucket(_objects("lerobot-inbox/repository/revision"))
    imported: list[str] = []
    discovery = LeRobotOssDiscovery(
        bucket,
        _config(tmp_path),
        import_root=lambda prefix: imported.append(prefix) or {"prefix": prefix},
    )

    assert discovery.run_cycle(now=0) == ()
    assert discovery.run_cycle(now=59) == ()
    assert discovery.run_cycle(now=60) == ({"prefix": "lerobot-inbox/repository/revision"},)
    assert discovery.run_cycle(now=120) == ()
    assert imported == ["lerobot-inbox/repository/revision"]

    bucket.objects["lerobot-inbox/repository/revision/data/chunk-000/file-000.parquet"] += 1
    assert discovery.run_cycle(now=121) == ()
    assert discovery.run_cycle(now=181) == ({"prefix": "lerobot-inbox/repository/revision"},)
