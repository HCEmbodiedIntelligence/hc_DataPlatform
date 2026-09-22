"""Read original files and episode offsets without decoding or copying videos."""

from __future__ import annotations

import io
import json
import math
from datetime import datetime, timedelta
from threading import BoundedSemaphore
from typing import Any, cast

from pydantic import BaseModel

from hc_data_platform.core.errors import problem
from hc_data_platform.ingest.models import utc_now
from hc_data_platform.ingest.ports import ObjectStoragePort
from hc_data_platform.ingest.raw_sources import (
    RawSource,
    RawSourceFormat,
    RawSourceRepositoryPort,
    RawSourceStatus,
)

from .service import read_bounded

_INDEX_SLOTS = BoundedSemaphore(2)
_MAX_MANIFEST_BYTES = 8 * 1024**2
_MAX_METADATA_READ = 32 * 1024**2


class OriginalFile(BaseModel):
    path: str
    size: int
    sha256: str


class OriginalFilePage(BaseModel):
    import_id: str
    source_format: str
    episode_count: int
    files: list[OriginalFile]
    total: int
    offset: int


class OriginalFileGrant(BaseModel):
    path: str
    url: str
    expires_at: datetime


class OriginalVideo(BaseModel):
    camera_id: str
    path: str
    start_seconds: float
    end_seconds: float


class OriginalEpisode(BaseModel):
    episode_index: int
    frame_count: int
    fps: float
    duration_ns: str
    videos: list[OriginalVideo]


class _RangeReader(io.RawIOBase):
    """Seekable Parquet input; no whole-shard download or persistent cache."""

    def __init__(self, storage: ObjectStoragePort, key: str, size: int) -> None:
        super().__init__()
        self.storage, self.key, self.size, self.position = storage, key, size, 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = 0) -> int:
        position = offset + (self.position if whence == 1 else self.size if whence == 2 else 0)
        if whence not in {0, 1, 2} or position < 0:
            raise ValueError("invalid Parquet byte position")
        self.position = position
        return position

    def read(self, size: int = -1) -> bytes:
        end = self.size if size < 0 else min(self.size, self.position + size)
        if end <= self.position:
            return b""
        if end - self.position > _MAX_METADATA_READ:
            raise problem(
                status=413,
                code="RAW_INDEX_READ_TOO_LARGE",
                title="元数据读取超过预览上限",
                detail="原始文件已保留，可直接下载；该分片需要离线建立索引。",
            )
        data = self.storage.read_range(self.key, self.position, end)
        self.position += len(data)
        return data


class OriginalSourceBrowser:
    def __init__(self, storage: ObjectStoragePort, repository: RawSourceRepositoryPort) -> None:
        self.storage, self.repository = storage, repository

    def source(
        self, organization_id: str, project_id: str, region_code: str, import_id: str
    ) -> RawSource:
        raw = self.repository.get_source(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            raw_source_id=import_id,
        )
        if raw is None or raw.raw_status is not RawSourceStatus.COMMITTED:
            raise problem(
                status=404,
                code="RAW_SOURCE_NOT_FOUND",
                title="原始数据不存在",
                detail="当前项目和区域中没有这条原始数据记录。",
            )
        return raw

    def manifest(self, raw: RawSource) -> dict[str, Any]:
        value = json.loads(read_bounded(self.storage, raw.manifest_key, _MAX_MANIFEST_BYTES))
        if not isinstance(value, dict) or value.get("content_hash") != raw.content_hash:
            raise ValueError("original source manifest identity mismatch")
        return cast(dict[str, Any], value)

    def files(self, raw: RawSource, *, offset: int, limit: int) -> OriginalFilePage:
        manifest = self.manifest(raw)
        files = manifest["files"]
        return OriginalFilePage(
            import_id=raw.raw_source_id,
            source_format=raw.source_format.value,
            episode_count=int(manifest.get("episode_count", 0)),
            files=[OriginalFile.model_validate(item) for item in files[offset : offset + limit]],
            total=len(files),
            offset=offset,
        )

    def grant(self, raw: RawSource, path: str, *, download: bool = False) -> OriginalFileGrant:
        # Membership, not just path syntax, controls the signed object location.
        descriptor = next(
            (item for item in self.manifest(raw)["files"] if item["path"] == path), None
        )
        if descriptor is None:
            raise problem(
                status=404,
                code="RAW_OBJECT_NOT_FOUND",
                title="原文件不存在",
                detail="该路径不属于已提交的原始数据。",
            )
        return OriginalFileGrant(
            path=path,
            url=self.storage.presign_read(
                descriptor.get("object_key") or f"{raw.storage_prefix}/{path}",
                900,
                download_name=path.rsplit("/", 1)[-1] if download else None,
            ),
            expires_at=utc_now() + timedelta(seconds=900),
        )

    def episode(self, raw: RawSource, episode_index: int) -> OriginalEpisode:
        if raw.source_format is not RawSourceFormat.LEROBOT_V3:
            raise problem(
                status=422,
                code="RAW_PREVIEW_UNSUPPORTED",
                title="该格式暂不支持视频预览",
                detail="原始文件可正常下载；MCAP 和 ROS bag 不会自动转成新视频。",
            )
        if not _INDEX_SLOTS.acquire(blocking=False):
            raise problem(
                status=429,
                code="RAW_INDEX_BUSY",
                title="预览索引繁忙",
                detail="请稍后重试。",
                retryable=True,
                retry_after_seconds=2,
            )
        try:
            return self._episode(raw, episode_index)
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            raise problem(
                status=422,
                code="RAW_EPISODE_METADATA_INVALID",
                title="原始 Episode 元数据无法预览",
                detail="元数据字段或视频片段索引无效；原始文件仍可正常下载。",
            ) from exc
        finally:
            _INDEX_SLOTS.release()

    def _episode(self, raw: RawSource, episode_index: int) -> OriginalEpisode:
        import pyarrow as pa
        import pyarrow.parquet as pq

        manifest = self.manifest(raw)
        if not 0 <= episode_index < int(manifest.get("episode_count", 0)):
            raise problem(
                status=404,
                code="RAW_EPISODE_NOT_FOUND",
                title="Episode 不存在",
                detail="Episode 编号超出原数据范围。",
            )
        files = {item["path"]: item for item in manifest["files"]}
        info = json.loads(
            read_bounded(
                self.storage,
                files["meta/info.json"].get("object_key") or f"{raw.storage_prefix}/meta/info.json",
                1024**2,
            )
        )
        cameras = [
            key
            for key, feature in info["features"].items()
            if isinstance(feature, dict) and feature.get("dtype") == "video"
        ]
        if len(cameras) > 64:
            raise ValueError("too many camera columns for interactive preview")
        columns = ["episode_index", "length"]
        columns.extend(
            f"videos/{camera}/{field}"
            for camera in cameras
            for field in ("chunk_index", "file_index", "from_timestamp", "to_timestamp")
        )
        found: dict[str, Any] | None = None
        for path, descriptor in sorted(files.items()):
            if not path.startswith("meta/episodes/") or not path.endswith(".parquet"):
                continue
            with _RangeReader(
                self.storage,
                descriptor.get("object_key") or f"{raw.storage_prefix}/{path}",
                descriptor["size"],
            ) as stream:
                parquet = pq.ParquetFile(stream)
                for column in columns:
                    kind = parquet.schema_arrow.field(column).type
                    if not (pa.types.is_integer(kind) or pa.types.is_floating(kind)):
                        raise ValueError("episode index columns must be numeric scalars")
                for group_index in range(parquet.metadata.num_row_groups):
                    group = parquet.metadata.row_group(group_index)
                    decoded_bytes = sum(
                        group.column(index).total_uncompressed_size
                        for index in range(group.num_columns)
                        if group.column(index).path_in_schema in columns
                    )
                    if decoded_bytes > _MAX_METADATA_READ:
                        raise problem(
                            status=413,
                            code="RAW_INDEX_DECODE_TOO_LARGE",
                            title="元数据超过在线预览上限",
                            detail="原始文件已保存，可下载后离线查看。",
                        )
                for batch in parquet.iter_batches(
                    batch_size=128, columns=columns, use_threads=False
                ):
                    for row in batch.to_pylist():
                        if row.get("episode_index") == episode_index:
                            if found is not None:
                                raise ValueError("episode metadata must be unique")
                            found = row
        if found is None:
            raise problem(
                status=404,
                code="RAW_EPISODE_NOT_FOUND",
                title="Episode 元数据缺失",
                detail="原始 episode 元数据没有对应记录，原文件仍可下载。",
            )
        fps, count = float(info["fps"]), int(found["length"])
        if not math.isfinite(fps) or fps <= 0 or count <= 0:
            raise ValueError("invalid original episode timeline")
        videos: list[OriginalVideo] = []
        for camera in cameras:
            prefix = f"videos/{camera}/"
            path = str(info["video_path"]).format(
                video_key=camera,
                chunk_index=int(found[prefix + "chunk_index"]),
                file_index=int(found[prefix + "file_index"]),
            )
            start, end = (
                float(found[prefix + "from_timestamp"]),
                float(found[prefix + "to_timestamp"]),
            )
            if (
                path not in files
                or not math.isfinite(start)
                or not math.isfinite(end)
                or not 0 <= start < end
            ):
                raise ValueError("original video segment is missing or has an invalid time range")
            videos.append(
                OriginalVideo(camera_id=camera, path=path, start_seconds=start, end_seconds=end)
            )
        return OriginalEpisode(
            episode_index=episode_index,
            frame_count=count,
            fps=fps,
            duration_ns=str(round(count / fps * 1_000_000_000)),
            videos=videos,
        )
