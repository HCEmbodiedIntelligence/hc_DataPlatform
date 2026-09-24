"""Materialize a raw recording on the Worker; replay from an immutable receipt."""

import hashlib
import io
import json
import tempfile
from pathlib import Path

from hc_data_platform.continuous_recordings.asset_models import CreateRecordingUploadCommand
from hc_data_platform.continuous_recordings.openarm_raw.prepare import VERSION, prepare
from hc_data_platform.ingest.models import CompletedPart
from hc_data_platform.ingest.ports import crc64_ecma

from .models import RobotIngestAsset


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


def materialize(storage, upload, marker, command, originals, progress=lambda: None):
    prefix = "/".join(
        (
            "derived-recordings",
            upload.target.organization_id,
            upload.target.project_id,
            upload.target.region_code,
            upload.raw_source_id,
            VERSION.replace("/", "-"),
        )
    )
    receipt_key = prefix + "/receipt.json"
    if storage.head(receipt_key) is not None:
        body = bytearray()
        for block in storage.read_chunks(receipt_key):
            body.extend(block)
            if len(body) > 16 * 1024 * 1024:
                raise ValueError("derivation receipt too large")
        receipt = json.loads(body)
        if (
            receipt["source_sha256"] != marker["content_sha256"]
            or receipt["processor_version"] != VERSION
        ):
            raise ValueError("immutable derivation receipt conflict")
        derived = CreateRecordingUploadCommand.model_validate(receipt["command"])
        assets = {
            path: RobotIngestAsset.model_validate(value)
            for path, value in receipt["assets"].items()
        }
        for asset in assets.values():
            verify(
                storage,
                asset.object_key,
                asset.expected_size_bytes,
                asset.expected_sha256,
                progress,
            )
        return derived, assets
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
        config, roles = prepare(root, output, command, progress)
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
                    "expected_crc64": str(crc),
                    "actual_size_bytes": size,
                    "actual_sha256": sha,
                    "actual_crc64": str(crc),
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
                "schema_version": "continuous-recording-upload/v2",
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
