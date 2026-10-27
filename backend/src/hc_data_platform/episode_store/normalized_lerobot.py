"""Exact committed-frame selection for normalized capture collection exports."""

import copy
import io
import json
import shutil
from contextlib import contextmanager
from itertools import groupby
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from PIL import Image

from hc_data_platform.continuous_recordings.package import prepare_rgb_window
from hc_data_platform.data_formats.depth_evidence import file_hash
from hc_data_platform.data_formats.lerobot_format import make_task_table
from hc_data_platform.data_formats.lerobot_layout import DATA_PATH, EPISODES_PATH, VIDEO_PATH
from hc_data_platform.data_formats.video import inspect_depth_video, inspect_video

from .depth_window import prepare_depth_window
from .frame_mapping import package_mapping, replace_column
from .frame_policy import refresh_repetition
from .frame_window import DepthWindow
from .models import FileEntry, canonical
from .normalized_export import frame_selection
from .prepared_assets import PreparedVideo
from .statistics import refresh_numeric_statistics

PROCESSOR = "hc-lerobot-collection/4"
LEGACY_PROCESSOR = "hc-lerobot-collection/3"


class SelectedView:
    def __init__(self, root, parent, frames, segments, selection, operations):
        self.root, self.parent, self.frames = root, parent, frames
        self.segments, self.operations = segments, operations
        self.selection = {
            "annotation_revision": selection.annotation_file.file.revision if selection.annotation_file else None,
            "annotation_sha256": selection.annotation_file.file.content_sha256 if selection.annotation_file else None,
            "parent_frame_indices": [f[1]["parent_frame_index"] for f in frames],
            "task": frames[0][1]["task"],
        }
        self.entries = {e.path: e for e in parent.manifest.files}
        for path in root.rglob("*"):
            if path.is_file():
                rel = path.relative_to(root).as_posix()
                self.entries[rel] = FileEntry(path=rel, size=path.stat().st_size,
                    sha256=file_hash(path), media_type="application/octet-stream")
        self.manifest = SimpleNamespace(files=tuple(self.entries.values()))

    @contextmanager
    def file(self, relative):
        path = self.root / relative
        if path.is_file():
            yield path
        else:
            with self.parent.file(relative) as original:
                yield original

    def document(self, relative):
        with self.file(relative) as path:
            return json.loads(path.read_bytes())


def views(selection, parent_directory, checkpoint, modalities, *, compact_invalid=True):
    parent = selection.package
    parent_entries = {entry.path: entry for entry in parent.manifest.files}
    selected = frame_selection(selection, compact_invalid=compact_invalid)
    with parent.file(DATA_PATH) as path:
        original = pq.read_table(path)
    for _, items in groupby(selected.items(), key=lambda item: item[1]["segment"]):
        frames = list(items)
        indexes = [row["parent_frame_index"] for _, row in frames]
        if not compact_invalid and indexes != list(range(indexes[0], indexes[-1] + 1)):
            raise ValueError("normalized export segment is not contiguous")
        with TemporaryDirectory(prefix="selected-lerobot-", dir=parent_directory) as directory:
            root = Path(directory)
            info = copy.deepcopy(parent.document("meta/info.json"))
            fps, count, task = info["fps"], len(frames), frames[0][1]["task"]
            table = original.take(pa.array(indexes, type=pa.int64()))
            if compact_invalid:
                previous, retained = {}, table.to_pylist()
                for row in retained:
                    refresh_repetition(row, previous)
                for key in table.column_names:
                    if key.startswith("hc.repeated."):
                        table = replace_column(table, key, [r[key] for r in retained], pa.bool_())
            replacements = {"index": list(range(count)), "frame_index": list(range(count)),
                            "timestamp": [i / fps for i in range(count)], "task_index": [0] * count}
            if compact_invalid and "hc.video_frame_index" in table.column_names:
                replacements.update({"hc.video_frame_index": list(range(count)),
                                     "hc.video_timestamp": [i / fps for i in range(count)]})
            segments = []
            if selection.annotation_file is not None:
                segments = [{"subtask_index": 0, "label": task, "start_step": 0, "end_step": count,
                             "parent_start_step": indexes[0], "parent_end_step": indexes[-1] + 1}]
                replacements.update(subtask_index=[0] * count, **{"hc.annotation.excluded": [False] * count})
                if compact_invalid:
                    info["features"].update({
                        "subtask_index": {"dtype": "int64", "shape": [1], "names": None},
                        "hc.annotation.excluded": {"dtype": "bool", "shape": [1], "names": None},
                    })
            for name, values in replacements.items():
                if name in table.column_names:
                    column = pa.array(values, type=table.schema.field(name).type)
                    table = table.set_column(table.column_names.index(name), name, column)
                else:
                    table = table.append_column(name, pa.array(values))
            (root / DATA_PATH).parent.mkdir(parents=True)
            pq.write_table(table, root / DATA_PATH)
            (root / "meta").mkdir(exist_ok=True)
            pq.write_table(make_task_table([task]), root / "meta/tasks.parquet")
            (root / "hc").mkdir()
            (root / "hc/selected_frames.json").write_bytes(canonical(frames))
            if "hc/source_frames.parquet" in parent_entries:
                if compact_invalid:
                    mapping = package_mapping(parent, original).take(pa.array(indexes, type=pa.int64()))
                    mapping = replace_column(mapping, "frame_index", range(count), pa.int64())
                    mapping = replace_column(mapping, "parent_frame_index", indexes, pa.int64())
                    mapping = replace_column(mapping, "timestamp_ns", [stamp for stamp, _ in frames], pa.int64())
                    mapping = replace_column(mapping, "repeated", [canonical({
                        topic: r.get("hc.repeated." + feature, False) for topic, feature in info["hc.feature_mapping"].items()
                    }).decode() for r in retained], pa.string())
                else:
                    with parent.file("hc/source_frames.parquet") as path:
                        mapping = pq.read_table(path).take(pa.array(indexes, type=pa.int64()))
                    mapping = replace_column(mapping, "frame_index", range(count), pa.int64())
                pq.write_table(mapping, root / "hc/source_frames.parquet")
            with parent.file(EPISODES_PATH) as path:
                episode = pq.read_table(path).to_pylist()[0]
            episode.update(length=count, tasks=[task], dataset_from_index=0, dataset_to_index=count)
            (root / EPISODES_PATH).parent.mkdir(parents=True)
            pq.write_table(pa.Table.from_pylist([episode]), root / EPISODES_PATH)
            stats = copy.deepcopy(parent.document("meta/stats.json"))
            depth = parent.document("hc/modalities.json")["depth"]
            operations = []
            window = DepthWindow(indexes[0], indexes[-1] + 1, count, fps, frames[0][0],
                source_frames=tuple(indexes), target_timestamps_ns=tuple(stamp for stamp, _ in frames))
            for key, feature in info["features"].items():
                if feature["dtype"] != "video" or key in depth and modalities == "rgb":
                    continue
                checkpoint()
                relative = VIDEO_PATH.format(video_key=key, chunk_index=0, file_index=0)
                work = root / "work" / key
                work.mkdir(parents=True)
                evidence_root, entries = None, []
                if key in depth:
                    prefix = depth[key]["evidence_path"]
                    evidence_root = work / "parent-evidence"
                    for entry in parent.manifest.files:
                        if entry.path.startswith(prefix + "/"):
                            short = entry.path[len(prefix) + 1:]
                            target = evidence_root / short
                            target.parent.mkdir(parents=True, exist_ok=True)
                            with parent.file(entry.path) as origin:
                                shutil.copyfile(origin, target)
                            entries.append(entry.model_copy(update={"path": short}))
                with parent.file(relative) as path:
                    entry = parent_entries[relative]
                    media = PreparedVideo(path, entry.sha256, entry.size, feature, evidence_root, tuple(entries))
                    result = (prepare_depth_window(media, window, work / "selected", preserve_parent_times=True)
                              if key in depth else prepare_rgb_window(media, window, work / "rgb.mp4"))
                    target = root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(result.path, target)
                    if key in depth:
                        prefix = root / depth[key]["evidence_path"]
                        for item in result.evidence_files:
                            (prefix / item.path).parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(result.evidence_root / item.path, prefix / item.path)
                        def masks(path=prefix / "validity/flags.parquet"):
                            for row in pq.read_table(path).to_pylist():
                                with Image.open(io.BytesIO(row["flags_png"])) as image:
                                    yield np.asarray(image) & 1 != 0
                        current, statistics = inspect_depth_video(target, frame_count=count, fps=fps,
                            info=result.feature["info"], masks=masks())
                    else:
                        current, statistics = inspect_video(target, frame_count=count, fps=fps)
                    info["features"][key] = current
                    stats.pop(key, None)
                    if statistics:
                        stats[key] = statistics
                    operations.append({"feature": key, "transcoded": result is not media,
                        "operation": "byte_copy" if result is media else "lossless_code_selection" if key in depth else "rgb_clip_encoding",
                        "requantized": False})
            shutil.rmtree(root / "work", ignore_errors=True)
            info.update(total_frames=count, total_tasks=1, **{"hc.media_frame_count": count})
            if compact_invalid:
                info["hc.video_duration_seconds"] = count / fps
                info["hc.duration_seconds"] = (frames[-1][0] - frames[0][0]) / 1e9 + 1 / fps
                info["hc.time_origin_ns"] = frames[0][0]
            (root / "meta/info.json").write_bytes(canonical(info))
            (root / "meta/stats.json").write_bytes(canonical(stats))
            if compact_invalid:
                refresh_numeric_statistics(root)
            yield SelectedView(root, parent, frames, segments, selection, operations)
