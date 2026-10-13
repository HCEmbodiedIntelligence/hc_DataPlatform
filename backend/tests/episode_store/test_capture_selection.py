import json

import pyarrow.parquet as pq
import pytest

from hc_data_platform.core.context import RequestContext, bind_request_context, reset_request_context
from hc_data_platform.episode_store.models import PackageInput
from hc_data_platform.episode_store.prepared_assets import PreparedAssets
from hc_data_platform.episode_store.writer import write_episode
from hc_data_platform.episode_store.validation import validate_package
from hc_data_platform.lance_catalog.models import StepRecord
from tests.episode_store.test_writer import identity, build_rgbd_package


def test_missing_row_drops_state_and_action_but_preserves_actual_time(tmp_path):
    ident = identity()
    source = PackageInput(identity=ident, source_sha256='1'*64, processor_version='test/1',
                          configuration_sha256='2'*64, schema_snapshot_id='schema-1',
                          required_features=('observation.state', 'action'))
    origin = 1790000000000000001
    rows = []
    for i in range(4):
        good = i != 1
        modalities = {
            '/observation/state': {'values': [float(i) if good else None], 'names': ['joint'], 'units': ['rad']},
            '/action': {'values': [float(10+i) if good else None], 'names': ['joint'], 'units': ['rad'],
                        'source_origins': {'arm': 'recorded_command'}},
            'source.timestamp_ns': origin+i*33_333_333 if good else None,
        }
        rows.append(StepRecord(rollout_id=ident.rollout_id, step_index=i, timestamp_ns=i*33_333_333,
                               modalities=modalities, valid={key: good for key in modalities}, sample_valid=good))
    token = bind_request_context(RequestContext(organization_id=ident.organization_id,
        project_id=ident.project_id, region_code=ident.region_code))
    try:
        root = tmp_path/'episode'
        manifest = write_episode(root, source, rows, PreparedAssets(ident, 'attempt', {}), fps=30,
                                 metadata={'quality': {'capture_quality': {'valid_ratio': .75}}})
        assert manifest.frame_count == 3
        table = pq.read_table(root/'data/chunk-000/file-000.parquet').to_pydict()
        assert table['observation.state'] == [0., 2., 3.]
        assert table['action'] == [10., 12., 13.]
        assert table['hc.timestamp_ns'] == [origin, origin+66_666_666, origin+99_999_999]
        assert table['timestamp'] == [0., .066666666, .099999999]
        assert table['hc.video_frame_index'] == [0, 1, 2]
        assert table['hc.source_frame_index'] == [0, 2, 3]
        assert table['hc.video_timestamp'] == [0., 1/30, 2/30]
        assert table['hc.sample_valid'] == [True]*3
        stats = json.loads((root/'meta/stats.json').read_bytes())
        assert stats['action']['count'] == [3]
        assert stats['action']['min'] == [10.]
        assert validate_package(root, source).frame_count == 3
    finally:
        reset_request_context(token)


def test_sparse_rgb_depth_compacts_all_media_together(tmp_path):
    root, source, _, _ = build_rgbd_package(tmp_path)
    from hc_data_platform.episode_store.capture_selection import compact_capture_rows
    from hc_data_platform.data_formats.lerobot_layout import DATA_PATH
    import pyarrow as pa
    table = pq.read_table(root/DATA_PATH)
    rows = table.to_pylist()
    for i, row in enumerate(rows):
        row['hc.sample_valid'] = i != 1
        row['source.timestamp_ns'] = 1000000000+i*33_333_333
    pq.write_table(pa.Table.from_pylist(rows), root/DATA_PATH)
    info = json.loads((root/'meta/info.json').read_bytes())
    info['hc.feature_mapping']['source.timestamp_ns'] = 'source.timestamp_ns'
    info['features']['source.timestamp_ns'] = {'dtype': 'int64', 'shape': [1], 'names': None}
    (root/'meta/info.json').write_text(json.dumps(info))
    compact_capture_rows(root)
    assert validate_package(root, source).frame_count == 3

    import av
    import numpy as np
    from hc_data_platform.data_formats.lerobot_layout import VIDEO_PATH
    info = json.loads((root/'meta/info.json').read_bytes())
    assert info['hc.media_frame_count'] == 3
    assert info['hc.source_frame_count'] == 4
    assert info['hc.video_duration_seconds'] == .1
    for key, feature in info['features'].items():
        if feature['dtype'] != 'video':
            continue
        path = root/VIDEO_PATH.format(video_key=key, chunk_index=0, file_index=0)
        with av.open(str(path)) as video:
            frames = list(video.decode(video=0))
            assert len(frames) == 3
            assert [float(f.pts*f.time_base) for f in frames] == pytest.approx([0., 1/30, 2/30])
            if not feature['info'].get('is_depth_map'):
                assert [float(f.to_ndarray(format='rgb24').mean()) for f in frames] == pytest.approx([0.,100.,150.], abs=4)
    retained = pq.read_table(root/DATA_PATH).to_pylist()
    assert [r['hc.source_frame_index'] for r in retained] == [0,2,3]
    assert [r['hc.timestamp_ns'] for r in retained] == [1000000000,1066666666,1099999999]
    assert [r['hc.video_frame_index'] for r in retained] == [0,1,2]
