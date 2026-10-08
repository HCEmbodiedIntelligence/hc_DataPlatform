"""Byte-stable historical frame-selection semantics after ownership extraction."""

import json
from pathlib import Path

import pytest

from hc_data_platform.annotation.sampling import FrameSelectionSampler


@pytest.mark.parametrize("cap", [3, 10000])
def test_frozen_adaptive_v2_selection(cap):
    golden = json.loads((Path(__file__).parent / "fixtures/frame-selection-v2.json").read_text())
    sampler = FrameSelectionSampler()
    sampler._MAX_GROUPS = cap
    for event in golden["trace"]:
        if event[0] == "signal":
            sampler.observe_signal(*event[1:])
        else:
            sampler.observe_camera(
                *event[1:3], luma_mean=event[3], perceptual_hash=event[4], corrupt=event[5]
            )
    assert (
        sampler.manifest(source_sha256="a" * 64, camera_topics=("/rgb",))
        == golden["results"][str(cap)]
    )
