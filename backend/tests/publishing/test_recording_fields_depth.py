import pytest

from hc_data_platform.recording_fields import description


@pytest.mark.parametrize(
    "source,expected",
    [
        ({}, None),
        ({"depth_scale": 0.00025}, 0.00025),
        ({"depth_scale": True}, None),
        ({"depth_scale": float("nan")}, None),
        ({"depth_scale": -1}, None),
        ({"depth_source": {}, "depth_scale": 0.001}, None),
    ],
)
def test_public_fields_do_not_invent_source_units(source, expected):
    result = description({"sources": {"head": {"kind": "rgbd", **source}}})
    assert result["cameras"][0]["depth_scale_m_per_unit"] == expected


def test_public_fields_use_actual_declared_scale():
    profile = dict(
        schema_version="hc-depth-source/1",
        encoding="16UC1",
        unit="source_unit",
        scale_m_per_unit=0.00025,
        invalid_values=[0],
        invalid_nonfinite=True,
        registered_to_rgb=None,
        registration_evidence="",
        evidence=dict(kind="synthetic_fixture", reference=__name__, version="1"),
    )
    result = description({"sources": {"head": {"kind": "rgbd", "depth_source": profile}}})
    assert result["cameras"][0]["depth_scale_m_per_unit"] == 0.00025
