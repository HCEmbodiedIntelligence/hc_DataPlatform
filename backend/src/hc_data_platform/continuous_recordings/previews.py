"""Worker-built recording depth previews, using the annotation display encoder.

Original HEVC depth and validity evidence remain immutable. GET only authorizes
an already published, source-bound derivative; it never starts an encoder.
"""

import hashlib
import json
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from hc_data_platform.object_storage.immutable import MultipartImmutableWriter
from hc_data_platform.security.audit import canonical_hash

from .asset_models import RecordingAssetRole, RecordingAssetStatus

PROFILE = "rs-2.58.4-dynamic-jet-h264-v1"


def is_depth(camera):
    return (
        camera.camera_id.endswith("_depth")
        or "depth" in (camera.topic or "").split("/")[-1].split(".")[-1]
    )


def preview_key(upload, asset, assets, *, selected=True):
    # Include every committed evidence identity, scope and display profile. No
    # preview can be reused across recordings or changed evidence manifests.
    identity = canonical_hash(
        {
            "upload": str(upload.upload_id),
            "scope": upload.scope.model_dump(),
            "manifest": upload.manifest_sha256,
            "asset": str(asset.asset_id),
            "inputs": sorted((item.manifest.path, item.manifest.sha256) for item in assets),
            "profile": PROFILE,
            **(
                {"frame_policy": "valid-source-rows-vfr/1"}
                if selected and any(item.manifest.path == "source/frames.jsonl" for item in assets)
                else {}
            ),
        }
    )
    scope = upload.scope
    return (
        f"recording-previews/{scope.organization_id}/{scope.project_id}/"
        f"{scope.region_code}/{identity}/media.mp4"
    )


def read_preview(storage, upload, asset, assets, *, selected=True):
    key = preview_key(upload, asset, assets, selected=selected)
    if storage.head(key + ".json") is None:
        return None
    body = bytearray()
    for block in storage.read_chunks(key + ".json"):
        body.extend(block)
        if len(body) > 16384:
            raise ValueError("recording preview receipt exceeds limit")
    receipt = json.loads(body)
    head = storage.head(key)
    if (
        receipt["source_sha256"] != asset.manifest.sha256
        or head is None
        or head.size != receipt["size"]
    ):
        raise ValueError("recording preview differs from its committed source")
    return key


def _download(storage, asset, path, progress):
    digest, size = hashlib.sha256(), 0
    with path.open("wb") as output:
        for block in storage.read_chunks(asset.object_key):
            progress()
            size += len(block)
            if size > asset.manifest.size:
                raise ValueError("recording preview source exceeds committed size")
            output.write(block)
            digest.update(block)
    if size != asset.manifest.size or digest.hexdigest() != asset.manifest.sha256:
        raise ValueError("recording preview source checksum mismatch")


def select_preview(source, destination, frames, fps, progress=lambda: None):
    """Dense decoded frame order on the original clock, for raw slice coordinates."""
    from fractions import Fraction

    import av

    retained = [(r["source_frame"], r["offset_ns"]) for r in frames if r["valid"]]
    if not retained:
        raise ValueError("recording preview has no usable frames")
    count = 0
    with av.open(str(source)) as incoming, av.open(
        str(destination), "w",
        options={"movie_timescale": "1000000000", "video_track_timescale": "1000000000"},
    ) as output:
        stream = incoming.streams.video[0]
        if stream.frames and stream.frames != len(frames):
            raise ValueError("recording preview frame map does not cover its source video")
        encoder = output.add_stream("libx264", rate=Fraction(str(fps)))
        encoder.width, encoder.height = stream.width, stream.height
        encoder.pix_fmt = "yuv420p"
        encoder.options = {"crf": "0", "preset": "fast", "threads": "1", "bf": "0"}
        encoder.time_base = Fraction(1, 1_000_000_000)
        encoder.codec_context.time_base = encoder.time_base
        for i, frame in enumerate(incoming.decode(stream)):
            progress()
            if count == len(retained):
                break
            index, offset = retained[count]
            if i != index:
                continue
            frame.pts, frame.time_base = offset, encoder.time_base
            for packet in encoder.encode(frame):
                output.mux(packet)
            count += 1
        for packet in encoder.encode():
            output.mux(packet)
    if count != len(retained):
        raise ValueError("recording preview selection exceeds video frames")
    with av.open(str(destination)) as verify:
        observed = [round(f.pts * f.time_base * 1_000_000_000) for f in verify.decode(video=0)]
    if len(observed) != count or any(
        abs(a - b) > 1_000 for a, (_, b) in zip(observed, retained, strict=True)
    ):
        raise ValueError("recording preview changed source timeline")
    return count


def prepare_recording_previews(storage, upload, assets, progress=lambda: None):
    """Invoked by the registration Worker, and explicitly for historical recordings."""
    from hc_data_platform.data_formats.depth_evidence import file_hash

    by_path = {item.manifest.path: item for item in assets}
    cameras = {camera.camera_id: camera for camera in upload.command.recording_config.cameras}
    for asset in assets:
        if (
            asset.manifest.role != RecordingAssetRole.RAW_VIDEO
            or asset.status != RecordingAssetStatus.COMMITTED
        ):
            continue
        camera = cameras[asset.manifest.camera_id]
        trace_asset = by_path.get("source/frames.jsonl")
        if (not is_depth(camera) and trace_asset is None) or read_preview(
            storage, upload, asset, assets
        ):
            continue
        stem = PurePosixPath(asset.manifest.path).stem.removesuffix("_depth")
        evidence = f"hc/depth/{stem}"
        profile_asset = by_path.get(f"{evidence}/profile.json")
        masks_asset = by_path.get(f"{evidence}/validity/flags.parquet")
        if is_depth(camera) and (profile_asset is None or masks_asset is None):
            # Never guess physical units or invalid pixels for historical formats.
            continue
        with TemporaryDirectory(prefix="recording-preview-") as directory:
            root = Path(directory)
            if not is_depth(camera):
                _download(storage, asset, root / "source.mp4", progress)
            else:
                _prepare_depth_preview(
                    storage, asset, profile_asset, masks_asset, root, camera, progress
                )
            output = root / ("preview.mp4" if is_depth(camera) else "source.mp4")
            frame_count = None
            if trace_asset is not None:
                _download(storage, trace_asset, root / "frames.jsonl", progress)
                frames = [
                    json.loads(line)
                    for line in (root / "frames.jsonl").read_text().splitlines()
                    if line.strip()
                ]
                if [r["source_frame"] for r in frames] != list(range(len(frames))):
                    raise ValueError("recording preview source frame map is not dense")
                if not any(r["valid"] for r in frames):
                    continue
                frame_count = select_preview(
                    output, root / "selected.mp4", frames, camera.fps, progress
                )
                output = root / "selected.mp4"
            progress()
            key = preview_key(upload, asset, assets)
            size, sha = output.stat().st_size, file_hash(output)
            with output.open("rb") as stream:
                MultipartImmutableWriter(storage).write_immutable(
                    key, stream, size=size, sha256=sha, checkpoint=progress
                )
            storage.put_json(
                key + ".json",
                {
                    "source_sha256": asset.manifest.sha256,
                    "sha256": sha,
                    "size": size,
                    "frame_count": frame_count,
                    "timestamp_policy": "source_offsets",
                },
                if_none_match=True,
            )


def _prepare_depth_preview(storage, asset, profile_asset, masks_asset, root, camera, progress):
    import av
    import pyarrow.parquet as pq

    from hc_data_platform.data_formats.holobrain_depth import DepthProfile
    from hc_data_platform.episode_store.previews import EpisodePreviews
    from hc_data_platform.episode_store.validation import parquet_rows

    for source, name in (
        (asset, "source.mp4"),
        (profile_asset, "profile.json"),
        (masks_asset, "flags.parquet"),
    ):
        _download(storage, source, root / name, progress)
    declaration = json.loads((root / "profile.json").read_text())
    profile = DepthProfile(
        **{
            key: declaration[key]
            for key in ("depth_min", "depth_max", "shift", "use_log", "version")
        }
    )
    if profile.sha256 != declaration["profile_sha256"]:
        raise ValueError("recording depth profile checksum mismatch")
    masks = iter(parquet_rows(root / "flags.parquet"))
    frame_count = pq.read_metadata(root / "flags.parquet").num_rows
    output = root / "preview.mp4"
    with av.open(str(root / "source.mp4")) as source:
        stream = source.streams.video[0]
        if stream.width * stream.height * 3 > 64 * 1024**2:
            raise ValueError("recording preview frame exceeds limit")
        stream.thread_count = 1
        EpisodePreviews._encode(
            source,
            stream,
            output,
            frame_count,
            camera.fps,
            stream.width + stream.width % 2,
            stream.height + stream.height % 2,
            masks=masks,
            profile=profile,
            checkpoint=progress,
        )
