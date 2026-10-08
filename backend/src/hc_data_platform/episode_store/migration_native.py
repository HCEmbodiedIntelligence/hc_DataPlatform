"""Prepare historical native RGB assets from scoped, frozen database receipts.

Complete streams are reused; shared native windows preserve every decoded RGB
pixel and original frame/time reference. Source depth requires its explicit
evidence adapter. Original objects and historical rows remain intact.
"""

import hashlib
import json
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from psycopg import sql

from hc_data_platform.aligned_media.models import (
    AlignedMediaFrameReferenceV1,
    OriginalVideoReferenceV1,
)
from hc_data_platform.data_formats.feature_names import feature_names
from hc_data_platform.data_formats.video import inspect_video
from hc_data_platform.ingest.raw_sources import RawSource
from hc_data_platform.lerobot_imports.committed import read_committed_manifest
from hc_data_platform.lerobot_imports.package import raw_files
from hc_data_platform.lerobot_imports.reader import episode_metadata, format_path, safe_relative

from .bundle_assets import BundleAssetsLoader
from .bundle_models import FrozenObject
from .migration_conversion import freeze_plan, legacy_steps, pinned_legacy
from .migration_inventory import FORMAT
from .migration_objects import _ManifestBytes, validate_raw_file_set
from .migration_rgb import prepare_rgb_selection
from .models import digest
from .postgres import PackageConflict, assert_scope
from .prepared_assets import PreparedAssets, PreparedVideo
from .validation import parquet_rows


class NativeMigrationAssets:
    def __init__(self, connections, storage, legacy, directory, *, max_frames=20000):
        if type(max_frames) is not int or not 1 <= max_frames <= 100000:
            raise ValueError("migration frame limit must be 1..100000")
        self.connections, self.storage, self.legacy = connections, storage, legacy
        self.directory, self.max_frames = Path(directory), max_frames
        self.copy = BundleAssetsLoader(storage, None, self.directory)._copy

    def _current(self, inventory, identity, table, fields):
        """A saved plan is not authority: recheck the exact scoped persisted row."""
        entries = [
            r
            for r in inventory["inventory"]["tables"][table]
            if all(r["document"].get(k) == v for k, v in fields.items())
        ]
        if len(entries) != 1:
            raise PackageConflict("historical source binding is absent or ambiguous")
        query_fields = dict(
            zip(("organization_id", "project_id", "region_code"), identity.scope, strict=True)
        )
        query_fields.update(fields)
        with self.connections() as connection:
            records = connection.execute(
                sql.SQL("SELECT to_jsonb(r) FROM {} r WHERE {} LIMIT 2").format(
                    sql.Identifier(*table.split(".")),
                    sql.SQL(" AND ").join(
                        sql.SQL("r.{}=%s").format(sql.Identifier(k)) for k in query_fields
                    ),
                ),
                tuple(query_fields.values()),
            ).fetchall()
        if len(records) != 1 or digest(records[0][0]) != entries[0]["row_sha256"]:
            raise PackageConflict("historical source row changed since inventory")
        return records[0][0]

    def _manifest(self, raw, source_sha256):
        head = self.storage.head(raw.manifest_key)
        if head is None or not 0 < head.size <= 16 * 1024**2:
            raise PackageConflict("historical native manifest is missing or too large")
        body = bytearray()
        for block in self.storage.read_chunks(raw.manifest_key):
            body.extend(block)
            if len(body) > head.size:
                raise PackageConflict("historical native manifest exceeded its size")
        if len(body) != head.size or hashlib.sha256(body).hexdigest() != source_sha256:
            raise PackageConflict("historical native manifest differs from Lance lineage")
        manifest, _ = read_committed_manifest(_ManifestBytes(raw.manifest_key, body), raw)
        if any(
            manifest.get(k) != getattr(raw, k)
            for k in (
                "organization_id",
                "project_id",
                "region_code",
                "dataset_id",
                "collection_task_id",
                "robot_id",
            )
        ):
            raise PermissionError("historical native manifest scope differs")
        if json.loads(body)["schema_version"] == "raw-upload-manifest/v1":
            validate_raw_file_set(raw, manifest)
        files = raw_files(raw, manifest)
        paths = [safe_relative(item["path"]) for item in manifest["files"]]
        if (
            len(set(paths)) != len(paths)
            or len(paths) != raw.file_count
            or sum(item["size"] for item in manifest["files"]) != raw.total_bytes
            or any(
                not f.object.key.startswith(raw.storage_prefix.rstrip("/") + "/")
                for f in files.values()
            )
        ):
            raise PackageConflict("historical native file inventory differs")
        return files

    @contextmanager
    def load(self, inventory, reference):
        identity = reference.identity
        assert_scope(identity)
        payload = inventory["inventory"]
        if (
            inventory["inventory_sha256"] != digest(payload)
            or payload.get("format") != FORMAT
            or payload["scope"]
            != {
                k: getattr(identity, k)
                for k in ("organization_id", "project_id", "region_code", "dataset_id")
            }
        ):
            raise PermissionError("historical native inventory hash/scope differs")
        if reference.frame_count > self.max_frames:
            raise ValueError("historical Episode exceeds the explicit frame limit")
        _, lineage = pinned_legacy(self.legacy, reference)
        schema = self.legacy.schema_for(identity.project_id, identity.dataset_id)
        if schema is None or schema.schema_snapshot_id != reference.schema_snapshot_id:
            raise PackageConflict("historical native schema is unavailable")
        rows = list(legacy_steps(self.legacy, reference))
        first = rows[0]
        source = first.modalities.get("/metadata/source", {})
        if source.get("source_format") != "lerobot_v3" or not source.get("raw_upload_id"):
            raise ValueError("historical Episode requires a different source preparation adapter")
        if any(row.modalities.get("/metadata/source") != source for row in rows):
            raise PackageConflict("historical native source/task changes within Episode")
        record = self._current(
            inventory,
            identity,
            "ingest.raw_sources",
            {
                "dataset_id": identity.dataset_id,
                "raw_source_id": source["raw_upload_id"],
            },
        )
        raw = RawSource.model_validate(
            {k: record[k] for k in RawSource.model_fields if k in record}
        )
        if (
            raw.raw_status.value != "COMMITTED"
            or raw.source_format.value != "LEROBOT_V3"
            or raw.manifest_key != source.get("raw_manifest_key")
        ):
            raise PackageConflict("historical native source is not a committed bound manifest")
        episode = self._current(
            inventory,
            identity,
            "ingest.raw_source_episodes",
            {
                "raw_source_id": raw.raw_source_id,
                "episode_id": identity.rollout_id,
            },
        )
        if (
            episode["source_episode_index"] != source.get("episode_index")
            or episode["status"] != "READY"
            or episode["frame_count"] != reference.frame_count
        ):
            raise PackageConflict("historical native Episode mapping differs")
        revision = self._current(
            inventory,
            identity,
            "dataset_registry.dataset_version_episode_revisions",
            {
                "dataset_id": identity.dataset_id,
                "episode_id": identity.episode_id,
                "version_id": f"version_lance_{reference.dataset_version}",
            },
        )["revision_document"]
        bindings = {
            s["channel_path"]: s["aligned_media_binding"]
            for s in revision["streams"]
            if s.get("aligned_media_binding") is not None
        }
        cameras = {
            name
            for name, value in first.modalities.items()
            if isinstance(value, dict)
            and value.get("schema_version") == "aligned-media-frame-ref/v1"
        }
        if set(bindings) != cameras:
            raise PackageConflict("historical camera inventory differs from persisted revision")
        mapping = feature_names(sorted(first.modalities), camera_topics=sorted(cameras))
        files = self._manifest(raw, lineage.source_sha256)
        self.directory.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="native-migration-", dir=self.directory) as temporary:
            root = Path(temporary)

            def local(relative, *, max_bytes=64 * 1024**2):
                file = files[relative]
                if file.object.size > max_bytes:
                    raise ValueError("native migration input exceeds its explicit byte limit")
                target = root / "source" / relative
                if not target.exists():
                    self.copy(file.object, target)
                return target

            info = json.loads(local("meta/info.json").read_bytes())
            declared = {k: v for k, v in info["features"].items() if v.get("dtype") == "video"}
            if any(
                v.get("info", {}).get("is_depth_map")
                or v.get("info", {}).get("video.is_depth_map")
                or v.get("shape", [None])[-1] == 1
                for v in declared.values()
            ):
                raise ValueError("historical depth requires profile, masks and source evidence")
            if set(declared) != {mapping[name] for name in cameras}:
                raise PackageConflict("migration cannot omit or invent native video features")
            tasks = list(parquet_rows(local("meta/tasks.parquet")))
            selected_tasks = [t for t in tasks if t.get("task_index") == source.get("task_index")]
            if len(selected_tasks) != 1:
                raise PackageConflict("native task index has no unique original task")
            task = selected_tasks[0].get("task", selected_tasks[0].get("__index_level_0__"))
            if not isinstance(task, str) or not task:
                raise PackageConflict("native task text is unavailable")
            metadata = {
                "source": {**source, "raw_manifest_sha256": lineage.source_sha256},
                "robot_type": info.get("robot_type", "unknown"),
                "task": task,
                "camera_topics": sorted(cameras),
            }
            for name in ("modalities", "calibration"):
                if f"hc/{name}.json" in files:
                    metadata[name] = json.loads(local(f"hc/{name}.json").read_bytes())
            if metadata.get("modalities", {}).get("depth"):
                raise ValueError("historical depth declaration requires its evidence adapter")
            videos = {}
            native_episode, native_rows = None, None
            for camera in sorted(cameras):
                binding = bindings[camera]
                artifact = self._current(
                    inventory,
                    identity,
                    "aligned_media.artifacts",
                    {
                        "dataset_id": identity.dataset_id,
                        "artifact_id": binding["artifact_id"],
                    },
                )
                refs = [
                    AlignedMediaFrameReferenceV1.model_validate(r.modalities[camera]) for r in rows
                ]
                timeline = self._binding(artifact, binding, rows, reference, lineage, schema)
                if timeline.get("original_source"):
                    if native_episode is None:
                        native_episode, native_rows = self._native_rows(
                            files, local, root / "source", info, source["episode_index"]
                        )
                    original, media = self._original_media(
                        artifact,
                        refs,
                        rows,
                        files,
                        info,
                        native_episode,
                        native_rows,
                        mapping[camera],
                    )
                    path = root / f"original-{len(videos)}.mp4"
                    self.copy(media, path)
                    prepared = PreparedVideo(
                        path,
                        media.sha256,
                        media.size,
                        {
                            "shape": [original.height, original.width, 3],
                            "info": {"video.fps": original.fps},
                        },
                    )
                    selected, report = prepare_rgb_selection(
                        prepared,
                        [r.frame_index for r in refs],
                        artifact["fps"],
                        root / f"video-{len(videos)}.mp4",
                    )
                    report.update(
                        {
                            "original_reference": original.model_dump(mode="json"),
                            "source_references_sha256": digest(
                                [r.model_dump(mode="json") for r in refs]
                            ),
                            "source_timestamp_contract": "native-parquet-float32-seconds/1",
                        }
                    )
                    metadata["source"].setdefault("media_preparation", {})[mapping[camera]] = report
                    videos[mapping[camera]] = selected
                else:
                    media = self._whole_media(
                        artifact, binding, refs, rows, reference, lineage, schema
                    )
                    path = root / f"video-{len(videos)}.mp4"
                    self.copy(media, path)
                    self._video_times(path, refs, artifact)
                    feature, _ = inspect_video(path, frame_count=len(rows), fps=artifact["fps"])
                    videos[mapping[camera]] = PreparedVideo(path, media.sha256, media.size, feature)
            assets = PreparedAssets(identity, "historical-native", videos)
            plan = freeze_plan(inventory, reference, self.legacy, schema, assets, metadata=metadata)
            yield plan, assets

    @staticmethod
    def _binding(a, binding, rows, reference, lineage, schema):
        timeline = a.get("timeline_json") or {}
        if (
            a["status"] != "READY"
            or a["retired_at"] is not None
            or a["deleted_at"] is not None
            or a["source_sha256"] != lineage.source_sha256
            or a["rollout_id"] != reference.identity.rollout_id
            or a["dataset_version"] != binding["dataset_version"]
            or a["camera_id"] != binding["camera_id"]
            or binding["rollout_id"] != reference.identity.rollout_id
            or binding["start_step"] != 0
            or binding["end_step"] != reference.frame_count
            or a["frame_count"] != reference.frame_count
            or a["fps"] != schema.frequency_hz
            or binding["fps"] != a["fps"]
        ):
            raise PackageConflict("historical media differs from the scoped whole Episode")
        if (
            timeline.get("frame_count") != len(rows)
            or timeline.get("fps") != a["fps"]
            or int(timeline.get("start_timestamp_ns", -1)) != rows[0].timestamp_ns
            or timeline.get("first_step") != 0
        ):
            raise PackageConflict("historical media timeline differs")
        return timeline

    @staticmethod
    def _whole_media(a, binding, refs, rows, reference, lineage, schema):
        timeline = NativeMigrationAssets._binding(a, binding, rows, reference, lineage, schema)
        if timeline.get("original_source"):
            raise ValueError("shared/original media requires explicit window preparation")
        for index, (ref, row) in enumerate(zip(refs, rows, strict=True)):
            if (
                ref.artifact_id != a["artifact_id"]
                or ref.object_key != a["media_object_key"]
                or ref.camera_id != a["camera_id"]
                or ref.alignment_version != a["alignment_version"]
                or ref.frame_index != index
                or ref.timestamp_ns != row.timestamp_ns
                or Fraction(ref.pts * ref.pts_time_base_numerator, ref.pts_time_base_denominator)
                != Fraction(index, a["fps"])
            ):
                raise PackageConflict("historical camera source-frame/PTS binding differs")
        objects = [o for o in a["object_manifest"] if o["key"] == a["media_object_key"]]
        if len(objects) != 1 or objects[0]["sha256"] != a["content_sha256"]:
            raise PackageConflict("historical media primary object is not in its receipt")
        return FrozenObject(objects[0]["key"], objects[0]["size"], objects[0]["sha256"])

    @staticmethod
    def _native_rows(files, local, root, info, index):
        import math

        import pyarrow as pa
        import pyarrow.parquet as pq

        for relative in files:
            if relative.startswith("meta/episodes/") and relative.endswith(".parquet"):
                local(relative)
        episode = episode_metadata(root, info)[index]
        relative = format_path(
            info["data_path"],
            chunk_index=episode["data/chunk_index"],
            file_index=episode["data/file_index"],
        )
        data = pq.ParquetFile(local(relative, max_bytes=1024**3))
        if data.schema_arrow.field("timestamp").type != pa.float32():
            raise PackageConflict("historical native timestamp representation is unsupported")
        selected = {}
        columns = ["episode_index", "frame_index", "index", "timestamp", "task_index"]
        count = 0
        for batch in data.iter_batches(batch_size=128, columns=columns):
            for row in batch.to_pylist():
                if row["episode_index"] != index:
                    continue
                timestamp = row["timestamp"]
                if (
                    count >= 100000
                    or row["frame_index"] != count
                    or row["index"] != episode["dataset_from_index"] + count
                    or not isinstance(timestamp, (int, float))
                    or not math.isfinite(timestamp)
                    or timestamp < 0
                ):
                    raise PackageConflict("historical native source row index/time differs")
                stamp = round(timestamp * 1000000000)
                if stamp in selected:
                    raise PackageConflict("historical native source timestamps are ambiguous")
                selected[stamp] = row
                count += 1
        if count != episode["length"]:
            raise PackageConflict("historical native source Episode rows are incomplete")
        return episode, selected

    @staticmethod
    def _original_media(a, refs, rows, files, info, episode, source_rows, feature):
        original = OriginalVideoReferenceV1.model_validate(a["timeline_json"]["original_source"])
        prefix = "videos/" + feature
        relative = format_path(
            info["video_path"],
            video_key=feature,
            chunk_index=episode[prefix + "/chunk_index"],
            file_index=episode[prefix + "/file_index"],
        )
        frozen = files[relative].object
        if (
            (original.object_key, original.size_bytes, original.content_sha256)
            != (frozen.key, frozen.size, frozen.sha256)
            or original.object_key != a["media_object_key"]
            or original.content_sha256 != a["content_sha256"]
            or original.start_seconds != episode[prefix + "/from_timestamp"]
            or original.end_seconds != episode[prefix + "/to_timestamp"]
            or original.fps != info["fps"]
            or [original.height, original.width, 3] != info["features"][feature]["shape"]
        ):
            raise PackageConflict(
                "historical original video differs from the native manifest/window"
            )
        offset_ns = round(original.start_seconds * 1000000000)
        for ref, row in zip(refs, rows, strict=True):
            if len(ref.source_timestamps_ns) != 1:
                raise PackageConflict("historical native video has no unique source timestamp")
            stamp = ref.source_timestamps_ns[0]
            expected_pts = offset_ns + stamp
            if (
                stamp not in source_rows
                or ref.artifact_id != a["artifact_id"]
                or ref.object_key != original.object_key
                or ref.camera_id != a["camera_id"]
                or ref.alignment_version != a["alignment_version"]
                or ref.timestamp_ns != row.timestamp_ns
                or Fraction(ref.pts * ref.pts_time_base_numerator, ref.pts_time_base_denominator)
                != Fraction(expected_pts, 1000000000)
                or ref.frame_index != round(expected_pts * original.fps / 1000000000)
            ):
                raise PackageConflict(
                    "historical original frame/time differs from native source rows"
                )
        return original, frozen

    @staticmethod
    def _video_times(path, refs, artifact):
        import av

        with av.open(str(path)) as container:
            if len(container.streams.video) != 1 or container.streams.audio:
                raise PackageConflict("historical canonical video must have one silent stream")
            count = 0
            for frame in container.decode(video=0):
                if (
                    count >= len(refs)
                    or frame.pts is None
                    or frame.pts * frame.time_base != Fraction(count, artifact["fps"])
                    or (frame.width, frame.height)
                    != (artifact["media_width"], artifact["media_height"])
                    or frame.format.name.startswith("gray")
                ):
                    raise PackageConflict(
                        "historical decoded video time/shape/RGB contract differs"
                    )
                count += 1
            if count != len(refs):
                raise PackageConflict("historical decoded video frame count differs")
