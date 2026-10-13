"""Camera phase, synchronization requirements and missing frames are distinct."""
from copy import deepcopy
import json
import pytest

from hc_data_platform.continuous_recordings.openarm_raw.alignment import Aligner
from hc_data_platform.continuous_recordings.openarm_raw.models import Sample
from hc_data_platform.continuous_recordings.openarm_raw.config import validate
from tests.continuous_recordings.test_exposure_association import example, BASE, GRID


def cameras(skew=6_000_000):
    cfg, cache = example()
    cfg['alignment'].update(timeline_mode='primary_camera', camera_timing_reference='per_camera_rgb',
                            camera_pair_max_skew_ms=2, require_camera_sync=False)
    cfg['sources']['wrist']['rgbd_max_midpoint_skew_ms'] = 2
    cfg['sources']['head'] = {**cfg['sources']['wrist'], 'depth_enabled': False}
    # A secondary camera comes first in config; it must not choose the state clock.
    cfg['sources'] = {'head': cfg['sources'].pop('head'), **cfg['sources']}
    original = cache.values['wrist'][0][2]
    body = original.data()
    body['source_id'] = 'head'
    body['capture_time_ns'] += skew
    body['rgb'] = {**body['rgb'], 'capture_time_ns': body['capture_time_ns'],
                   'exposure_midpoint_ns': body['capture_time_ns']}
    cache.add(Sample('head', 11, body['capture_time_ns'], original.receive_time_ns,
                     original.arrival_time_ns, 7, json.dumps(body).encode()), BASE)
    return validate(cfg), cache


def test_actual_primary_exposure_ignores_record_button_phase_and_interpolates_state():
    cfg, cache = cameras()
    stamp = GRID + 500_000
    row = Aligner(cfg, cache).row(0, BASE, stamp+80_000_000, target_ns=stamp)
    assert row['valid'], row['invalid_reasons']
    assert row['state']['joints']['components']['values'] == pytest.approx([1.05])
    assert row['images']['head']['rgb']['time_error_ns'] == 6_000_000
    assert row['timing_warnings'] == ['camera_pair_skew_exceeded']
    assert not any('grid_error' in reason for reason in row['invalid_reasons'])


def test_strict_sync_is_optional_and_rejects_the_same_six_ms_difference():
    cfg, cache = cameras()
    cfg['alignment']['require_camera_sync'] = True
    row = Aligner(cfg, cache).row(0, BASE, GRID+80_000_000, target_ns=GRID+500_000)
    assert row['invalid_reasons'] == ['camera_pair_skew_exceeded']


def test_missing_secondary_is_not_replaced_by_an_image_beyond_half_a_frame():
    cfg, cache = cameras(18_000_000)
    row = Aligner(cfg, cache).row(0, BASE, GRID+80_000_000, target_ns=GRID+500_000)
    assert 'head:camera_missing' in row['invalid_reasons']
    assert 'head' not in row['images']


@pytest.mark.parametrize('key,value', [('require_camera_sync','false'),
    ('camera_match_max_skew_ms',20), ('timeline_mode','unknown')])
def test_invalid_policy_values_are_rejected(key,value):
    cfg, _ = cameras()
    cfg['alignment'][key] = value
    with pytest.raises(ValueError):
        validate(cfg)


def test_fixed_rate_export_preserves_real_time_and_keeps_a_missing_frame_invalid():
    from hc_data_platform.continuous_recordings.openarm_raw.export_timeline import export_rows
    from hc_data_platform.continuous_recordings.openarm_raw.primary_timeline import frame_identity
    cfg, cache = cameras()
    origin = GRID+500_000
    first = Aligner(cfg, cache).row(0, BASE, origin+80_000_000, target_ns=origin)
    first['primary_frame'] = frame_identity('wrist', first['images']['wrist'])
    following = deepcopy(first)
    following.update(target_frame_index=1, target_time_ns=origin+66_666_667)
    following['primary_frame'].update(source_seq=13, rgb_source_seq=13,
                                      capture_time_ns=origin+66_666_667)
    exported = list(export_rows(iter([first, following]), cfg, BASE, BASE+100_000_000))
    assert len(exported) == 3
    assert exported[0]['target_time_ns'] == origin
    assert exported[0]['export_grid_origin_ns'] == origin
    assert exported[1]['valid'] is False
    assert 'primary_frame_missing' in exported[1]['invalid_reasons']
    assert exported[1]['images'] == {}
    assert exported[2]['primary_frame']['rgb_source_seq'] == 13


@pytest.mark.parametrize('next_seq,dt_ns', [(12, 33_344_000), (13, 66_666_666)])
def test_recorder_primary_identity_and_next_frame_are_required_for_derived_actions(next_seq, dt_ns):
    from hc_data_platform.continuous_recordings.openarm_raw.recorded_actions import apply_recorded_targets
    from hc_data_platform.continuous_recordings.openarm_raw.primary_timeline import frame_identity, PRIMARY_ACTION_POLICY
    cfg, cache = cameras()
    stamp = GRID+500_000
    # No real action source: an optional recorder target may resolve only its action error.
    cache.values['commands'] = []
    row = Aligner(cfg, cache).row(0, BASE, stamp+80_000_000, target_ns=stamp)
    identity = frame_identity('wrist', row['images']['wrist'])
    following = {**identity, 'source_seq': next_seq, 'rgb_source_seq': next_seq,
                 'capture_time_ns': stamp+dt_ns}
    row.update(primary_frame=identity, next_primary_frame=following)
    target = {'schema_version': PRIMARY_ACTION_POLICY, 'source_id': 'commands',
        'action_origin': 'next_state', 'action_stage': 'derived',
        'selection': 'only_if_no_real_source_in_recording', 'clock_epoch': row['clock_epoch'],
        'primary_frame': identity, 'next_primary_frame': following,
        'target_frame_index': 0, 'next_frame_index': 1,
        'target_time_ns': stamp, 'capture_time_ns': stamp,
        'next_target_time_ns': following['capture_time_ns'], 'next_state_time_ns': following['capture_time_ns'],
        'joint_names': ['axis'], 'joint_units': ['rad'],
        'components': {'values': [4.], 'valid': [True], 'source_ns': [following['capture_time_ns']], 'evidence': [{}]}}
    valid = deepcopy(row)
    apply_recorded_targets(valid, {'commands': target}, cfg)
    assert valid['valid'], valid['invalid_reasons']
    assert valid['action']['commands']['action_dt_ns'] == dt_ns
    tampered = deepcopy(target)
    tampered['next_primary_frame']['rgb_source_seq'] = 99
    apply_recorded_targets(row, {'commands': tampered}, cfg)
    assert not row['valid']
    assert 'commands:recorded_action_target_mismatch' in row['invalid_reasons']
