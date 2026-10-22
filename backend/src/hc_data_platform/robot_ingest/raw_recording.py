"""Materialize a raw recording on the Worker; replay from an immutable receipt."""

import hashlib
import io
import json
import tempfile
from pathlib import Path

from hc_data_platform.continuous_recordings.asset_models import CreateRecordingUploadCommand
from hc_data_platform.continuous_recordings.openarm_raw.prepare import VERSION, prepare
from hc_data_platform.ingest.models import CompletedPart
from hc_data_platform.object_storage.checksums import crc64_ecma

from .models import RobotIngestAsset

READABLE_VERSIONS = tuple(
    dict.fromkeys((VERSION, *(f"openarm-raw-platform/{i}" for i in (9, 8, 7, 6, 5, 4, 3, 2, 1))))
)


def verify(storage, key, size, sha, progress, destination=None):
    digest, count = hashlib.sha256(), 0
    for block in storage.read_chunks(key):
        digest.update(block)
        count += len(block)
        if count > size:
            raise ValueError("object exceeds immutable receipt")
        if destination is not None:
            destination.write(block)
        progress()
    if (count, digest.hexdigest()) != (size, sha):
        raise ValueError("object differs from immutable receipt")


def publish(storage, key, path, size, sha, progress):
    if storage.head(key) is None:
        upload_id = storage.create_multipart(key)
        try:
            parts = []
            with path.open("rb") as stream:
                while block := stream.read(8 * 1024 * 1024):
                    part = storage.upload_part_stream(
                        key, upload_id, len(parts) + 1, io.BytesIO(block), len(block)
                    )
                    parts.append(CompletedPart(part_number=part.part_number, etag=part.etag))
                    progress()
            storage.complete_multipart(key, upload_id, parts)
        except Exception:
            # A lost completion response is reconciled against actual bytes.
            if storage.head(key) is None:
                storage.abort_multipart(key, upload_id)
                raise
    verify(storage, key, size, sha, progress)
    return storage.head(key)


def receipt_prefix(upload, version):
    if version not in READABLE_VERSIONS:
        raise ValueError("unsupported raw derivation version")
    return "/".join(
        (
            "derived-recordings",
            upload.target.organization_id,
            upload.target.project_id,
            upload.target.region_code,
            upload.raw_source_id,
            version.replace("/", "-"),
        )
    )


def checked_receipt(receipt, upload, marker, command, originals, version):
    """An internal cache cannot introduce another identity or arbitrary locator."""
    if (
        receipt["source_sha256"] != marker["content_sha256"]
        or receipt["processor_version"] != version
    ):
        raise ValueError("immutable derivation receipt conflict")
    derived = CreateRecordingUploadCommand.model_validate(receipt["command"])
    fields = set(type(command).model_fields) - {"assets", "schema_version"}
    if any(getattr(derived, field) != getattr(command, field) for field in fields):
        raise ValueError("cached derivation changes original recording identity")
    assets = {
        path: RobotIngestAsset.model_validate(value) for path, value in receipt["assets"].items()
    }
    if set(assets) != {item.path for item in derived.assets}:
        raise ValueError("cached derivation asset inventory mismatch")
    prefix = receipt_prefix(upload, version)
    source_paths = {"source/original/" + item.path: item for item in command.assets}
    if not source_paths.keys() <= assets.keys():
        raise ValueError("cached derivation omits original assets")
    for item in derived.assets:
        asset = assets[item.path]
        if (
            (
                asset.path,
                asset.role,
                asset.media_type,
                asset.expected_size_bytes,
                asset.expected_sha256,
                asset.expected_crc64,
            )
            != (
                item.path,
                item.role.value,
                item.media_type,
                item.size,
                item.sha256,
                item.crc64,
            )
            or asset.state.value != "COMPLETED"
            or (asset.actual_size_bytes, asset.actual_sha256, asset.actual_crc64)
            != (item.size, item.sha256, item.crc64)
        ):
            raise ValueError("cached derivation differs from its verified asset receipt")
        if original := source_paths.get(item.path):
            if item.model_copy(update={"path": original.path}) != original:
                raise ValueError("cached derivation changes original manifest")
            expected_key = originals[original.path].object_key
        else:
            if item.path.startswith("source/original/"):
                raise ValueError("cached derivation invents an original asset")
            expected_key = f"{prefix}/{item.sha256}/{item.path}"
        if asset.object_key != expected_key:
            raise PermissionError("cached derivation object is outside its frozen source")
    return derived, assets


def materialize(
    storage,
    upload,
    marker,
    command,
    originals,
    progress=lambda: None,
    *,
    expected_command=None,
    expected_objects=None,
):
    # The caller holds the immutable robot-upload row lock. An existing recording
    # pins the *complete command*, not merely the currently installed processor.
    # Lost DB transactions can still recover the older completed storage receipt.
    if expected_command is not None and not expected_objects:
        raise ValueError("pinned recording lacks its committed asset locators")
    for version in READABLE_VERSIONS:
        prefix = receipt_prefix(upload, version)
        if expected_objects is not None and not any(
            key.startswith(prefix + "/") for key in expected_objects.values()
        ):
            continue
        receipt_key = prefix + "/receipt.json"
        if storage.head(receipt_key) is None:
            continue
        body = bytearray()
        for block in storage.read_chunks(receipt_key):
            body.extend(block)
            if len(body) > 16 * 1024 * 1024:
                raise ValueError("derivation receipt too large")
        receipt = json.loads(body)
        derived, assets = checked_receipt(receipt, upload, marker, command, originals, version)
        if expected_command is not None and (
            derived.model_copy(update={"collection_job_id": upload.collection_job_id})
            != expected_command
            or {path: asset.object_key for path, asset in assets.items()} != expected_objects
        ):
            continue
        for asset in assets.values():
            verify(
                storage,
                asset.object_key,
                asset.expected_size_bytes,
                asset.expected_sha256,
                progress,
            )
        return derived, assets
    if expected_command is not None:
        raise ValueError("pinned recording derivation receipt is missing; refusing regeneration")
    prefix = receipt_prefix(upload, VERSION)
    receipt_key = prefix + "/receipt.json"
    with tempfile.TemporaryDirectory(prefix="openarm-raw-") as directory:
        root = Path(directory) / "original"
        root.mkdir()
        for item in command.assets:
            target = root / item.path
            if not target.resolve().is_relative_to(root.resolve()):
                raise ValueError("unsafe source path")
            target.parent.mkdir(parents=True, exist_ok=True)
            source = originals[item.path]
            with target.open("wb") as stream:
                verify(storage, source.object_key, item.size, item.sha256, progress, stream)
        output = Path(directory) / "derived"
        session = json.loads((root / "session.json").read_text())
        if session.get("schema_version") == "hc-topic-video/v1":
            from hc_data_platform.continuous_recordings.topic_video import prepare as prepare_video

            config, roles = prepare_video(
                root, output, command, getattr(upload, "processing_config_snapshot", None), progress
            )
        else:
            config, roles = prepare(root, output, command, progress)
            if getattr(upload, "processing_config_snapshot", None):
                from hc_data_platform.capture_schemes.bundles import validate_prepared

                validate_prepared(
                    root, output, config, getattr(upload, "processing_config_snapshot", None)
                )
        declarations, assets = [], {}
        template = next(iter(originals.values()))
        for path in sorted(output.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(output).as_posix()
            digest, crc, size = hashlib.sha256(), 0, 0
            with path.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    digest.update(block)
                    crc = crc64_ecma(block, crc)
                    size += len(block)
                    progress()
            sha = digest.hexdigest()
            key = prefix + "/" + sha + "/" + relative
            metadata = publish(storage, key, path, size, sha, progress)
            role, camera, media = roles[relative]
            declarations.append(
                {
                    "path": relative,
                    "role": role,
                    "camera_id": camera,
                    "media_type": media,
                    "size": size,
                    "sha256": sha,
                    "crc64": crc,
                    "part_count": 1,
                }
            )
            assets[relative] = template.model_copy(
                update={
                    "path": relative,
                    "role": role,
                    "media_type": media,
                    "object_key": key,
                    "expected_size_bytes": size,
                    "expected_sha256": sha,
                    "expected_crc64": crc,
                    "actual_size_bytes": size,
                    "actual_sha256": sha,
                    "actual_crc64": crc,
                    "etag": metadata.etag,
                    "multipart_upload_id": "platform-derived",
                }
            )
        # Reuse original verified objects. No robot SQLite or derivative archive exists.
        for item in command.assets:
            relative = "source/original/" + item.path
            declarations.append({**item.model_dump(mode="json"), "path": relative})
            assets[relative] = originals[item.path].model_copy(update={"path": relative})
        derived = CreateRecordingUploadCommand.model_validate(
            {
                **command.model_dump(mode="json"),
                "schema_version": "continuous-recording-upload/v3"
                if config.get("collection_mode") == "JOINT_ONLY"
                else "continuous-recording-upload/v2",
                "assets": sorted(declarations, key=lambda a: a["path"]),
                "recording_config": config,
            }
        )
        receipt = {
            "processor_version": VERSION,
            "source_sha256": marker["content_sha256"],
            "command": derived.model_dump(mode="json"),
            "assets": {path: a.model_dump(mode="json") for path, a in assets.items()},
        }
        storage.put_json(receipt_key, receipt, if_none_match=True)
        return derived, assets
