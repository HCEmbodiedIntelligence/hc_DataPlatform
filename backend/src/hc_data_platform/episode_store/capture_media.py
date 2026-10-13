"""Select exactly the retained capture rows in every video, using a compact CFR clock."""
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import io
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

import av
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from PIL import Image

from hc_data_platform.data_formats.depth_evidence import file_hash
from hc_data_platform.data_formats.lerobot_layout import DATA_PATH, EPISODES_PATH, VIDEO_PATH
from hc_data_platform.data_formats.video import inspect_video, inspect_depth_video
from .depth_window import prepare_depth_window
from .models import FileEntry, canonical
from .prepared_assets import PreparedVideo
from .validation import parquet_rows

MEDIA_POLICY = 'selected-rows-cfr/v1'


@dataclass(frozen=True)
class CaptureDepthWindow:
    """Nonuniform source selection; exposure/association times stay unchanged."""
    indices: tuple[int, ...]
    targets: tuple[int, ...]
    frequency_hz: float
    source_start_offset_ns: None = None

    @property
    def first_source_frame(self):
        return self.indices[0]

    @property
    def stop_source_frame(self):
        return self.indices[-1] + 1

    @property
    def frame_count(self):
        return len(self.indices)

    def source_frame(self, index, source_fps):
        return self.indices[index]

    def target_time(self, index):
        return self.targets[index]


def select_rgb(source, destination, indices, fps):
    """Losslessly re-encode decoded YUV planes and verify selected pixels exactly."""
    expected = hashlib.sha256()
    rate = Fraction(str(fps))
    count = 0
    with av.open(str(source)) as incoming, av.open(str(destination), 'w', options={'movflags': '+faststart'}) as output:
        original = incoming.streams.video[0]
        encoder = output.add_stream('libx264', rate=rate)
        encoder.width, encoder.height = original.width, original.height
        encoder.pix_fmt = 'yuv420p'
        encoder.options = {'crf': '0', 'preset': 'fast', 'threads': '1', 'bf': '0', 'g': '30'}
        for index, frame in enumerate(incoming.decode(original)):
            if count == len(indices):
                break
            if index != indices[count]:
                continue
            if frame.format.name != 'yuv420p':
                raise ValueError('capture RGB selection requires decoded yuv420p')
            expected.update(frame.to_ndarray().tobytes())
            frame.pts, frame.time_base = count, 1 / rate
            for packet in encoder.encode(frame):
                output.mux(packet)
            count += 1
        for packet in encoder.encode():
            output.mux(packet)
    observed = hashlib.sha256()
    decoded = 0
    with av.open(str(destination)) as output:
        for frame in output.decode(video=0):
            if frame.pts is None or abs(float(frame.pts * frame.time_base) - decoded/fps) > 1e-6:
                raise ValueError('selected RGB timeline mismatch')
            observed.update(frame.to_ndarray().tobytes())
            decoded += 1
    if count != len(indices) or decoded != count or expected.digest() != observed.digest():
        raise ValueError('selected RGB frames changed during encoding')


def compact_capture_media(root: Path):
    """Upgrade a selected-row package in an unpublished attempt directory only."""
    info = json.loads((root/'meta/info.json').read_bytes())
    if info.get('hc.media_selection') == MEDIA_POLICY:
        raise ValueError('capture media already compacted')
    parquet = pq.ParquetFile(root/DATA_PATH)
    addresses = parquet.read(columns=['hc.video_frame_index', 'hc.alignment_timestamp_ns']).to_pydict()
    indices = tuple(addresses['hc.video_frame_index'])
    targets = tuple(addresses['hc.alignment_timestamp_ns'])
    if not indices or any(b <= a for a, b in zip(indices, indices[1:])):
        raise ValueError('capture media requires increasing selected indices')
    stats = json.loads((root/'meta/stats.json').read_bytes())
    modalities = json.loads((root/'hc/modalities.json').read_bytes())
    fps = info['fps']
    media_evidence = {}
    with TemporaryDirectory(prefix='capture-media-', dir=root.parent) as temporary:
        work = Path(temporary)
        for key, feature in list(info['features'].items()):
            if feature['dtype'] != 'video':
                continue
            path = root/VIDEO_PATH.format(video_key=key, chunk_index=0, file_index=0)
            parent_hash = file_hash(path)
            if feature.get('info', {}).get('is_depth_map'):
                evidence = root/modalities['depth'][key]['evidence_path']
                entries = tuple(FileEntry(path=p.relative_to(evidence).as_posix(), size=p.stat().st_size,
                    sha256=file_hash(p), media_type='application/octet-stream')
                    for p in sorted(evidence.rglob('*')) if p.is_file())
                prepared = PreparedVideo(path, parent_hash, path.stat().st_size, feature, evidence, entries)
                selected = prepare_depth_window(prepared, CaptureDepthWindow(indices, targets, fps),
                    work/key, preserve_association_reference=True)
                if selected.path != path:
                    shutil.copyfile(selected.path, path)
                    for entry in selected.evidence_files:
                        target = evidence/entry.path
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(selected.evidence_root/entry.path, target)
                def masks():
                    for row in parquet_rows(evidence/'validity/flags.parquet'):
                        with Image.open(io.BytesIO(row['flags_png'])) as image:
                            yield np.asarray(image) & 1 != 0
                selected_feature, statistics = inspect_depth_video(path, len(indices), fps,
                    feature['info'], masks=masks())
            else:
                output = work/(key+'.mp4')
                select_rgb(path, output, indices, fps)
                shutil.copyfile(output, path)
                selected_feature, statistics = inspect_video(path, frame_count=len(indices), fps=fps)
            info['features'][key] = selected_feature
            if statistics:
                stats[key] = statistics
            else:
                stats.pop(key, None)
            media_evidence[key] = {'source_video_sha256': parent_hash, 'selected_video_sha256': file_hash(path)}
    schema = parquet.schema_arrow.append(pa.field('hc.source_frame_index', pa.int64()))
    temporary_data = (root/DATA_PATH).with_suffix('.selected.parquet')
    count = 0
    with pq.ParquetWriter(temporary_data, schema) as output:
        for batch in parquet.iter_batches(batch_size=128):
            rows = batch.to_pylist()
            for row in rows:
                row['hc.source_frame_index'] = row['hc.video_frame_index']
                row['hc.video_frame_index'] = count
                row['hc.video_timestamp'] = count/fps
                count += 1
            output.write_table(pa.Table.from_pylist(rows, schema=schema))
    temporary_data.replace(root/DATA_PATH)
    info['features']['hc.source_frame_index'] = {'dtype': 'int64', 'shape': [1], 'names': None}
    info['hc.source_frame_count'] = info['hc.media_frame_count']
    info['hc.media_frame_count'] = count
    info['hc.media_selection'] = MEDIA_POLICY
    info['hc.video_duration_seconds'] = count/fps
    episode = pq.read_table(root/EPISODES_PATH).to_pylist()[0]
    for key in list(episode):
        if key.startswith('stats/'):
            del episode[key]
        elif key.startswith('videos/') and key.endswith('/to_timestamp'):
            episode[key] = count/fps
    for key, statistics in stats.items():
        for name, value in statistics.items():
            episode[f'stats/{key}/{name}'] = value
    pq.write_table(pa.Table.from_pylist([episode]), root/EPISODES_PATH)
    selection = json.loads((root/'hc/training_selection.json').read_bytes())
    selection.update(media_policy=MEDIA_POLICY, media=media_evidence,
        video_duration_seconds=count/fps, source_index_column='hc.source_frame_index')
    conversion = json.loads((root/'hc/conversion_report.json').read_bytes())
    conversion['capture_media_selection'] = {'policy': MEDIA_POLICY, 'media': media_evidence,
        'operation': 'lossless_decoded_frame_selection', 'requantized': False}
    conversion['timestamp_policy'] = 'actual capture timestamps; separate dense CFR video clock'
    for relative, document in [('hc/conversion_report.json', conversion), ('meta/info.json', info), ('meta/stats.json', stats),
                               ('hc/training_selection.json', selection)]:
        (root/relative).write_bytes(canonical(document))
