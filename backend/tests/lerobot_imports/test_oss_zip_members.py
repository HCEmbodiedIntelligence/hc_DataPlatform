import io
import zipfile

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
        from types import SimpleNamespace
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
