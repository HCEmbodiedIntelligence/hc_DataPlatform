"""Processing-owned browser derivatives; authorization never starts an encoder.

Primary package files remain immutable. Preview READY follows verified object
publication, and every grant requires a fresh scoped committed-package lookup.
"""

import io
import time
from contextlib import ExitStack, suppress
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from uuid import uuid4

import av
import numpy as np
from PIL import Image
from pydantic import Field

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import problem
from hc_data_platform.publishing.depth_evidence import file_hash
from hc_data_platform.publishing.holobrain_depth import DepthProfile, dequantize
from hc_data_platform.publishing.lerobot_export import VIDEO_PATH

from .models import DataVersionRef, Digest, FileEntry, FrozenModel, canonical, digest
from .postgres import PackageConflict, assert_scope
from .realsense_colorizer import RealSenseColorizer, display_profile
from .validation import parquet_rows

PROFILE = "rs-2.58.4-dynamic-jet-h264-v1"


class PreviewReceipt(FrozenModel):
    data: DataVersionRef
    feature: str
    profile_id: Literal[PROFILE] = PROFILE
    source_hash: Digest
    source_video_sha256: Digest
    object_key: str
    file: FileEntry
    kind: Literal["rgb-video", "depth-preview"]
    source_reused: bool
    fps: float = Field(gt=0, allow_inf_nan=False)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    depth_min_m: float | None = None
    depth_max_m: float | None = None
    colorizer: dict | None = None


class EpisodePreviewGrant(FrozenModel):
    data: DataVersionRef
    feature: str
    profile_id: str
    kind: Literal["rgb-video", "depth-preview"]
    source_reused: bool
    source_video_sha256: Digest
    media_sha256: Digest
    frame_count: int = Field(gt=0)
    fps: float = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    depth_min_m: float | None = None
    depth_max_m: float | None = None
    colorizer: dict | None = None
    media_url: str
    expires_at: datetime


class EpisodePreviews:
    def __init__(self, files, committer, directory, *, signer=None, checkpoint=lambda stage: None):
        self.files, self.committer, self.directory = files, committer, directory
        self.repository, self.storage = files.repository, files.storage
        self.signer, self.checkpoint = signer, checkpoint

    @staticmethod
    def _key(data, feature):
        return (*data.identity.scope, data.data_version_id, feature, PROFILE)

    @staticmethod
    def _source(package, feature):
        info = package.document("meta/info.json")
        camera = info["features"].get(feature)
        if camera is None or camera["dtype"] != "video":
            raise KeyError("selected feature is not a package camera")
        relative = VIDEO_PATH.format(video_key=feature, chunk_index=0, file_index=0)
        entry = package.entries[relative]
        source_hash = digest(
            {
                "data": package.receipt.data.model_dump(mode="json"),
                "feature": feature,
                "profile": PROFILE,
                "video": entry.model_dump(mode="json"),
            }
        )
        return info, camera, entry, source_hash

    @staticmethod
    def _prefix(data, feature):
        return (
            f"episode-previews/{digest(data.identity.key)}/{data.data_version_id}"
            f"/{PROFILE}/{digest(feature)}"
        )

    def _row(self, data, feature):
        assert_scope(data.identity)
        with self.repository.transaction() as cursor:
            cursor.execute(
                """SELECT status,source_hash,receipt FROM episode_store.media_previews
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                  AND data_version_id=%s AND feature=%s AND profile_id=%s""",
                self._key(data, feature),
            )
            return cursor.fetchone()

    def _verified(self, package, feature, row, source_hash, *, verify_bytes=True):
        receipt = PreviewReceipt.model_validate(row[2])
        expected_key = (
            f"{receipt.data.package_prefix}/{receipt.file.path}"
            if receipt.source_reused
            else f"{self._prefix(receipt.data, feature)}/{receipt.file.path}"
        )
        if (
            receipt.data != package.receipt.data
            or receipt.feature != feature
            or row[1] != source_hash
            or receipt.source_hash != source_hash
            or receipt.object_key != expected_key
        ):
            raise PackageConflict("preview differs from its committed Episode inputs")
        info, camera, source, _ = self._source(package, feature)
        is_depth = camera.get("info", {}).get("is_depth_map", False)
        if (
            receipt.kind != ("depth-preview" if is_depth else "rgb-video")
            or receipt.fps != info["fps"]
            or (
                is_depth
                and (
                    receipt.depth_min_m != camera["info"]["video.depth_min"]
                    or receipt.depth_max_m != camera["info"]["video.depth_max"]
                    or receipt.colorizer != display_profile(receipt.depth_max_m)
                )
            )
            or (not is_depth and receipt.colorizer is not None)
        ):
            raise PackageConflict("preview display profile differs from the camera declaration")
        if receipt.source_video_sha256 != source.sha256 or (
            receipt.source_reused and (receipt.file != source or receipt.kind != "rgb-video")
        ):
            raise PackageConflict("preview source file differs from the committed camera")
        head = self.storage.head(receipt.object_key)
        if head is None or head.size != receipt.file.size:
            raise PackageConflict("ready preview file is missing or changed")
        if verify_bytes:
            with self.files.cache.file(self.storage, receipt.object_key, receipt.file):
                pass
        return receipt

    def prepare_all(self, data):
        package = self.files.open(data.identity, data.data_version_id)
        info = package.document("meta/info.json")
        return tuple(
            self.prepare(data, key)
            for key, item in info["features"].items()
            if item["dtype"] == "video"
        )

    def prepare(self, data: DataVersionRef, feature: str):
        """Called by processing, never by a GET/authorization request. Retry is idempotent."""
        with self.repository.writer_lock(data.identity):
            package = self.files.open(data.identity, data.data_version_id)
            if package.receipt.data != data:
                raise PackageConflict("preview input reference is not the committed version")
            info, camera, entry, source_hash = self._source(package, feature)
            row = self._row(data, feature)
            if row and row[0] == "READY":
                return self._verified(package, feature, row, source_hash)
            with self.repository.transaction() as cursor:
                cursor.execute(
                    """INSERT INTO episode_store.media_previews
                    (organization_id,project_id,region_code,dataset_id,episode_id,data_version_id,
                     feature,profile_id,source_hash,status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'PREPARING')
                    ON CONFLICT
                        (organization_id,project_id,region_code,data_version_id,feature,profile_id)
                    DO UPDATE SET status='PREPARING',failure_code=NULL,updated_at=clock_timestamp()
                    WHERE media_previews.source_hash=EXCLUDED.source_hash
                      AND media_previews.status != 'READY'""",
                    (*data.identity.key, data.data_version_id, feature, PROFILE, source_hash),
                )
                if cursor.rowcount != 1:
                    raise PackageConflict("preview inputs changed for the same version/profile")
            try:
                receipt = self._materialize(package, feature, info, camera, entry, source_hash)
                self.checkpoint("objects_verified")
                with self.repository.transaction() as cursor:
                    cursor.execute(
                        """UPDATE episode_store.media_previews
                        SET status='READY',receipt=%s::jsonb,updated_at=clock_timestamp()
                        WHERE organization_id=%s AND project_id=%s AND region_code=%s
                          AND data_version_id=%s AND feature=%s AND profile_id=%s
                          AND status='PREPARING' AND source_hash=%s""",
                        (
                            canonical(receipt.model_dump(mode="json")).decode(),
                            *self._key(data, feature),
                            source_hash,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise PackageConflict("preview publication state changed")
                self.checkpoint("database_committed")
                return receipt
            except BaseException as exc:
                with suppress(Exception), self.repository.transaction() as cursor:
                    cursor.execute(
                        """UPDATE episode_store.media_previews
                        SET status='FAILED',failure_code=%s,updated_at=clock_timestamp()
                        WHERE organization_id=%s AND project_id=%s AND region_code=%s
                          AND data_version_id=%s AND feature=%s AND profile_id=%s
                          AND status='PREPARING'""",
                        (type(exc).__name__, *self._key(data, feature)),
                    )
                raise

    def _materialize(self, package, feature, info, camera, entry, source_hash):
        data = package.receipt.data
        is_depth = camera.get("info", {}).get("is_depth_map", False)
        depth_min, depth_max = None, None
        with ExitStack() as stack:
            path = stack.enter_context(package.file(entry.path))
            source = stack.enter_context(av.open(str(path)))
            stream = source.streams.video[0]
            stream.thread_count = 1
            width, height = stream.width, stream.height
            if width * height * 3 > 64 * 1024**2:
                raise ValueError("preview input frame exceeds decode limit")
            reused = (
                not is_depth
                and stream.codec_context.name == "h264"
                and stream.codec_context.format.name == "yuv420p"
            )
            if reused:
                output_entry, key = entry, f"{data.package_prefix}/{entry.path}"
            else:
                masks, profile = None, None
                if is_depth:
                    evidence = package.document("hc/modalities.json")["depth"][feature][
                        "evidence_path"
                    ]
                    declaration = package.document(f"{evidence}/profile.json")
                    profile = DepthProfile(
                        **{
                            key: declaration[key]
                            for key in ("depth_min", "depth_max", "shift", "use_log", "version")
                        }
                    )
                    if profile.sha256 != declaration["profile_sha256"]:
                        raise PackageConflict("preview depth profile checksum mismatch")
                    depth_min, depth_max = profile.depth_min, profile.depth_max
                    masks = iter(
                        parquet_rows(
                            stack.enter_context(package.file(f"{evidence}/validity/flags.parquet"))
                        )
                    )
                self.directory.mkdir(parents=True, exist_ok=True)
                temporary = stack.enter_context(
                    TemporaryDirectory(prefix="preview-", dir=self.directory)
                )
                output = Path(temporary) / "media.mp4"
                width, height = width + width % 2, height + height % 2
                self._encode(
                    source,
                    stream,
                    output,
                    data.frame_count,
                    info["fps"],
                    width,
                    height,
                    masks=masks,
                    profile=profile,
                )
                sha = file_hash(output)
                # Content-addressed output also permits safe orphan recovery without overwrite.
                target = output.with_name(sha + ".mp4")
                output.rename(target)
                output_entry = FileEntry(
                    path=target.name, size=target.stat().st_size, sha256=sha, media_type="video/mp4"
                )
                prefix = self._prefix(data, feature)
                self.committer._put_file(Path(temporary), prefix, output_entry)
                key = f"{prefix}/{output_entry.path}"
        return PreviewReceipt(
            data=data,
            feature=feature,
            source_hash=source_hash,
            source_video_sha256=entry.sha256,
            object_key=key,
            file=output_entry,
            kind="depth-preview" if is_depth else "rgb-video",
            source_reused=reused,
            fps=info["fps"],
            width=width,
            height=height,
            depth_min_m=depth_min,
            depth_max_m=depth_max,
            colorizer=display_profile(depth_max) if is_depth else None,
        )

    @staticmethod
    def _encode(
        source, stream, output, frame_count, fps, width, height, *, masks, profile
    ):
        rate, began, count = Fraction(str(fps)), time.monotonic(), 0
        with (
            ExitStack() as resources,
            av.open(str(output), "w", options={"movflags": "+faststart"}) as container,
        ):
            colorizer = (
                resources.enter_context(
                    RealSenseColorizer((stream.height, stream.width), depth_max=profile.depth_max)
                )
                if profile
                else None
            )
            encoder = container.add_stream("libx264", rate=rate)
            encoder.width, encoder.height, encoder.pix_fmt = width, height, "yuv420p"
            encoder.options = {"crf": "20", "preset": "fast", "threads": "2", "bf": "0", "g": "30"}
            for frame in source.decode(stream):
                if (
                    count >= frame_count
                    or time.monotonic() - began > 3600
                    or frame.pts is None
                    or abs(float(frame.pts * frame.time_base) - count / fps) > 1e-6
                ):
                    raise ValueError("preview source frame count/time contract failed")
                if profile:
                    mask = next(masks, None)
                    if mask is None or mask["frame_index"] != count:
                        raise ValueError("preview depth mask/source frame mismatch")
                    with Image.open(io.BytesIO(mask["flags_png"])) as image:
                        flags = np.asarray(image).copy()
                    codes = frame.to_ndarray(format="gray12le")
                    if flags.shape != codes.shape:
                        raise ValueError("preview depth mask shape mismatch")
                    pixels = colorizer.colorize(dequantize(codes, profile, output_unit="m"), flags)
                    frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
                if frame.width != width or frame.height != height:
                    # Pad odd sensor dimensions without changing the original package.
                    pixels = frame.to_ndarray(format="rgb24")
                    pixels = np.pad(
                        pixels, ((0, height - frame.height), (0, width - frame.width), (0, 0))
                    )
                    frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
                frame.pts, frame.time_base = count, 1 / rate
                for packet in encoder.encode(frame):
                    container.mux(packet)
                count += 1
            for packet in encoder.encode():
                container.mux(packet)
        if count != frame_count or (masks is not None and next(masks, None) is not None):
            raise ValueError("preview output frame count differs from Episode")
        with av.open(str(output)) as verified:
            video = verified.streams.video[0]
            if video.codec_context.name != "h264" or video.codec_context.format.name != "yuv420p":
                raise ValueError("preview codec verification failed")
            count = 0
            for frame in verified.decode(video):
                if abs(float(frame.pts * frame.time_base) - count / fps) > 1e-6:
                    raise ValueError("preview output timeline verification failed")
                count += 1
            if count != frame_count:
                raise ValueError("preview output decode count differs from Episode")

    def authorize(self, identity, data_version_id, feature, *, actor_id):
        package = self.files.open(identity, data_version_id)
        data = package.receipt.data
        source_hash = self._source(package, feature)[3]
        row = self._row(data, feature)
        if row is None or row[0] != "READY":
            failed = row is not None and row[0] == "FAILED"
            raise problem(
                status=409 if failed else 425,
                code="EPISODE_PREVIEW_FAILED" if failed else "EPISODE_PREVIEW_NOT_READY",
                title="Episode preview unavailable",
                detail="Processing has not published this preview.",
            )
        receipt = self._verified(package, feature, row, source_hash)
        if self.signer is None:
            raise RuntimeError("Episode preview URL signer is not configured")
        ttl = timedelta(minutes=5)
        now = datetime.now(timezone.utc)
        url = self.signer(receipt.object_key, expires_in=ttl)
        # Audit contains immutable identities and expiry, never signed credentials.
        with self.repository.transaction() as cursor:
            cursor.execute(
                """INSERT INTO core.audit_events
                (audit_id,organization_id,project_id,region_code,actor_id,action,resource_type,
                 resource_id,request_id,details,occurred_at)
                VALUES (%s,%s,%s,%s,%s,'episode.preview.authorized','EPISODE_DATA_VERSION',
                        %s,%s,%s::jsonb,%s)""",
                (
                    uuid4(),
                    *identity.scope,
                    actor_id,
                    data_version_id,
                    current_request_context().request_id,
                    canonical(
                        {
                            "feature": feature,
                            "data_version_id": data_version_id,
                            "expires_at": (now + ttl).isoformat(),
                        }
                    ).decode(),
                    now,
                ),
            )
        return EpisodePreviewGrant(
            data=data,
            feature=feature,
            profile_id=PROFILE,
            kind=receipt.kind,
            source_reused=receipt.source_reused,
            source_video_sha256=receipt.source_video_sha256,
            media_sha256=receipt.file.sha256,
            frame_count=data.frame_count,
            fps=receipt.fps,
            width=receipt.width,
            height=receipt.height,
            depth_min_m=receipt.depth_min_m,
            depth_max_m=receipt.depth_max_m,
            colorizer=receipt.colorizer,
            media_url=url,
            expires_at=now + ttl,
        )
