import copy
import hashlib
import json
from types import SimpleNamespace

import pytest
from tests.annotation.test_subtasks import snapshot  # noqa: F401

from hc_data_platform.episode_store.holobrain_export import write_dataset
from hc_data_platform.episode_store.native_capture import AUDIT, MANIFEST, MCAP, build_native
from hc_data_platform.episode_store.native_sources import native_source
from hc_data_platform.episode_store.original_export import copy_original
from hc_data_platform.episode_store.selected_cache import (
    freeze_selection,
    materialize_selected,
    selected_reference,
)
from hc_data_platform.object_storage.memory import InMemoryObjectStorage
from tests.episode_store.test_holobrain_export import rows, source


def fixture(tmp_path):
    src = source(tmp_path)
    provenance = src["provenance"]
    raw = tmp_path / "raw.mcap"
    provenance["source_origin_ns"] = 10**12
    provenance["source_object"] = {
        "path": "raw.mcap",
        "object_key": "raw/original.mcap",
        "size": raw.stat().st_size,
        "sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
    }
    store = InMemoryObjectStorage()
    store.objects["raw/original.mcap"] = raw.read_bytes()
    freeze_selection(provenance, "derived-mcap/o/p/r/id")
    return src, store


def test_reconstruct_selected_without_persisting_it(tmp_path):
    src, store = fixture(tmp_path)
    ref = selected_reference(src["provenance"])
    before = dict(store.objects)
    target = tmp_path / "rebuild.mcap"
    materialize_selected(store, ref, src["provenance"], target)
    assert target.read_bytes() == src["path"].read_bytes()
    assert store.objects == before
    corrupt = copy.deepcopy(src["provenance"])
    corrupt["source_object"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SOURCE_MISMATCH"):
        materialize_selected(store, ref, corrupt, tmp_path / "bad.mcap")


def test_native_window_preserves_holobrain_values_and_images(tmp_path):
    src, _ = fixture(tmp_path)
    frames = {
        10**12 + i * 33_000_000: {"task": "pick", "segment": 0, "parent_frame_index": i}
        for i in (1, 2, 3)
    }
    src["frames"] = frames
    write_dataset(tmp_path / "before", [src])
    root = tmp_path / "native"
    entries = build_native(
        root,
        src["path"],
        src["provenance"],
        10**12 + 33_000_000,
        10**12 + 132_000_000,
        audit=src["audit_path"],
    )
    document = json.loads((root / MANIFEST).read_bytes())
    assert {e.path for e in entries} == {MCAP, AUDIT, MANIFEST}
    migrated = {
        **src,
        "path": root / MCAP,
        "audit_path": root / AUDIT,
        "provenance": document["provenance"],
    }
    write_dataset(tmp_path / "after", [migrated])
    assert rows(tmp_path / "before") == rows(tmp_path / "after")


def test_backfilled_episode_exports_after_all_processing_objects_are_removed(tmp_path):
    src, store = fixture(tmp_path)
    prov = src["provenance"]
    selected = selected_reference(prov)
    audit_key = "derived-mcap/o/p/r/id/audit.json"
    store.objects[audit_key] = src["audit_path"].read_bytes()
    audit = {
        "relative_path": "source/normalization.json",
        "object": {
            "key": audit_key,
            "size": len(store.objects[audit_key]),
            "sha256": hashlib.sha256(store.objects[audit_key]).hexdigest(),
        },
    }
    document = {
        "mcap": prov,
        "inventory": [audit],
        "start_offset_ns": 33_000_000,
        "end_offset_ns": 132_000_000,
    }
    data = SimpleNamespace(
        package_prefix="episodes/o/p/r/version", model_dump=lambda **_: {"id": "version"}
    )
    package = SimpleNamespace(
        receipt=SimpleNamespace(data=data),
        manifest=SimpleNamespace(files=[]),
        document=lambda path: document,
    )
    before_objects = dict(store.objects)

    def rejected(before, after):
        raise ValueError("baseline unavailable")

    with pytest.raises(ValueError, match="baseline unavailable"):
        native_source(package, store, allow_backfill=True, verifier=rejected)
    assert store.objects == before_objects

    def verified(before, after):
        expected = {
            10**12 + i * 33_000_000: {"task": "pick", "segment": 0, "parent_frame_index": i}
            for i in (1, 2, 3)
        }
        write_dataset(tmp_path / "baseline", [{**src, "path": before, "frames": expected}])
        write_dataset(tmp_path / "candidate", [{**src, "path": after, "frames": expected}])
        assert rows(tmp_path / "baseline") == rows(tmp_path / "candidate")
        return {"status": "VERIFIED", "method": "holobrain-rows-sha256/v1"}

    file, provenance, audit_ref = native_source(
        package, store, allow_backfill=True, verifier=verified
    )
    from hc_data_platform.episode_store.native_sources import export_source

    proof_key = data.package_prefix + "/native-retained-v1/verification.json"
    proof = store.objects.pop(proof_key)
    before_fallback = dict(store.objects)
    assert export_source(package, store)[0] == selected
    assert store.objects == before_fallback
    store.objects[proof_key] = proof
    # Neither raw nor processing storage is an export dependency after backfill.
    for key in list(store.objects):
        if not key.startswith("episodes/"):
            del store.objects[key]
    assert native_source(package, store) == (file, provenance, audit_ref)
    target = tmp_path / "retained.mcap"
    copy_original(store, file, target, lambda: None)
    assert hashlib.sha256(target.read_bytes()).hexdigest() == file.object.sha256
    assert file.object.key.startswith(data.package_prefix + "/")
    assert selected.object.key not in store.objects
    other = SimpleNamespace(
        package_prefix=data.package_prefix, model_dump=lambda **_: {"id": "other"}
    )
    package.receipt.data = other
    with pytest.raises(ValueError, match="another data version"):
        native_source(package, store)


@pytest.mark.parametrize("raw_reference", [False, True])
@pytest.mark.parametrize("outcome", [None, "success", "fail"])
def test_both_exports_read_only_episode_owned_bytes(tmp_path, raw_reference, outcome, snapshot):
    from contextlib import contextmanager

    import pyarrow as pa
    import pyarrow.parquet as pq
    from mcap.reader import make_reader

    from hc_data_platform.data_formats.lerobot_layout import DATA_PATH
    from hc_data_platform.episode_store.holobrain_export import write_collection
    from hc_data_platform.episode_store.normalized_export import write_mcap_collection

    src, store = fixture(tmp_path)
    src["provenance"]["format"] = "native-mcap-recording/4"
    root = tmp_path / "episode"
    entries = build_native(
        root,
        src["path"],
        src["provenance"],
        10**12 + 33_000_000,
        10**12 + 132_000_000,
        audit=src["audit_path"],
    )
    timestamps = [10**12 + i * 33_000_000 for i in (1, 2, 3)]
    data_path = root / DATA_PATH
    data_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table(
            {
                "source.timestamp_ns": timestamps,
                "hc.sample_valid": [True] * 3,
                "task_index": [0] * 3,
            }
        ),
        data_path,
    )
    (root / "meta").mkdir(exist_ok=True)
    pq.write_table(pa.table({"task_index": [0], "task": ["pick"]}), root / "meta/tasks.parquet")
    source_doc = {
        "mcap": src["provenance"],
        "inventory": [],
        "start_offset_ns": 33_000_000,
        "end_offset_ns": 132_000_000,
    }
    (root / "hc/source.json").write_text(json.dumps(source_doc))
    prefix = "episodes/pinned/v1"
    if raw_reference:
        from hc_data_platform.episode_store.native_reference import build_reference
        from hc_data_platform.episode_store.native_capture import file_entry
        entries = [build_reference(root, src["provenance"], timestamps[0], timestamps[-1] + 33_000_000),
                   file_entry(root, "hc/source.json")]
        audit_key = "derived-mcap/o/p/r/id/audit.json"
        store.objects[audit_key] = src["audit_path"].read_bytes()
        source_doc["inventory"] = [{"relative_path": "source/normalization.json", "object": {
            "key": audit_key, "size": len(store.objects[audit_key]),
            "sha256": hashlib.sha256(store.objects[audit_key]).hexdigest()}}]
        (root / "hc/source.json").write_text(json.dumps(source_doc))
        entries[-1] = file_entry(root, "hc/source.json")
    else:
        store.objects.clear()
    for entry in entries:
        store.objects[prefix + "/" + entry.path] = (root / entry.path).read_bytes()

    @contextmanager
    def local(path):
        yield root / path

    data = SimpleNamespace(package_prefix=prefix, model_dump=lambda **_: {"data_version_id": "v1"})
    package = SimpleNamespace(
        receipt=SimpleNamespace(data=data),
        manifest=SimpleNamespace(files=entries),
        document=lambda path: json.loads((root / path).read_bytes()),
        file=local,
    )
    selection = SimpleNamespace(package=package, annotation_file=None, track=None)
    if outcome is not None:
        from tests.episode_store.test_episode_outcome_exports import frozen_result
        data = snapshot[1].model_copy(update={"frame_count": 3, "package_prefix": prefix})
        package.receipt.data = data
        selection.annotation_file, selection.track = frozen_result(snapshot, data, outcome)
    write_collection(tmp_path / "holobrain", [selection], storage=store, modalities="rgbd")
    src["frames"] = {
        stamp: {"task": "pick", "segment": 0, "parent_frame_index": i}
        for i, stamp in enumerate(timestamps)
    }
    write_dataset(tmp_path / "expected", [src])
    assert rows(tmp_path / "holobrain") == rows(tmp_path / "expected")
    write_mcap_collection(tmp_path / "mcap-export", [selection], storage=store)
    with next((tmp_path / "mcap-export").glob("*.mcap")).open("rb") as stream:
        messages = list(
            make_reader(stream).iter_messages(topics=["/observation/cameras/head/color/compressed"])
        )
    assert len(messages) == 3
    if outcome is not None:
        import duckdb
        with duckdb.connect(str(tmp_path / "holobrain/meta_db.duckdb"), read_only=True) as db:
            stored_success, info = db.execute('SELECT success,info FROM episode').fetchone()
            assert stored_success is None
            assert json.loads(info)["episode_outcome"] == outcome
        with next((tmp_path / "mcap-export").glob("*.mcap")).open("rb") as stream:
            metadata = next(m for m in make_reader(stream).iter_metadata() if m.name == "hc.capture.selection")
            document = json.loads(metadata.metadata["document"])
            assert document["episode_outcome"] == outcome
            assert "success" not in document
    if raw_reference:
        assert not any(e.path.endswith(".mcap") for e in entries)
        assert selected_reference(src["provenance"]).object.key not in store.objects
        assert sum(e.size for e in entries) < 16_384
    else:
        assert all(key.startswith(prefix + "/") for key in store.objects)
