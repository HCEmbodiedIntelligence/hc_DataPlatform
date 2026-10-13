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


def preview_key(upload, asset, assets):
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
        }
    )
    scope = upload.scope
    return (
        f"recording-previews/{scope.organization_id}/{scope.project_id}/"
        f"{scope.region_code}/{identity}/media.mp4"
    )


def read_preview(storage, upload, asset, assets):
    key = preview_key(upload, asset, assets)
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


def prepare_recording_previews(storage, upload, assets, progress=lambda: None):
    """Invoked by the registration Worker, and explicitly for historical recordings."""
    import av
    import pyarrow.parquet as pq

    from hc_data_platform.data_formats.depth_evidence import file_hash
    from hc_data_platform.data_formats.holobrain_depth import DepthProfile
    from hc_data_platform.episode_store.previews import EpisodePreviews
    from hc_data_platform.episode_store.validation import parquet_rows

    by_path = {item.manifest.path: item for item in assets}
    cameras = {camera.camera_id: camera for camera in upload.command.recording_config.cameras}
    for asset in assets:
        if (
            asset.manifest.role != RecordingAssetRole.RAW_VIDEO
            or asset.status != RecordingAssetStatus.COMMITTED
        ):
            continue
        camera = cameras[asset.manifest.camera_id]
        if not is_depth(camera) or read_preview(storage, upload, asset, assets):
            continue
        stem = PurePosixPath(asset.manifest.path).stem.removesuffix("_depth")
        evidence = f"hc/depth/{stem}"
        profile_asset = by_path.get(f"{evidence}/profile.json")
        masks_asset = by_path.get(f"{evidence}/validity/flags.parquet")
        if profile_asset is None or masks_asset is None:
            # Never guess physical units or invalid pixels for historical formats.
            continue
        with TemporaryDirectory(prefix="recording-preview-") as directory:
            root = Path(directory)
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
            progress()
            key = preview_key(upload, asset, assets)
            size, sha = output.stat().st_size, file_hash(output)
            with output.open("rb") as stream:
                MultipartImmutableWriter(storage).write_immutable(
                    key, stream, size=size, sha256=sha, checkpoint=progress
                )
            storage.put_json(
                key + ".json",
                {"source_sha256": asset.manifest.sha256, "sha256": sha, "size": size},
                if_none_match=True,
            )
