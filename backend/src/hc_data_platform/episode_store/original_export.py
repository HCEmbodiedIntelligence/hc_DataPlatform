"""Original-byte delivery and schema-preserving MCAP slices from frozen lineage."""

import hashlib
import json
from pathlib import Path

from .models import canonical
from .native_plan import NativeFile


def originals(package, storage=None):
    source = package.document("hc/source.json")
    entries = source.get("original_inventory") or source.get("inventory")
    if not entries and source.get("raw_upload_id") and storage is not None:
        from hc_data_platform.ingest.raw_sources import PostgresRawSourceRepository
        from hc_data_platform.lerobot_imports.configuration import connections
        from hc_data_platform.lerobot_imports.source_browser import OriginalSourceBrowser

        browser = OriginalSourceBrowser(storage, PostgresRawSourceRepository(connections()))
        identity = package.receipt.data.identity
        raw = browser.source(*identity.scope, source["raw_upload_id"])
        if raw.dataset_id != identity.dataset_id:
            raise PermissionError("original dataset differs from package")
        entries = [
            {
                "relative_path": f["path"],
                "object": {
                    "key": f.get("object_key") or raw.storage_prefix + "/" + f["path"],
                    "size": f["size"],
                    "sha256": f["sha256"],
                },
            }
            for f in browser.manifest(raw)["files"]
        ]
    if not entries:
        raise ValueError("ORIGINAL_SOURCE_UNAVAILABLE: package has no verified original inventory")
    files = tuple(NativeFile.model_validate(e) for e in entries)
    if len({f.relative_path for f in files}) != len(files):
        raise ValueError("duplicate original paths")
    # Native MCAP derivation retains its input alongside derived preview assets.
    raw = tuple(f for f in files if f.relative_path.startswith("source/original/"))
    return raw or files


def copy_original(storage, file, destination, checkpoint):
    destination.parent.mkdir(parents=True, exist_ok=True)
    sha, size = hashlib.sha256(), 0
    with destination.open("xb") as output:
        for block in storage.read_chunks(file.object.key):
            checkpoint()
            size += len(block)
            if size > file.object.size:
                raise ValueError("original exceeds committed size")
            sha.update(block)
            output.write(block)
    if size != file.object.size or sha.hexdigest() != file.object.sha256:
        raise ValueError("original differs from committed hash")


def selection_document(selection):
    source = selection.package.document("hc/source.json")
    return {
        "format": "hc-original-selection/1",
        "data": selection.package.receipt.data.model_dump(mode="json"),
        "source": source,
        "annotations": json.loads(selection.annotation_file.content)
        if selection.annotation_file
        else None,
        "review": selection.review_document,
        "annotation_coordinates": "episode steps, half-open [start_step,end_step)",
        "original_policy": "unchanged source bytes; selection and annotations are sidecars",
    }


def inventory(root, members, format):
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        sha = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024**2), b""):
                sha.update(block)
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
                "sha256": sha.hexdigest(),
            }
        )
    return {"format": format, "members": members, "files": files}, None


def write_original_collection(root, selections, *, storage, checkpoint=lambda: None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    members, seen = [], {}
    for index, selected in enumerate(selections):
        references = []
        for file in originals(selected.package, storage):
            key = (file.object.key, file.object.sha256, file.relative_path)
            if key not in seen:
                target = f"sources/{len(seen):06d}/{file.relative_path}"
                copy_original(storage, file, root / target, checkpoint)
                seen[key] = target
            references.append(
                {
                    "path": seen[key],
                    "original_path": file.relative_path,
                    "size": file.object.size,
                    "sha256": file.object.sha256,
                }
            )
        sidecar = f"selections/{index:06d}.json"
        (root / sidecar).parent.mkdir(exist_ok=True)
        (root / sidecar).write_bytes(
            canonical({**selection_document(selected), "files": references})
        )
        # Exact source-frame mappings bridge annotation frame coordinates to raw timestamps.
        for entry in selected.package.manifest.files:
            if entry.path in {"hc/recording/source_frames.jsonl", "hc/source_frames.parquet"}:
                target = root / f"selections/{index:06d}-{Path(entry.path).name}"
                with (
                    selected.package.file(entry.path) as source,
                    source.open("rb") as stream,
                    target.open("wb") as output,
                ):
                    for block in iter(lambda: stream.read(1024**2), b""):
                        checkpoint()
                        output.write(block)
        members.append(
            {
                "data": selected.package.receipt.data.model_dump(mode="json"),
                "selection": sidecar,
                "files": references,
            }
        )
    return inventory(root, members, "hc-original-collection/1")


def original_mcap_window(package):
    source = package.document("hc/source.json")
    if source.get("mcap", {}).get("source_profile") == "hc-episode-mcap/1":
        return None
    if "start_offset_ns" not in source:
        return None
    files = originals(package)
    mcaps = [f for f in files if f.relative_path.lower().endswith(".mcap")]
    ros_originals = [f for f in mcaps if Path(f.relative_path).name == "originals.mcap"]
    if ros_originals:
        mcaps = ros_originals
    if len(mcaps) != 1:
        return None
    origin = source.get("clock", {}).get("recording_origin_ns")
    if origin is None:
        return None
    return mcaps[0], int(origin) + source["start_offset_ns"], int(origin) + source["end_offset_ns"]


def require_original_mcap(package):
    from .delivery_repository import failure

    window = original_mcap_window(package)
    if window is None:
        raise failure(
            "MCAP_ORIGINAL_REQUIRED",
            "MCAP 导出需要保留原始 MCAP 和切片时间映射；LeRobot 数据不支持转换为 MCAP。",
            422,
        )
    return window


def validate_original_mcap(path):
    from mcap.reader import make_reader

    from .delivery_repository import failure

    with Path(path).open("rb") as stream:
        if make_reader(stream, validate_crcs=True).get_header().profile == "hc-episode-mcap/1":
            raise failure("MCAP_ORIGINAL_REQUIRED", "LeRobot 数据不支持转换为 MCAP。", 422)


def write_mcap_collection(root, selections, *, storage, checkpoint=lambda: None):
    from .mcap_export import slice_original

    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    members = []
    for index, selected in enumerate(selections):
        checkpoint()
        file, start, end = require_original_mcap(selected.package)
        target = root / f"episode-{index:06d}.mcap"
        document = selection_document(selected)
        for entry in selected.package.manifest.files:
            if entry.path == "hc/source_frames.parquet":
                sidecar = f"episode-{index:06d}-source-frames.parquet"
                import shutil

                with selected.package.file(entry.path) as local_mapping:
                    shutil.copyfile(local_mapping, root / sidecar)
                document["annotation_frame_mapping"] = sidecar
        # Keep the complete original outside the exported archive.
        from tempfile import TemporaryDirectory

        with TemporaryDirectory(dir=root.parent) as temp:
            local = Path(temp) / "source.mcap"
            copy_original(storage, file, local, checkpoint)
            validate_original_mcap(local)
            slice_original(
                local,
                target,
                start_ns=start,
                end_ns=end,
                annotation=document,
                checkpoint=checkpoint,
            )
        policy = "original_topics_schemas_and_message_bytes"
        members.append(
            {
                "data": selected.package.receipt.data.model_dump(mode="json"),
                "path": target.name,
                "topic_policy": policy,
            }
        )
    return inventory(root, members, "hc-mcap-collection/1")
