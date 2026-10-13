"""Remove unavailable capture rows as whole observations, keeping media addressing explicit."""
from pathlib import Path
import json
from collections import Counter

import pyarrow as pa
import pyarrow.parquet as pq

from hc_data_platform.data_formats.lerobot_layout import DATA_PATH, EPISODES_PATH
from .models import canonical

POLICY = 'drop_invalid_rows_preserve_source_time/v1'
ARCHIVE_PATH = 'hc/recording/aligned_rows.parquet'


def compact_capture_rows(root: Path):
    """Drop complete samples, preserve capture time, and compact all media together."""
    info = json.loads((root / 'meta/info.json').read_bytes())
    mapping = info['hc.feature_mapping']
    source_key = mapping.get('source.timestamp_ns')
    if source_key is None:
        raise ValueError('capture selection requires actual source timestamps')
    archive = root / ARCHIVE_PATH
    archive.parent.mkdir(parents=True, exist_ok=True)
    (root / DATA_PATH).rename(archive)
    source = pq.ParquetFile(archive)
    schema = source.schema_arrow.append(pa.field('hc.video_frame_index', pa.int64())).append(
        pa.field('hc.video_timestamp', pa.float64())).append(pa.field('hc.alignment_timestamp_ns', pa.int64()))
    schema = schema.set(schema.get_field_index('timestamp'), pa.field('timestamp', pa.float64()))
    previous = origin = None
    selected = []
    invalid_modalities = Counter()
    count = 0
    # Imported lazily to avoid a module initialization cycle.
    from .writer import Moments
    moments = {key: Moments() for key, feature in info['features'].items()
               if feature['dtype'] not in {'string', 'binary', 'video'}}
    with pq.ParquetWriter(root / DATA_PATH, schema) as output:
        for batch in source.iter_batches(batch_size=1024):
            rows = []
            for row in batch.to_pylist():
                if not row['hc.sample_valid']:
                    invalid_modalities.update(k for k, value in row.items() if k.startswith('hc.valid.') and not value)
                    continue
                stamp = row[source_key]
                if type(stamp) is not int or previous is not None and stamp <= previous:
                    raise ValueError('retained source timestamps must strictly increase')
                if origin is None:
                    origin = stamp
                previous = stamp
                selected.append(row['frame_index'])
                row['hc.video_frame_index'] = row['frame_index']
                row['hc.video_timestamp'] = row['frame_index'] / info['fps']
                row['hc.alignment_timestamp_ns'] = row['hc.timestamp_ns']
                row['hc.timestamp_ns'] = stamp
                row['timestamp'] = (stamp-origin)/1e9
                row['index'] = row['frame_index'] = count
                count += 1
                rows.append(row)
            if rows:
                output.write_table(pa.Table.from_pylist(rows, schema=schema))
                for key, moment in moments.items():
                    moment.add([row[key] for row in rows])
    if count == 0:
        raise ValueError(f'capture selection contains no usable observations: {dict(invalid_modalities)}')
    info['hc.media_frame_count'] = info['total_frames']
    info['total_frames'] = count
    info['hc.time_origin_ns'] = origin
    info['hc.duration_seconds'] = (previous-origin)/1e9 + 1/info['fps']
    info['hc.training_policy'] = POLICY
    info['features']['timestamp']['dtype'] = 'float64'
    for key, dtype in [('hc.video_frame_index', 'int64'), ('hc.video_timestamp', 'float64'),
                       ('hc.alignment_timestamp_ns', 'int64')]:
        info['features'][key] = {'dtype': dtype, 'shape': [1], 'names': None}
    stats = json.loads((root / 'meta/stats.json').read_bytes())
    stats.update({key: moment.document() for key, moment in moments.items()})
    episode = pq.read_table(root / EPISODES_PATH).to_pylist()[0]
    episode['length'] = episode['dataset_to_index'] = count
    for key, value in stats.items():
        for field, number in value.items():
            episode[f'stats/{key}/{field}'] = number
    pq.write_table(pa.Table.from_pylist([episode]), root / EPISODES_PATH)
    for name, value in [('meta/info.json', info), ('meta/stats.json', stats),
                        ('hc/training_selection.json', {
                            'schema_version': 'hc-training-selection/v1', 'policy': POLICY,
                            'source_row_count': info['hc.media_frame_count'],
                            'row_count': count, 'selected_frame_indices': selected,
                            'source_time_origin_ns': origin,
                            'media_timestamp_column': 'hc.video_timestamp'})]:
        (root / name).write_bytes(canonical(value))

    from .capture_media import compact_capture_media
    compact_capture_media(root)


def validate_capture_selection(root, info):
    """Prove selection removed only invalid rows and never shifted state/action."""
    from itertools import zip_longest
    archive = pq.ParquetFile(root/ARCHIVE_PATH)
    data = pq.ParquetFile(root/DATA_PATH)
    compacted = info.get('hc.media_selection') == 'selected-rows-cfr/v1'
    if archive.metadata.num_rows != info.get('hc.source_frame_count', info['hc.media_frame_count']):
        raise ValueError('capture archive count differs from declared media grid')
    source_rows = (row for batch in archive.iter_batches(batch_size=1024)
                   for row in batch.to_pylist() if row['hc.sample_valid'])
    selected_rows = (row for batch in data.iter_batches(batch_size=1024) for row in batch.to_pylist())
    source_key = info['hc.feature_mapping']['source.timestamp_ns']
    for index, (before, after) in enumerate(zip_longest(source_rows, selected_rows)):
        if before is None or after is None:
            raise ValueError('capture selection omitted or invented usable rows')
        if (after['hc.video_frame_index'] != (index if compacted else before['frame_index'])
                or compacted and after['hc.source_frame_index'] != before['frame_index']
                or after['hc.alignment_timestamp_ns'] != before['hc.timestamp_ns']
                or after['hc.timestamp_ns'] != before[source_key]):
            raise ValueError('capture selection changed source time or media address')
        for key, value in before.items():
            if key not in {'index', 'frame_index', 'timestamp', 'hc.timestamp_ns'} and after[key] != value:
                raise ValueError(f'capture selection shifted or changed a source value: {key}')
