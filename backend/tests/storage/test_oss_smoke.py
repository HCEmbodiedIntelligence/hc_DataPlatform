import hashlib
import io
from types import SimpleNamespace

import pytest

from hc_data_platform.tools.oss_smoke import SmokeError, destination_key, verify_destination


class Stored:
    def __init__(self, data, corrupt_range=False):
        self.data, self.corrupt_range = data, corrupt_range

    def head(self, key):
        return SimpleNamespace(size=len(self.data))

    def read_chunks(self, key):
        yield self.data

    def read_range(self, key, start, end):
        return b"wrong" if self.corrupt_range else self.data[start:end]


def test_round_trip_checks_content_not_only_size_and_checks_ranges():
    data = bytes(range(256)) * 1024
    evidence = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    verify_destination(Stored(data), "test", io.BytesIO(data), evidence)
    with pytest.raises(SmokeError, match="checksum"):
        verify_destination(Stored(data[::-1]), "test", io.BytesIO(data), evidence)
    with pytest.raises(SmokeError, match="range"):
        verify_destination(Stored(data, True), "test", io.BytesIO(data), evidence)


@pytest.mark.parametrize("prefix", ["", "/", "/other", "../other", "hc/../other", "hc\\other"])
def test_destination_rejects_outside_prefix(prefix):
    with pytest.raises(SmokeError):
        destination_key(prefix, "raw/test.mcap", "run-1")


def test_destination_stays_under_selected_folder():
    assert destination_key("hc_dataplatform/", "raw/source/test.mcap", "run-1") == (
        "hc_dataplatform/oss-smoke/run-1/test.mcap"
    )
