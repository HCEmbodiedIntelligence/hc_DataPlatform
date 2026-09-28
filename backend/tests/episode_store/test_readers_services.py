"""Real object/DB reads, including exact-version authorization on a warm cache."""

import io
from uuid import uuid4

import numpy as np
import pytest
from PIL import Image

from hc_data_platform.episode_store.commit import EpisodePackageCommitter
from hc_data_platform.episode_store.files import PackageFiles, VerifiedFileCache
from hc_data_platform.episode_store.models import PackageInput
from hc_data_platform.episode_store.prepared_assets import PreparedAssets
from hc_data_platform.episode_store.reader import ParquetStepReader, PhysicalDepthReader
from hc_data_platform.episode_store.writer import write_episode
from hc_data_platform.lance_catalog.models import StepRecord
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_writer import build_rgbd_package, identity


@pytest.fixture
def cache(tmp_path):
    value = VerifiedFileCache(
        tmp_path / "cache", capacity_bytes=8 * 1024**2, max_object_bytes=16 * 1024**2
    )
    yield value
    value.close()


@pytest.mark.integration
def test_fixed_version_rgbd_reads_masked_values_and_denies_foreign_binding(
    services, cache, tmp_path
):
    repository, storage = services
    ident = identity().model_copy(update={"organization_id": "reader-" + uuid4().hex})
    root, source, _, _ = build_rgbd_package(tmp_path, ident)
    files = PackageFiles(repository, storage, cache)
    steps, depth = ParquetStepReader(files), PhysicalDepthReader(files)
    key = "observation.images.head_depth"
    with scoped(source):
        receipt = EpisodePackageCommitter(repository, storage).commit(root, source)
        version = receipt.data.data_version_id
        read = steps.read_steps(ident, version, 1, 4, columns=["/observation/state", key])
        assert [row.timestamp_ns for row in read.steps] == [i * 33_333_333 for i in range(1, 4)]
        assert [row.modalities["/observation/state"] for row in read.steps] == [
            [0.1 + i * 1e-12] for i in range(1, 4)
        ]
        assert all(set(row.modalities) == {"/observation/state", key} for row in read.steps)
        assert read.steps[0].source_timestamps_ns[key] == (33_333_333,)
        assert read.steps[0].modalities[key]["kind"] == "episode_video_frame"
        # First, last, and reverse seeks; no assumption about sequential decoder state.
        for index in (3, 0, 2, 1):
            decoded = depth.read(ident, version, key, index, unit="mm")
            assert decoded.unit == "mm" and decoded.values.dtype == np.float32
            assert np.isnan(decoded.values[0, 0])
            assert abs(float(decoded.values[1, 1]) - (500 + index * 500)) <= 3
            assert decoded.source["source_seq"] == index
            with np.load(io.BytesIO(decoded.binary()), allow_pickle=False) as binary:
                np.testing.assert_equal(binary["values"], decoded.values)
                np.testing.assert_equal(binary["flags"], decoded.flags)
            with Image.open(io.BytesIO(decoded.preview_png())) as preview:
                assert preview.size == (16, 16)
                assert preview.getpixel((0, 0)) == (0, 0, 0)
        metres = depth.read(ident, version, key, 3, unit="m")
        assert abs(float(metres.values[1, 1]) - 2.0) < 0.003
        assert "lance_version" not in read.model_dump_json()
        with pytest.raises(KeyError):
            files.open(ident.model_copy(update={"episode_id": "another"}), version)
        with pytest.raises(KeyError):
            files.open(ident.model_copy(update={"rollout_id": "forged"}), version)
        package = files.open(ident, version)
        with (
            pytest.raises(KeyError),
            package.file("../raw/private"),
        ):  # Never receives an object key.
            pass
        with pytest.raises(PermissionError):
            files.open(ident.model_copy(update={"region_code": "foreign"}), version)
    foreign = source.model_copy(
        update={"identity": ident.model_copy(update={"project_id": "foreign"})}
    )
    with scoped(foreign), pytest.raises(PermissionError):
        steps.read_steps(ident, version, 0, 1)


@pytest.mark.integration
def test_parquet_seek_prunes_groups_and_columns_and_preserves_numeric_semantics(
    services, cache, tmp_path, monkeypatch
):
    repository, storage = services
    ident = identity().model_copy(update={"organization_id": "numeric-" + uuid4().hex})
    source = PackageInput(
        identity=ident,
        source_sha256="3" * 64,
        processor_version="seek-test/1",
        configuration_sha256="4" * 64,
        schema_snapshot_id="schema-1",
        required_features=("observation.state", "action"),
    )
    rows = [
        StepRecord(
            rollout_id=ident.rollout_id,
            step_index=i,
            timestamp_ns=1_790_000_000_000_000_001 + i * 33_333_333,
            modalities={
                "/observation/state": {
                    "positions": [0.1 + i * 1e-12],
                    "names": ["joint"],
                    "units": "rad",
                },
                "action": [float(i), float(-i)],
            },
            source_timestamps_ns={"/observation/state": (99 + i,)},
            valid={"/observation/state": i % 2 == 0},
            repeated={"/observation/state": i % 3 == 0},
        )
        for i in range(2051)
    ]
    with scoped(source):
        root = tmp_path / "numeric"
        write_episode(
            root,
            source,
            iter(rows),
            PreparedAssets(ident, "attempt", {}),
            fps=30,
            metadata={"task": "row group pruning probe"},
        )
        receipt = EpisodePackageCommitter(repository, storage).commit(root, source)
        reader = ParquetStepReader(PackageFiles(repository, storage, cache))
        import pyarrow.parquet as pq

        batches = pq.ParquetFile.iter_batches
        calls = []

        def observe(self, *args, **kwargs):
            calls.append(kwargs)
            return batches(self, *args, **kwargs)

        monkeypatch.setattr(pq.ParquetFile, "iter_batches", observe)
        window = reader.read_steps(
            ident, receipt.data.data_version_id, 2048, 2051, columns=["/observation/state"]
        )
        assert len(calls) == 1 and calls[0]["row_groups"] == [2]
        assert "action" not in calls[0]["columns"]
        for actual, original in zip(window.steps, rows[2048:], strict=True):
            assert actual.timestamp_ns == original.timestamp_ns
            assert (
                actual.modalities["/observation/state"] == original.modalities["/observation/state"]
            )
            assert actual.valid == original.valid and actual.repeated == original.repeated
            assert actual.source_timestamps_ns == original.source_timestamps_ns

        # A cached seek must not re-download the numerical/media files.
        def no_download(*args, **kwargs):
            raise AssertionError("warm seek unexpectedly downloaded object bytes")

        monkeypatch.setattr(storage, "read_chunks", no_download)
        assert reader.read_steps(
            ident, receipt.data.data_version_id, 1023, 1025, columns=["action"]
        ).steps[0].modalities["action"] == [1023.0, -1023.0]
        with pytest.raises(ValueError, match="beyond"):
            reader.read_steps(ident, receipt.data.data_version_id, 2050, 2052)
