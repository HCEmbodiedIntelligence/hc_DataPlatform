import hashlib

from hc_data_platform.episode_store.files import VerifiedFileCache
from hc_data_platform.episode_store.models import FileEntry


def test_adopted_commit_file_is_read_without_download(tmp_path):
    source = tmp_path / "package" / "videos" / "head.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"video bytes")
    entry = FileEntry(path="videos/head.mp4", size=11, sha256=hashlib.sha256(b"video bytes").hexdigest(),
                      media_type="video/mp4")
    cache = VerifiedFileCache(tmp_path / "cache", capacity_bytes=64)

    class NoDownload:
        def read_chunks(self, key):
            raise AssertionError("adopted file must not be downloaded")

    cache.adopt(source, entry)
    source.unlink()
    with cache.file(NoDownload(), "prefix/videos/head.mp4", entry) as path:
        assert path.read_bytes() == b"video bytes"
    cache.close()
