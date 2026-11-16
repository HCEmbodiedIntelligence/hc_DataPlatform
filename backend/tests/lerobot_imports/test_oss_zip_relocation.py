import pytest
from hc_data_platform.core.context import RequestContext, bind_request_context, reset_request_context
from hc_data_platform.storage.oss_zip import OssZipSources
import io
import zipfile
from types import SimpleNamespace


class ReadOnlyArchive:
    etag = "v1"

    def __init__(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("run/sample.mcap", b"recording")
        self.contents = payload.getvalue()

    def get_object_meta(self, key):
        return SimpleNamespace(headers={"Content-Length": len(self.contents)}, etag=self.etag)

    def get_object(self, key, *, byte_range, headers):
        assert headers["If-Match"] == f'"{self.etag}"'
        start, end = byte_range
        return io.BytesIO(self.contents[start:end+1])



def test_relocated_archive_requires_allowlist_and_original_etag():
    old = "oss://bucket/original.zip"
    new = "oss://bucket/dagger/original.zip"
    ctx = bind_request_context(RequestContext(organization_id="org",project_id="p",region_code="cn"))
    try:
        archive = ReadOnlyArchive()
        reader = OssZipSources({"org/p/cn":[old,new]},lambda _:archive,relocations={"org/p/cn":{old:new}})
        checked = []
        head = archive.get_object_meta
        def check_key(key):
            checked.append(key)
            return head(key)
        archive.get_object_meta = check_key
        locator = old + "#zip=run%2Fsample.mcap&etag=v1"
        assert b"".join(reader.chunks(locator,1024)) == b"recording" and checked == ["dagger/original.zip"]
        archive.etag = "v2"
        with pytest.raises(ValueError,match="变化"):
            reader.head(locator)
        reader.allowed["org/p/cn"]=[old]
        with pytest.raises(ValueError):
            reader.head(locator)
    finally:
        reset_request_context(ctx)
