"""Read the actual download ZIP and verify the reference report against its data."""

import hashlib
import json
import zipfile
from types import SimpleNamespace

import duckdb
import pytest
from mcap.records import Chunk
from mcap.stream_reader import StreamReader

from hc_data_platform.episode_store import holobrain_export
from hc_data_platform.episode_store.collection_delivery import CollectionDelivery
from hc_data_platform.episode_store.export_formats import from_archive, get_format
from hc_data_platform.episode_store.holobrain_validation import validate
from tests.episode_store.test_holobrain_export import rows, source


def selected_sources(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    a, b = source(first), source(second, missing_actions=(1, 2))
    a["frames"] = {
        10**12 + i * 33_000_000: {"task": "抓取" if i < 4 else "放下",
                                "segment": int(i == 4), "parent_frame_index": i}
        for i in (0, 2, 4)
    }
    return [a, b]


@pytest.mark.parametrize("version", [1, 2, 3, 4])
@pytest.mark.parametrize("outcome", ["success", "fail"])
def test_download_keeps_all_shards_and_only_reference_files_for_new_jobs(
    tmp_path, monkeypatch, version, outcome
):
    sources = selected_sources(tmp_path)
    for source in sources:
        source["episode_outcome"] = outcome

    def write_collection(root, selections, *, storage, modalities, checkpoint, **kwargs):
        kwargs.pop("split_annotations", None)
        return holobrain_export.write_dataset(root, selections, checkpoint=checkpoint, **kwargs)

    monkeypatch.setattr(holobrain_export, "write_collection", write_collection)
    totals, progress = [], []
    repository = SimpleNamespace(
        phase=lambda *args: None,
        pin_totals=lambda row, count, size: totals.append((count, size)),
        progress=lambda row, count, size: progress.append((count, size)),
    )
    delivery = CollectionDelivery(SimpleNamespace(repository=repository, storage=None))
    monkeypatch.setattr(delivery, "sources", lambda _: sources)
    archive_format = f"holobrain_collection_zip/{version}"
    plan = SimpleNamespace(archive_format=archive_format, modalities="rgbd", job_id="test-job",
                           model_dump=lambda **_: {"archive_format": archive_format})
    archive_path = tmp_path / "download.zip"
    checksum, size = delivery.build(plan, tmp_path, archive_path, {}, lambda: None)
    assert checksum == hashlib.sha256(archive_path.read_bytes()).hexdigest()
    assert size == archive_path.stat().st_size
    expected = rows(tmp_path / "collection")
    assert len(expected) == 6
    assert all(row["joints"]["position"]["shape"] == [1, 19] for row in expected)
    prefix = "" if version >= 3 else "collection/"
    with zipfile.ZipFile(archive_path) as archive:
        state = json.loads(archive.read(prefix + "state.json"))
        shards = {entry["filename"] for entry in state["_data_files"]}
        assert len(shards) == 3
        if version >= 3:
            assert set(archive.namelist()) == shards | {
                "state.json", "dataset_info.json", "meta_db.duckdb", "conversion_report.json",
            }
        else:
            assert {"delivery.json", "collection/hc/collection_manifest.json",
                    "collection/alignment_provenance.jsonl", "collection/validation_report.json"
                    } <= set(archive.namelist())
        assert totals == [(len(archive.infolist()), sum(f.file_size for f in archive.infolist()))]
        assert progress[-1] == totals[0]
        archive.extractall(tmp_path / "download")
    downloaded = tmp_path / "download" / prefix
    assert rows(downloaded) == expected
    with duckdb.connect(str(downloaded / "meta_db.duckdb"), read_only=True) as db:
        assert db.execute('SELECT frame_num FROM episode ORDER BY "index"').fetchall() == [
            (2,), (1,), (3,),
        ]
        assert db.execute('SELECT success FROM episode ORDER BY "index"').fetchall() == [(None,)] * 3
        assert all(json.loads(row[0])["episode_outcome"] == outcome
                   for row in db.execute('SELECT info FROM episode').fetchall())
    if version >= 3:
        report = json.loads((downloaded / "conversion_report.json").read_bytes())
        assert all(row["episode_outcome"] == outcome and "success" not in row
                   for row in report["episodes"])
    assert get_format("holobrain").archive == "holobrain_collection_zip/4"
    assert from_archive(archive_format).standalone_dataset == (version >= 3)


def test_reference_report_measures_real_sources_drops_and_selected_episodes(tmp_path):
    sources = selected_sources(tmp_path)
    root = tmp_path / "dataset"
    holobrain_export.write_dataset(root, sources, reference_report=True)
    report = json.loads((root / "conversion_report.json").read_bytes())
    assert set(report) == {"episode_count", "episodes", "total_frames"}
    assert report["episode_count"] == len(report["episodes"]) == 3
    assert report["total_frames"] == 6
    assert [e["frames"]["output_frames"] for e in report["episodes"]] == [2, 1, 3]
    assert [e["frames"]["dropped_by_reason"] for e in report["episodes"]] == [
        {"excluded_by_selection": 2}, {}, {"joint_command_outside_tolerance": 2},
    ]
    assert sum(e["frames"]["candidate_rgb_frames"] for e in report["episodes"]) == 10
    for entry, src in zip(report["episodes"], [sources[0], sources[0], sources[1]], strict=True):
        assert set(entry) == {
            "source", "recovery", "messages", "frames", "joint_command_zero_tail_messages",
            "finger_command_trimmed_tail_messages",
        }
        assert entry["source"] == {
            k: src["provenance"]["selected_mcap"][k] for k in ("path", "sha256", "size_bytes")
        }
        with src["path"].open("rb") as stream:
            records = list(StreamReader(stream, emit_chunks=True).records)
        assert entry["recovery"] == {
            "recovered": False, "complete_records": len(records),
            "complete_chunks": sum(isinstance(record, Chunk) for record in records),
            "recovery_offset": None, "discarded_bytes": 0,
        }
        assert entry["messages"] == src["provenance"]["selected_mcap"]["message_counts"]
        assert entry["joint_command_zero_tail_messages"] == 0
        assert entry["finger_command_trimmed_tail_messages"] == 0
        frames = entry["frames"]
        assert set(frames) == {"candidate_rgb_frames", "output_frames", "dropped_frames",
                               "dropped_by_reason", "nearest_neighbor_error_ms"}
        for name, stats in frames["nearest_neighbor_error_ms"].items():
            assert stats == {"max": 0.0, "mean": 0.0, "p95": 0.0,
                             "tolerance": 50.0 if name == "depth" else
                             150.0 if "finger" in name else 20.0}
    assert validate(root)["frames"] == 6
    report["episodes"][0]["frames"]["output_frames"] += 1
    (root / "conversion_report.json").write_text(json.dumps(report))
    with pytest.raises(ValueError, match="REPORT_EPISODE_MISMATCH"):
        validate(root)
