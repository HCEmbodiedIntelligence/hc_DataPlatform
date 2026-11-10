from collections import defaultdict
from types import SimpleNamespace

import pytest

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.storage import task_usage
from hc_data_platform.storage.inventory import InventoryCatalogEntry, ProviderInventoryObject
from hc_data_platform.storage.models import BusinessCapacityCategory, ObjectRole
from hc_data_platform.storage.repository import InMemoryStorageRepository
from hc_data_platform.storage.service import StorageGovernanceService
from hc_data_platform.storage.task_inventory import FileOwner

from .test_inventory_producer import OBSERVED_AT, reader


@pytest.fixture(autouse=True)
def request_scope():
    token = bind_request_context(
        RequestContext(
            organization_id="organization-a",
            project_id="project-inventory",
            region_code="global",
        )
    )
    try:
        yield
    finally:
        reset_request_context(token)


@pytest.fixture
def usage(monkeypatch, tmp_path):
    labels = SimpleNamespace(
        tasks={
            "a": {"name": "抓取水瓶", "task_code": "00000001"},
            "b": {"name": "搬运箱子", "task_code": "00000002"},
        },
        owners={
            "raw": FileOwner(("a",), "RAW", "原始录制", "X1", "原始文件"),
            "reused-preview": FileOwner(("a",), "PROCESSING", "原始录制", "X1"),
            "episode": FileOwner(("a",), "EPISODE", "成片 001", "X1", "已标注"),
            "annotation": FileOwner(("a",), "ANNOTATION", "成片 001", "X1"),
            "export": FileOwner(("a", "b"), "EXPORT", "联合导出", "X1"),
        },
        extra_entries=[],
        raw_counts={"a": 1},
        episodes={"a": {"episode"}},
        completed={"a": {"episode"}},
        database_bytes=12345,
    )
    monkeypatch.setattr(task_usage, "load_task_inventory", lambda *_: labels)

    def entry(identity, key, priority):
        return InventoryCatalogEntry(
            identity,
            key,
            BusinessCapacityCategory.RAW,
            ObjectRole.RAW,
            OBSERVED_AT,
            priority=priority,
        )

    entries = [
        entry("raw", "raw/bottle.mcap", 65),
        entry("reused-preview", "raw/bottle.mcap", 60),
        entry("episode", "episodes/video.mp4", 75),
        entry("annotation", "annotation/labels.json", 80),
        entry("export", "exports/two-tasks.zip", 80),
    ]
    calls = defaultdict(int)
    objects = {
        key: ProviderInventoryObject(key, size, OBSERVED_AT)
        for key, size in (
            ("raw/bottle.mcap", 100),
            ("episodes/video.mp4", 50),
            ("annotation/labels.json", 2),
            ("exports/two-tasks.zip", 80),
        )
    }

    def head(key):
        calls[key] += 1
        return objects.get(key)

    target = task_usage.TaskStorageUsageReader(
        None,
        SimpleNamespace(entries=lambda **_: entries),
        SimpleNamespace(head=head),
        disk_path=str(tmp_path),
    )
    return target, entries, calls


def test_actual_file_categories_shared_export_and_annotation_progress(usage):
    target, _, _ = usage
    result = target.overview("project-inventory")
    assert result.total_bytes == "232"
    assert sum(int(t.total_bytes) for t in result.tasks) == 232
    assert {c.category: c.bytes for c in result.categories} == {
        "RAW": "100",
        "EPISODE": "50",
        "PROCESSING": "0",
        "ANNOTATION": "2",
        "EXPORT": "80",
        "OTHER": "0",
    }
    task = next(t for t in result.tasks if t.group_id == "task:a")
    assert task.total_bytes == "152"
    assert task.shared_bytes == "80"
    assert task.episode_count == task.annotation_complete_count == 1
    assert result.disk is not None
    assert int(result.disk.total_bytes) == sum(
        map(
            int,
            (
                result.disk.used_bytes,
                result.disk.available_bytes,
                result.disk.reserved_bytes,
            ),
        )
    )
    assert result.database_bytes == "12345"
    files = target.files("project-inventory", group_id="task:a", category="ANNOTATION")
    assert files.total_bytes == "2"
    assert [f.name for f in files.items] == ["labels.json"]
    shared = target.files("project-inventory", group_id="task:b", search="two-tasks")
    assert shared.items[0].shared
    assert shared.items[0].related_task_names == ("抓取水瓶", "搬运箱子")


def test_missing_files_are_flagged_and_cache_is_scoped_and_refreshable(usage):
    target, entries, calls = usage
    first = target.overview("project-inventory")
    before = sum(calls.values())
    assert target.overview("project-inventory") == first
    assert sum(calls.values()) == before
    token = bind_request_context(
        RequestContext(
            organization_id="another-org",
            project_id="project-inventory",
            region_code="global",
        )
    )
    try:
        target.overview("project-inventory")
    finally:
        reset_request_context(token)
    assert sum(calls.values()) > before
    entries.append(
        InventoryCatalogEntry(
            "missing",
            "raw/missing.mcap",
            BusinessCapacityCategory.RAW,
            ObjectRole.RAW,
            OBSERVED_AT,
            expected_bytes=999999,
        )
    )
    result = target.overview("project-inventory", refresh=True)
    assert result.total_bytes == "232"
    assert len(result.warnings) == 1
    with pytest.raises(ProblemException):
        target.files("project-inventory", group_id="task:outside-scope")


def test_task_usage_requires_storage_capability_and_exact_project(usage):
    target, _, calls = usage
    service = StorageGovernanceService(InMemoryStorageRepository(), task_usage_reader=target)
    with pytest.raises(ProblemException):
        service.task_storage_overview(project_id="project-inventory", actor=reader("other"))
    assert not calls
