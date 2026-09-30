"""Actual committed missing-depth selections; synthetic inputs, no sensor claim."""

import io
import json
import os
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from hc_data_platform.episode_store.derivation import write_tag_episode
from hc_data_platform.episode_store.models import canonical
from hc_data_platform.episode_store.quality import read_episode_quality
from hc_data_platform.episode_store.validation import parquet_rows
from tests.episode_store.test_annotation_revision_files_services import files  # noqa: F401
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_depth_window import forbid_quantization
from tests.episode_store.test_tag_derivation_services import selected  # noqa: F401


@pytest.mark.integration
@pytest.mark.parametrize(
    "prepared_bundle,status",
    [
        ({"depth_missing_frames": [1, 2, 3]}, "NO_SOURCE_FRAMES"),
        ({"depth_invalid_frames": [1, 2, 3]}, "NO_VALID_PIXELS"),
    ],
    indirect=["prepared_bundle"],
)
def test_selected_depth_absence_commits_and_reuses_codes_with_official_preview(
    selected,  # noqa: F811
    tmp_path,
    monkeypatch,
    status,
):
    source, packages, freeze = selected
    key = "observation.images.head_depth"
    with scoped(source):
        parent, snapshot, plan = freeze("child")
        original = parent.receipt
        forbid_quantization(monkeypatch)
        root = tmp_path / "child"
        write_tag_episode(root, parent, snapshot, plan)
        receipt = packages.committer.commit(root, plan.input)
        _, ready = packages._publish_committed(receipt, plan.input, plan.schema_snapshot)
        assert ready.step_count == 3
        child = packages.files.open(receipt.data.identity, receipt.data.data_version_id)
        prefix = child.document("hc/modalities.json")["depth"][key]["evidence_path"]
        report = child.document(prefix + "/report.json")
        assert report["measurement_status"] == status
        assert report["totals"]["valid_pixels"] == 0
        assert report["totals"]["invalid_pixels"] == 3 * 16 * 16
        assert report["quantization"]["unclipped_max_error_m"] is None
        assert report["video_encoding"]["lossless_codes"] is True
        assert report["window"]["requantized"] is False
        assert key not in child.document("meta/stats.json")
        assert (
            child.document("meta/info.json")["features"][key]["info"]["hc.stats_status"]
            == "NO_VALID_PIXELS"
        )
        qc = read_episode_quality(child)
        assert qc.evidence_status == "AVAILABLE"
        assert qc.status == ("RISK" if status == "NO_SOURCE_FRAMES" else "PASS")
        for index in range(3):
            frame = packages.depth.read(
                receipt.data.identity, receipt.data.data_version_id, key, index, unit="m"
            )
            assert np.isnan(frame.values).all() and not frame.flags.any()
            assert frame.pixel(0, 0).valid is False and frame.pixel(0, 0).value is None
            assert frame.source["missing"] is (status == "NO_SOURCE_FRAMES")
            if status == "NO_SOURCE_FRAMES":
                assert frame.source["source_seq"] is None
                assert frame.source["depth_scale_m_per_unit"] is None
            else:
                assert frame.source["source_seq"] == index + 1
                assert frame.source["depth_scale_m_per_unit"] == 0.001
            with Image.open(io.BytesIO(frame.preview_png())) as preview:
                assert not np.asarray(preview).any()  # Actual official SDK, all invalid.
        with child.file("meta/episodes/chunk-000/file-000.parquet") as path:
            episode = next(parquet_rows(path))
            assert not any(name.startswith(f"stats/{key}/") for name in episode)
        assert parent.receipt == original
        assert key in parent.document("meta/stats.json")  # Parent frame 0 is valid.
        evidence = (
            Path(os.getenv("HC_G1_WRITER_EVIDENCE_ROOT", str(tmp_path)))
            / "missing-depth"
            / receipt.data.data_version_id
        )
        evidence.mkdir(parents=True)
        for entry in child.manifest.files:
            with child.file(entry.path) as path:
                destination = evidence / "episode" / entry.path
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, destination)
        (evidence / "episode/hc/manifest.json").write_bytes(
            canonical(child.manifest.model_dump(mode="json"))
        )
        (evidence / "episode/hc/commit.json").write_bytes(
            canonical(receipt.model_dump(mode="json"))
        )
        (evidence / "result.json").write_text(
            json.dumps(
                {
                    "data": receipt.data.model_dump(mode="json"),
                    "measurement_status": status,
                    "official_black_preview": True,
                    "masked_physical_values_all_nan": True,
                    "numeric_depth_statistics_absent": True,
                    "quantization_error_unmeasured": True,
                    "requantized": False,
                    "parent_unchanged": True,
                    "synthetic_input": True,
                    "human_acceptance": False,
                },
                indent=2,
            )
            + "\n"
        )
