"""Worker-built recording depth previews, using the annotation display encoder.

Original HEVC depth and validity evidence remain immutable. Published previews
can be rehydrated into the local expiring cache from the same committed inputs.
"""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from hc_data_platform.data_formats.video import preview_timescale
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
    inputs = sorted((item.manifest.path, item.manifest.sha256) for item in assets)
    return preview_key_from_inputs(upload.upload_id, upload.scope, upload.manifest_sha256,
                                   asset.asset_id, inputs, selected=selected)


def preview_key_from_inputs(upload_id, scope, manifest_sha256, asset_id, inputs, *, selected):
    # Include every committed evidence identity, scope and display profile. No
    # preview can be reused across recordings or changed evidence manifests.
    identity = canonical_hash(
        {
            "upload": str(upload_id),
            "scope": scope.model_dump(),
            "manifest": manifest_sha256,
            "asset": str(asset_id),
            "inputs": inputs,
            "profile": PROFILE,
            **(
                {"frame_policy": "valid-source-rows-vfr/1"}
                if selected and any(path == "source/frames.jsonl" for path, _ in inputs)
                else {}
            ),
        }
    )
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
    if head is None:
        from hc_data_platform.storage.local_cache import local_cache_for
        if local_cache_for(storage) is not None:
            return None
    if (
        receipt["source_sha256"] != asset.manifest.sha256
        or head is None
        or head.size != receipt["size"]
    ):
        raise ValueError("recording preview differs from its committed source")
    return key


def _download(storage, asset, path, progress, verified_paths=None):
    if verified_paths and asset.object_key in verified_paths:
        source = verified_paths[asset.object_key]
        try:
            os.link(source, path)
        except OSError:
            pass
        else:
            progress()
            return
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


def _remux_complete_preview(source, destination, frames, fps, progress, scale):
    """Retimestamp every H.264 frame without encoding it a second time."""
    from fractions import Fraction

    import av

    clock = Fraction(1, scale)
    ns_per_tick = 1_000_000_000 // scale
    with av.open(str(source)) as incoming:
        stream = incoming.streams.video[0]
        codec = stream.codec_context
        if (codec.name != "h264" or codec.has_b_frames or codec.format is None
                or codec.format.name != "yuv420p" or stream.frames != len(frames)):
            return False
        count = 0
        previous = None
        with av.open(str(destination), "w", options={
            "movie_timescale": str(scale), "video_track_timescale": str(scale),
        }) as output:
            target = output.add_stream_from_template(stream)
            target.time_base = clock
            for packet in incoming.demux(stream):
                if not packet.size:
                    continue
                progress()
                if (packet.pts is None or packet.pts != packet.dts or count >= len(frames)
                        or previous is not None and packet.pts <= previous):
                    return False
                previous = packet.pts
                stamp = frames[count]["offset_ns"]
                duration = (frames[count + 1]["offset_ns"] - stamp if count + 1 < len(frames)
                            else round(1_000_000_000 / fps))
                if duration <= 0:
                    return False
                packet.stream = target
                tick = round(Fraction(stamp, ns_per_tick))
                packet.pts = packet.dts = tick
                packet.duration = round(Fraction(stamp + duration, ns_per_tick)) - tick
                packet.time_base = clock
                output.mux(packet)
                count += 1
        return count == len(frames)


def select_preview(source, destination, frames, fps, progress=lambda: None):
    """Dense decoded frame order on the original clock, for raw slice coordinates."""
    from fractions import Fraction

    import av

    retained = [(r["source_frame"], r["offset_ns"]) for r in frames if r["valid"]]
    if not retained:
        raise ValueError("recording preview has no usable frames")
    scale = preview_timescale(offset for _, offset in retained)
    if (len(retained) == len(frames)
            and all(index == position for position, (index, _) in enumerate(retained))
            and _remux_complete_preview(source, destination, frames, fps, progress, scale)):
        _verify_preview_timeline(destination, retained)
        return len(retained)
    count = 0
    with av.open(str(source)) as incoming, av.open(
        str(destination), "w",
        options={"movie_timescale": str(scale), "video_track_timescale": str(scale)},
    ) as output:
        stream = incoming.streams.video[0]
        if stream.frames and stream.frames != len(frames):
            raise ValueError("recording preview frame map does not cover its source video")
        encoder = output.add_stream("libx264", rate=Fraction(str(fps)))
        encoder.width, encoder.height = stream.width, stream.height
        encoder.pix_fmt = "yuv420p"
        # Browser display only; same settings as the depth preview encoder.
        encoder.options = {"crf": "20", "preset": "veryfast", "threads": "2", "bf": "0", "g": "30"}
        source_clock = Fraction(1, 1_000_000_000)
        encoder.time_base = Fraction(1, scale)
        encoder.codec_context.time_base = encoder.time_base
        for i, frame in enumerate(incoming.decode(stream)):
            progress()
            if count == len(retained):
                break
            index, offset = retained[count]
            if i != index:
                continue
            frame.pts, frame.time_base = offset, source_clock
            for packet in encoder.encode(frame):
                output.mux(packet)
            count += 1
        for packet in encoder.encode():
            output.mux(packet)
    if count != len(retained):
        raise ValueError("recording preview selection exceeds video frames")
    _verify_preview_timeline(destination, retained)
    return count


def _verify_preview_timeline(destination, retained):
    import av

    # Both paths emit H.264 in presentation order without B frames.
    with av.open(str(destination)) as verify:
        observed = [round(p.pts * p.time_base * 1_000_000_000)
                    for p in verify.demux(video=0) if p.size and p.pts is not None]
    if len(observed) != len(retained) or any(
        abs(a - b) > 1_000 for a, (_, b) in zip(observed, retained, strict=True)
    ):
        raise ValueError("recording preview changed source timeline")


def prepare_recording_previews(storage, upload, assets, progress=lambda: None, *, verified_paths=None, preview_frames=None):
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
            frames, offsets, frame_count = None, None, None
            if trace_asset is not None:
                _download(storage, trace_asset, root / "frames.jsonl", progress, verified_paths)
                frames = [
                    json.loads(line)
                    for line in (root / "frames.jsonl").read_text().splitlines()
                    if line.strip()
                ]
                if [r["source_frame"] for r in frames] != list(range(len(frames))):
                    raise ValueError("recording preview source frame map is not dense")
                if not any(r["valid"] for r in frames):
                    continue
                offsets = [r["offset_ns"] if r["valid"] else None for r in frames]
                frame_count = sum(r["valid"] for r in frames)
            if not is_depth(camera):
                _download(storage, asset, root / "source.mp4", progress, verified_paths)
                output = root / "source.mp4"
                if frames is not None:
                    frame_count = select_preview(
                        output, root / "selected.mp4", frames, camera.fps, progress
                    )
                    output = root / "selected.mp4"
            else:
                # Colorize only the retained rows, directly on the source clock.
                _prepare_depth_preview(
                    storage, asset, profile_asset, masks_asset, root, camera, progress, verified_paths,
                    preview_frames.frames.pop((verified_paths or {}).get(asset.object_key), None)
                    if preview_frames is not None else None,
                    offsets=offsets,
                )
                output = root / "preview.mp4"
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


def _prepare_depth_preview(storage, asset, profile_asset, masks_asset, root, camera, progress, verified_paths=None, verified_codes=None, offsets=None):
    import av
    import pyarrow.parquet as pq

    from hc_data_platform.data_formats.holobrain_depth import DepthProfile, parallel_decode
    from hc_data_platform.episode_store.previews import EpisodePreviews
    from hc_data_platform.episode_store.validation import parquet_rows

    for source, name in (
        (asset, "source.mp4"),
        (profile_asset, "profile.json"),
        (masks_asset, "flags.parquet"),
    ):
        _download(storage, source, root / name, progress, verified_paths)
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
        parallel_decode(stream)
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
            checkpoint=progress, verified_codes=verified_codes, offsets=offsets,
        )
