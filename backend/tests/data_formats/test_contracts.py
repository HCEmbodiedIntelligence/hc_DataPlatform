from __future__ import annotations

import subprocess
import sys

import pyarrow as pa
import pytest

from hc_data_platform.data_formats.arrow import FeatureShapeError, feature_type, lerobot_value
from hc_data_platform.data_formats.feature_names import feature_names, native_feature_topic
from hc_data_platform.data_formats.json import canonical_json_bytes
from hc_data_platform.episode_store.models import canonical


def test_unicode_serializations_keep_their_independent_hash_contracts() -> None:
    document = {"z": "抓取", "a": 1}
    assert canonical_json_bytes(document) == '{"a":1,"z":"抓取"}'.encode()
    assert canonical(document) == b'{"a":1,"z":"\\u6293\\u53d6"}'
    for encode in (canonical, canonical_json_bytes):
        with pytest.raises(ValueError):
            encode({"not_a_measurement": float("nan")})


def test_native_names_and_structured_values_roundtrip_without_services() -> None:
    name = "observation.新的传感器"
    topic = native_feature_topic(name)
    assert feature_names([topic]) == {topic: name}
    vector, encoding = lerobot_value({"positions": [1.0, 2.0], "names": ["left", "right"]})
    arrow_type, dtype, shape = feature_type(vector, pa)
    assert dtype == "float64" and shape == (2,)
    assert pa.array([vector], type=arrow_type).to_pylist() == [[1.0, 2.0]]
    assert encoding == {
        "encoding": "vector",
        "value_key": "positions",
        "names": ["left", "right"],
    }
    with pytest.raises(FeatureShapeError, match="rectangular"):
        feature_type([[1, 2], [3]], pa)


def test_codecs_import_without_domain_or_runtime_side_effects() -> None:
    script = """
import importlib
import sys
for name in ('json', 'arrow', 'archives', 'feature_names', 'lerobot_layout',
             'lerobot_format', 'depth_source', 'holobrain_depth', 'depth_evidence', 'video'):
    importlib.import_module('hc_data_platform.data_formats.' + name)
forbidden = ('hc_data_platform.publishing', 'hc_data_platform.episode_store',
             'hc_data_platform.ingest', 'hc_data_platform.lerobot_imports',
             'hc_data_platform.workflow', 'hc_data_platform.runtime',
             'fastapi', 'psycopg', 'boto3', 'oss2', 'temporalio')
unexpected = [name for name in sys.modules
              if any(name == p or name.startswith(p + '.') for p in forbidden)]
assert not unexpected, unexpected
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_legacy_helpers_are_the_same_codec_implementations() -> None:
    from hc_data_platform.core import ModuleRouter
    from hc_data_platform.core.discovery import ModuleRouter as DiscoveredRouter
    from hc_data_platform.data_formats.holobrain_depth import DepthVideo
    from hc_data_platform.publishing.exporters import _feature_type, _lerobot_value
    from hc_data_platform.publishing.holobrain_depth import DepthVideo as LegacyDepthVideo
    from hc_data_platform.publishing.service import canonical_json_bytes as legacy_json

    assert _feature_type is feature_type
    assert _lerobot_value is lerobot_value
    assert LegacyDepthVideo is DepthVideo
    assert legacy_json is canonical_json_bytes
    assert ModuleRouter is DiscoveredRouter
