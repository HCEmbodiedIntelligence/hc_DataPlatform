"""Resample already-associated capture rows for fixed-rate preview/export only.

Original frame times and action identities remain unchanged. Missing grid slots
are explicitly invalid placeholders; an image is never silently held or reused.
"""
from copy import deepcopy

from .alignment import Aligner, target_count, target_time
from .buffers import TimeCache
from .primary_timeline import uses_primary_timeline


def export_rows(source_rows, cfg, start, end):
    if not uses_primary_timeline(cfg['alignment']):
        yield from source_rows
        return
    source_rows = iter(source_rows)
    following = next(source_rows, None)
    if following is None:
        raise ValueError('primary_camera_has_no_recorded_frames')
    origin = following['target_time_ns']
    fps = cfg['dataset']['fps']
    half_period = round(500_000_000 / fps)
    before = None
    used_frame = None
    empty = Aligner(cfg, TimeCache(cfg['buffers']))
    for index in range(target_count(start, end, fps)):
        target = target_time(origin, index, fps)
        while following is not None and following['target_time_ns'] <= target:
            before = following
            following = next(source_rows, None)
        candidates = [row for row in (before, following) if row is not None
                      and abs(row['target_time_ns']-target) <= half_period]
        selected = min(candidates, key=lambda row: (abs(row['target_time_ns']-target),
                                                   row['target_time_ns'])) if candidates else None
        identity = selected['primary_frame'] if selected else None
        if selected is None or identity == used_frame:
            row = empty.row(index, origin, target, deadline=2**63-1, target_ns=target)
            row['invalid_reasons'].append('primary_frame_missing' if selected is None else 'primary_frame_reused')
            row.update(valid=False, export_qualified=False, strict_qualified=False,
                       capture_frame_index=None, timeline_mode='primary_camera')
        else:
            row = deepcopy(selected)
            row['capture_frame_index'] = selected['target_frame_index']
            used_frame = identity
        row.update(export_frame_index=index, export_target_time_ns=target,
                   export_grid_origin_ns=origin, export_grid_error_ns=(
                       selected['target_time_ns']-target if selected else None))
        yield row
