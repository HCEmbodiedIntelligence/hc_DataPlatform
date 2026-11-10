from contextlib import contextmanager
from types import SimpleNamespace

from hc_data_platform.lerobot_imports.mcap_retention import purge_failed
from hc_data_platform.object_storage.memory import InMemoryObjectStorage


def test_failed_cleanup_keeps_raw_and_other_attempts():
    store = InMemoryObjectStorage()
    ident = "a" * 64
    prefix = "derived-mcap/o/p/r/" + ident + "/"
    store.objects.update(
        {
            prefix + "partial": b"failed",
            "raw/source": b"original",
            "derived-mcap/o/p/r/" + ("b" * 64) + "/video": b"other",
        }
    )
    results = iter([("FAILED", None), None])
    db = SimpleNamespace(execute=lambda *a: SimpleNamespace(fetchone=lambda: next(results)))

    @contextmanager
    def connections():
        yield db

    assert (
        purge_failed(
            SimpleNamespace(storage=store, connections=connections), ("o", "p", "r"), ident
        )
        == 6
    )
    assert store.objects == {
        "raw/source": b"original",
        "derived-mcap/o/p/r/" + ("b" * 64) + "/video": b"other",
    }


def test_committed_attempt_is_not_collected():
    store = InMemoryObjectStorage()
    ident = "a" * 64
    key = "derived-mcap/o/p/r/" + ident + "/video"
    store.objects[key] = b"committed"
    results = iter([("FAILED", None), ("asset",)])
    db = SimpleNamespace(execute=lambda *a: SimpleNamespace(fetchone=lambda: next(results)))

    @contextmanager
    def connections():
        yield db

    assert (
        purge_failed(
            SimpleNamespace(storage=store, connections=connections), ("o", "p", "r"), ident
        )
        == 0
    )
    assert store.objects[key] == b"committed"
