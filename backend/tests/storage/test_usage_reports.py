from datetime import timezone,datetime
from threading import Event

from hc_data_platform.storage.usage_objects import UsageObjectIndex, logical_name
from hc_data_platform.storage.inventory import ProviderInventoryObject
from hc_data_platform.storage.usage_reports import UsageReports
from .test_task_usage import request_scope, usage


def test_scoped_object_lookup_and_overlapping_aliases():
    aliases = {"episodes/task": "episodes/id-a", "episodes/task/rec": "episodes/id-b"}
    assert logical_name("episodes/task/rec/file", aliases) == "episodes/id-b/file"
    assert logical_name("episodes/task-other/file", aliases) == "episodes/task-other/file"
    now = datetime.now(timezone.utc)
    index = UsageObjectIndex([ProviderInventoryObject(k, 23, now) for k in ("exports/01/010/a", "exports/01/01/b")])
    assert [x.object_key for x in index.list_prefix("exports/01/01/")] == ["exports/01/01/b"]


def test_published_report_remains_readable_during_refresh(usage, tmp_path):
    target, entries, calls = usage
    reports = UsageReports(tmp_path, "bucket", target._scan)
    target.reports = reports
    first = target.overview("project-inventory")
    page = target.files("project-inventory", group_id="task:a", category="ANNOTATION")
    assert page.total_count == 1 and page.total_bytes == "2"
    assert page.observed_at == first.observed_at
    started, resume = Event(), Event()
    def slow(project):
        started.set()
        assert resume.wait(5)
        return target._scan(project)
    reports.scan = slow
    path = reports.path("project-inventory")
    pending = reports.refresh("project-inventory", path)
    try:
        assert started.wait(5)
        assert reports.refresh("project-inventory", path) is pending
        assert target.overview("project-inventory") == first
    finally:
        resume.set()
    assert pending.result(timeout=5).file_count == first.file_count
    reports.executor.shutdown()
