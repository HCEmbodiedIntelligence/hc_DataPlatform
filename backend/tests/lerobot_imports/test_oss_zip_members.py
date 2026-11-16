import io
import zipfile
from types import SimpleNamespace

import pytest

from hc_data_platform.core.context import RequestContext, bind_request_context, reset_request_context
from hc_data_platform.storage.oss_zip import OssZipSources


class ReadOnlyArchive:
    def __init__(self):
        self.etag = "v1"
        contents = io.BytesIO()
        with zipfile.ZipFile(contents, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("run/sample.mcap", b"recording")
            archive.writestr("run/sample.takeovers.json", b"{}")
        self.contents = contents.getvalue()

    def get_object_meta(self, key):
        return SimpleNamespace(headers={"Content-Length": len(self.contents)}, etag=self.etag)

    def get_object(self, key, *, byte_range, headers):
        assert headers["If-Match"] == f'"{self.etag}"'
        start, end = byte_range
        return io.BytesIO(self.contents[start:end + 1])


def test_reads_only_selected_version_and_project():
    uri = "oss://source/recordings.zip"
    bucket = ReadOnlyArchive()
    reader = OssZipSources({"org/p/cn": [uri]}, lambda _: bucket)
    ctx = bind_request_context(RequestContext(organization_id="org", project_id="p", region_code="cn"))
    try:
        files = reader.inventory(uri)["files"]
        assert [f["path"] for f in files] == ["run/sample.mcap", "run/sample.takeovers.json"]
        locator = files[0]["object_key"]
        assert reader.head(locator).size == 9
        assert b"".join(reader.chunks(locator, 3)) == b"recording"
        assert b"".join(reader.chunks(locator, 2, start=2, end=6)) == b"cord"
        bucket.etag = "v2"
        with pytest.raises(ValueError, match="变化"):
            list(reader.chunks(locator, 3))
    finally:
        reset_request_context(ctx)
    other = bind_request_context(RequestContext(organization_id="org", project_id="other", region_code="cn"))
    try:
        with pytest.raises(ValueError, match="未配置"):
            reader.head(locator)
    finally:
        reset_request_context(other)


def test_project_can_inspect_unregistered_zip_and_read_its_recording():
    from hc_data_platform.object_storage.providers import OssObjectStorage

    archive = ReadOnlyArchive()
    adapter = OssObjectStorage(SimpleNamespace(bucket_name="managed"),
        zip_sources=OssZipSources({"org/p/cn": ["*"]}, lambda _: archive))
    ctx = bind_request_context(RequestContext(organization_id="org", project_id="p", region_code="cn"))
    try:
        for bucket in ["first-bucket", "another-bucket", "managed"]:
            uri = f"oss://{bucket}/new/recordings.ZIP"
            data = adapter.inspect_reference(uri)
            assert data["archive_members"] and len(data["files"]) == 2
            locator = data["files"][0]["object_key"]
            assert b"".join(adapter.read_chunks(locator)) == b"recording"
        for invalid in ["https://other/a.zip", "oss://name:80/a.zip",
                        "oss://other/a.zip?sign=x", "oss://other/a.zip#part",
                        "oss://other/%2e%2e/a.zip", "oss://other/a.mcap"]:
            with pytest.raises(ValueError):
                adapter.inspect_reference(invalid)
        archive.etag = "changed"
        with pytest.raises(ValueError):
            adapter.head(locator)
    finally:
        reset_request_context(ctx)
    ctx = bind_request_context(RequestContext(organization_id="org", project_id="q", region_code="cn"))
    try:
        with pytest.raises(ValueError):
            adapter.head(locator)
    finally:
        reset_request_context(ctx)


def test_managed_dataset_zip_keeps_dataset_import():
    archive = ReadOnlyArchive()
    contents = io.BytesIO()
    with zipfile.ZipFile(contents, "w") as out:
        out.writestr("bag/metadata.yaml", "rosbag2_bagfile_information")
        out.writestr("bag/data.mcap", b"rosbag")
    archive.contents = contents.getvalue()
    reader = OssZipSources({"org/p/cn": ["*"]}, lambda _: archive)
    ctx = bind_request_context(RequestContext(organization_id="org", project_id="p", region_code="cn"))
    try:
        assert reader.inventory("oss://managed/bag.zip", managed=True) is None
    finally:
        reset_request_context(ctx)


def test_regional_zip_uses_oss_location_on_endpoint_mismatch():

    class WrongRegion(Exception):
        status = 403

    class InitialBucket:
        def get_object_meta(self, key):
            raise WrongRegion()

        def get_bucket_location(self):
            return SimpleNamespace(location="oss-cn-shanghai")

    archive = ReadOnlyArchive()
    def factory(name, *, endpoint=None):
        assert name == "recordings"
        if endpoint is None:
            return InitialBucket()
        assert endpoint == "https://oss-cn-shanghai.aliyuncs.com"
        return archive

    reader = OssZipSources({"org/p/cn": ["*"]}, factory)
    ctx = bind_request_context(RequestContext(organization_id="org", project_id="p", region_code="cn"))
    try:
        assert reader.inventory("oss://recordings/new.zip")["archive_members"]
    finally:
        reset_request_context(ctx)


def test_full_member_read_stays_within_version_and_does_not_refetch_payload():
    from hc_data_platform.storage.oss_zip import BLOCK

    bucket = ReadOnlyArchive()
    data = b"abcd" * (5 * 1024**2)
    contents = io.BytesIO()
    with zipfile.ZipFile(contents, "w", zipfile.ZIP_STORED) as output:
        output.writestr("clip.mcap", data)
        output.writestr("other.mcap", b"end")
    bucket.contents = contents.getvalue()
    ranges = []
    original_get = bucket.get_object
    def track(key, *, byte_range, headers):
        ranges.append(byte_range)
        return original_get(key, byte_range=byte_range, headers=headers)
    bucket.get_object = track
    uri = "oss://source/recordings.zip"
    reader = OssZipSources({"org/p/cn": [uri]}, lambda _: bucket)
    ctx = bind_request_context(RequestContext(organization_id="org", project_id="p", region_code="cn"))
    try:
        locator = reader.inventory(uri)["files"][0]["object_key"]
        ranges.clear()
        assert b"".join(reader.chunks(locator, 128*1024, expected_version=("v1", len(data)))) == data
        payload_ranges = sorted((lo, hi) for lo, hi in ranges if lo < len(data))
        assert len(payload_ranges) == 2
        assert payload_ranges[1][0] == payload_ranges[0][1] + 1 == BLOCK
        assert payload_ranges[-1][1] < len(data) + 512
        with pytest.raises(ValueError, match="SOURCE_CHANGED"):
            list(reader.chunks(locator, 128*1024, expected_version=("v1", len(data)-1)))
    finally:
        reset_request_context(ctx)
