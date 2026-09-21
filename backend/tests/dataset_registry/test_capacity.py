from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hc_data_platform.aligned_media.models import AlignedMediaObjectV1
from hc_data_platform.dataset_registry.capacity import (
    CapacityReferences,
    StoredDatasetCapacityReader,
)
from hc_data_platform.dataset_registry.models import (
    DatasetPageScope,
    DatasetPageVersionCapacityFacts,
)
from hc_data_platform.storage.inventory import ProviderInventoryObject

NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)
URI = "s3://bucket/datasets/project/dataset/"
PREFIX = "datasets/project/dataset/"


def facts(**updates: object) -> DatasetPageVersionCapacityFacts:
    return DatasetPageVersionCapacityFacts(
        scope=DatasetPageScope(organization_id="org", project_id="project", region_code="region"),
        dataset_id="dataset_capacity",
        version_id="version_capacity",
        state="PARTIAL",
        source_bytes="8000",
        calculated_at=NOW,
        basis_revision="lance:fixed-commit",
    ).model_copy(update=updates)


def object_fact(key: str, size: int) -> ProviderInventoryObject:
    return ProviderInventoryObject(object_key=key, physical_bytes=size, observed_at=NOW)


def reader_fixture():
    shared_video = AlignedMediaObjectV1(
        key="raw/shared-video.mp4",
        size=1000,
        sha256="a" * 64,
        media_type="video/mp4",
    )
    references = Mock()
    references.load.return_value = CapacityReferences(
        ((URI, 1), (URI, 2)),
        (shared_video, shared_video),
    )
    provider = Mock()
    provider.list_prefix.return_value = tuple(
        object_fact(PREFIX + path, size)
        for path, size in (
            ("data/shared.lance", 100),
            ("data/second.lance", 200),
            ("_versions/1.manifest", 5),
            ("_versions/2.manifest", 6),
            ("data/newer.lance", 900),
            ("temporary.bin", 10000),
        )
    )
    provider.head.return_value = object_fact("raw/shared-video.mp4", 1000)
    tracked = [
        {"version": version, "base_uri": URI, "path": path}
        for version, path in (
            (1, "data/shared.lance"),
            (1, "_versions/1.manifest"),
            (2, "data/shared.lance"),
            (2, "data/second.lance"),
            (2, "_versions/2.manifest"),
            (3, "data/newer.lance"),
        )
    ]
    dataset = Mock()
    dataset.tracked_files.side_effect = lambda **_: [SimpleNamespace(to_pylist=lambda: tracked)]
    loader = Mock(return_value=dataset)
    reader = StoredDatasetCapacityReader(
        references,
        provider,
        bucket="bucket",
        storage_options={},
        dataset_loader=loader,
    )
    return reader, references, provider, loader


def test_unique_version_objects_are_measured_without_counting_future_or_temporary_files():
    reader, _, provider, loader = reader_fixture()
    measured = reader.resolve(facts())
    assert measured.state == "SETTLED"
    assert measured.source_bytes == "8000"
    assert measured.required_physical_bytes == "1311"
    assert measured.actual_oss_bytes == "1311"
    loader.assert_called_once_with(URI, version=2, storage_options={})
    provider.head.assert_called_once_with("raw/shared-video.mp4")


def test_old_version_capacity_does_not_include_new_version_files():
    reader, references, _, _ = reader_fixture()
    original = references.load.return_value
    references.load.return_value = CapacityReferences(((URI, 1),), original.media_objects)
    measured = reader.resolve(facts(version_id="version_old"))
    assert measured.actual_oss_bytes == "1105"


def test_measured_capacity_is_cached_but_isolated_by_version_and_tenant():
    reader, references, _, _ = reader_fixture()
    first = reader.resolve(facts())
    assert reader.resolve(facts()) == first
    assert references.load.call_count == 1
    reader.resolve(facts(version_id="version_other"))
    reader.resolve(facts(scope=facts().scope.model_copy(update={"organization_id": "other-org"})))
    assert references.load.call_count == 3


@pytest.mark.parametrize("failure", ["missing", "wrong-size", "provider-error", "wrong-bucket"])
def test_missing_or_unverifiable_objects_do_not_become_zero_or_settled(failure):
    reader, references, provider, _ = reader_fixture()
    if failure == "missing":
        provider.head.return_value = None
    elif failure == "wrong-size":
        provider.head.return_value = object_fact("raw/shared-video.mp4", 999)
    elif failure == "provider-error":
        provider.head.side_effect = OSError("Unavailable")
    else:
        references.load.return_value = CapacityReferences((("s3://other/dataset/", 1),), ())
    measured = reader.resolve(facts())
    assert measured.state == "PARTIAL"
    assert measured.actual_oss_bytes is None
    assert measured.required_physical_bytes is None


def test_conflicting_shared_receipts_fail_closed():
    reader, references, _, _ = reader_fixture()
    original = references.load.return_value
    receipt = original.media_objects[0]
    references.load.return_value = CapacityReferences(
        original.lance_versions,
        (receipt, receipt.model_copy(update={"size": 99})),
    )
    assert reader.resolve(facts()).actual_oss_bytes is None


def test_settled_record_does_not_need_a_second_inventory():
    reader, references, _, _ = reader_fixture()
    measured = facts(state="SETTLED", actual_oss_bytes="123", required_physical_bytes="123")
    assert reader.resolve(measured) is measured
    references.load.assert_not_called()
