from types import SimpleNamespace

from hc_data_platform.storage.location_usage import scan_local, storage_locations


def test_cloud_purposes_total_and_retention_are_independent_of_local(tmp_path):
    objects = [
        SimpleNamespace(object_key=f"{key}/sample", physical_bytes=i * 100)
        for i, key in enumerate(
            (
                "raw",
                "episodes",
                "collections",
                "derived-mcap",
                "recording-previews",
                "episode-previews",
                "episode-deliveries",
                "oss-smoke",
            ),
            1,
        )
    ]
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "private-name.bin").write_bytes(b"payload")
    local, cloud = storage_locations(
        objects=objects,
        provider="oss",
        object_scope="oss://bucket/prefix/",
        local_roots={"alignment": str(cache)},
        database_bytes=1000,
    )
    assert cloud.total_bytes == "3600"
    assert len(cloud.categories) == 8
    assert sum(int(c.bytes) for c in cloud.categories) == int(cloud.total_bytes)
    assert {c.category for c in cloud.categories if c.retention == "KEEP"} == {
        "raw",
        "episodes",
        "collections",
        "derived-evidence",
    }
    assert int(local.total_bytes) == sum(scan_local(cache).values()) + 1000
    assert "private-name" not in local.model_dump_json()
    assert local.complete and cloud.complete


def test_minio_layout_and_hardlinks_are_counted_once_without_symlink_targets(tmp_path):
    root = tmp_path / "minio"
    raw = root / "bucket" / "raw" / "source.mcap"
    raw.mkdir(parents=True)
    data = raw / "part.1"
    data.write_bytes(b"original")
    (raw / "hardlink").hardlink_to(data)
    external = tmp_path / "external"
    external.mkdir()
    (external / "large").write_bytes(b"x" * 100000)
    (raw / "shortcut").symlink_to(external, target_is_directory=True)
    totals = scan_local(root, minio=True)
    assert (
        totals["raw"]
        == (
            raw.parent.stat().st_blocks
            + raw.stat().st_blocks
            + data.stat().st_blocks
            + (raw / "shortcut").lstat().st_blocks
        )
        * 512
    )
    assert set(totals) == {"raw", "other"}


def test_unavailable_storage_is_unknown_not_empty(tmp_path):
    local, cloud = storage_locations(
        objects=None,
        provider="oss",
        object_scope="oss://bucket/prefix/",
        local_roots={"minio": str(tmp_path / "missing")},
        database_bytes=42,
    )
    assert not local.complete and not cloud.complete
    assert local.categories[0].bytes is None
    assert local.total_bytes == "42"
    assert cloud.categories == ()


def test_local_version_does_not_label_minio_objects_as_cloud(tmp_path):
    result = storage_locations(
        objects=[],
        provider="s3",
        object_scope="",
        local_roots={"alignment": str(tmp_path)},
        database_bytes=10,
    )
    assert [location.location for location in result] == ["LOCAL"]


def test_saved_oss_capacity_uses_its_prefix_without_switching_local_storage(monkeypatch):
    from hc_data_platform.core.config import Settings
    from hc_data_platform.platform_ops.object_store_config import (
        PostgresObjectStoreConfigurationRepository,
    )
    from hc_data_platform.storage import inventory
    from hc_data_platform.storage.location_usage import saved_oss_location

    settings = Settings(
        storage_oss_usage_prefix="capacity-only", object_store_provider="s3", object_store_prefix=""
    )
    stored = SimpleNamespace(
        bucket="cloud-bucket",
        settings_updates=lambda: {
            "object_store_provider": "oss",
            "object_store_bucket": "cloud-bucket",
        },
    )
    monkeypatch.setattr(
        PostgresObjectStoreConfigurationRepository,
        "from_dsn",
        lambda *a, **k: SimpleNamespace(current=lambda _: stored),
    )
    monkeypatch.setattr(
        inventory,
        "S3StorageInventoryProvider",
        lambda *a: SimpleNamespace(
            list_prefix=lambda _: [SimpleNamespace(object_key="raw/source", physical_bytes=42)]
        ),
    )
    observed = []

    def clients(config):
        observed.append(config)
        return None, None, None

    result = saved_oss_location(settings, clients)
    assert result[0].total_bytes == "42"
    assert result[0].scope == "oss://cloud-bucket/capacity-only/"
    assert observed[0].object_store_prefix == "capacity-only"
    assert observed[0].object_store_provider == "oss"
    assert settings.object_store_provider == "s3"
    assert settings.object_store_prefix == ""

    def unavailable(config):
        raise RuntimeError("private connection detail")

    unknown = saved_oss_location(settings, unavailable)[0]
    assert not unknown.complete
    assert "private connection detail" not in unknown.model_dump_json()
