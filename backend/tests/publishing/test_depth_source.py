import copy
import json

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from hc_data_platform.episode_store.validation import validate_package
from hc_data_platform.publishing.depth_source import (
    inspect_depth,
    profile_hash,
    require_depth_source,
)
from tests.episode_store.test_writer import build_rgbd_package


def profile():
    return dict(
        schema_version="hc-depth-source/1",
        encoding="16UC1",
        unit="mm",
        scale_m_per_unit=0.001,
        invalid_values=[0],
        invalid_nonfinite=True,
        registered_to_rgb=False,
        registration_evidence="independent synthetic grid",
        evidence=dict(kind="synthetic_fixture", reference=__name__, version="1"),
    )


@pytest.mark.parametrize(
    "change",
    [
        {"unit": "m"},
        {"scale_m_per_unit": True},
        {"scale_m_per_unit": float("nan")},
        {"invalid_values": [float("inf")]},
        {"invalid_nonfinite": False},
        {"encoding": []},
        {"unit": []},
        {"evidence": {"kind": [], "version": "1", "reference": "test"}},
        {"registration_evidence": ""},
        {"evidence": {}},
        {"extra": 1},
    ],
)
def test_malformed_declaration_is_invalid_not_inferred(change):
    pixels = np.ones((16, 16), dtype="<u2")
    declaration = {**profile(), **change}
    metadata = dict(
        source_profile=declaration,
        depth_scale=0.001,
        encoding="16UC1",
        shape=[16, 16],
        dtype="<u2",
        invalid_value=0,
    )
    assert inspect_depth(metadata, pixels)["status"] == "INVALID"
    with pytest.raises(ValueError, match="DEPTH_SOURCE_CONTRACT_REQUIRED"):
        require_depth_source(metadata, pixels)


@pytest.mark.parametrize("tamper", ["hash", "scale", "missing_reference", "dtype"])
def test_full_package_binds_declared_source_units_to_each_depth_frame(tmp_path, tamper):
    declaration = profile()
    output, source, _, _ = build_rgbd_package(tmp_path, source_profile=declaration)
    validate_package(output, source)
    report_path = next(output.glob("hc/depth/*/report.json"))
    report = json.loads(report_path.read_text())
    assert report["source_profiles"] == {profile_hash(declaration): declaration}
    assert report["source_contract_required"] is True
    if tamper == "hash":
        report["source_profiles"][profile_hash(declaration)]["evidence"]["version"] = "tampered"
        report_path.write_text(json.dumps(report))
    else:
        path = report_path.parent / "frames.parquet"
        table = pq.read_table(path)
        rows = copy.deepcopy(table.to_pylist())
        if tamper == "scale":
            rows[0]["depth_scale_m_per_unit"] = 0.00025
        elif tamper == "missing_reference":
            rows[0]["source_profile_sha256"] = None
        else:
            rows[0]["source_dtype"] = "<f4"
        pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), path)
    with pytest.raises(ValueError, match="depth .*source profile|depth frame units/dtype"):
        validate_package(output, source)
