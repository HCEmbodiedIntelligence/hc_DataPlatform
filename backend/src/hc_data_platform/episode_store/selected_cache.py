"""Materialize a frozen selected MCAP in job-local storage, never another primary."""

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from .native_plan import NativeFile

RETENTION = "rebuild-on-demand/v1"
SELECTED_PATH = "source/selected.mcap"


def freeze_selection(provenance, prefix):
    selected = provenance.get("selected_mcap")
    if selected:
        selected["retention"] = RETENTION
        selected["object_key"] = f"{prefix}/{selected['sha256']}/{SELECTED_PATH}"


def selected_reference(provenance):
    selected = provenance["selected_mcap"]
    if selected.get("retention") != RETENTION or selected.get("path") != SELECTED_PATH:
        raise ValueError("MCAP_SELECTION_REQUIRED: no frozen reconstruction recipe")
    return NativeFile.model_validate(
        {
            "relative_path": SELECTED_PATH,
            "object": {
                "key": selected["object_key"],
                "size": selected["size_bytes"],
                "sha256": selected["sha256"],
            },
        }
    )


def original_reference(provenance):
    original = provenance["source_object"]
    if original["sha256"] != provenance["selected_mcap"]["source_sha256"]:
        raise ValueError("MCAP_REBUILD_SOURCE_MISMATCH")
    return NativeFile.model_validate(
        {
            "relative_path": "source/original/source.mcap",
            "object": {
                "key": original["object_key"],
                "size": original["size"],
                "sha256": original["sha256"],
            },
        }
    )


def materialize_selected(
    storage, file, provenance, destination, checkpoint=lambda: None, *, rebuild=False
):
    from hc_data_platform.continuous_recordings.mcap_contracts import McapProcessingConfig
    from hc_data_platform.continuous_recordings.mcap_selection import select_mcap

    from .original_export import copy_original

    destination = Path(destination)
    if not rebuild and storage.head(file.object.key) is not None:
        try:
            copy_original(storage, file, destination, checkpoint)
            return
        except Exception:
            if storage.head(file.object.key) is not None:
                raise
            destination.unlink(missing_ok=True)
    selected = provenance["selected_mcap"]
    if (file.relative_path, file.object.sha256, file.object.size) != (
        SELECTED_PATH,
        selected["sha256"],
        selected["size_bytes"],
    ):
        raise ValueError("MCAP_REBUILD_SELECTION_MISMATCH")
    original = original_reference(provenance)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="mcap-rebuild-", dir=destination.parent) as directory:
        raw = Path(directory) / "original.mcap"
        copy_original(storage, original, raw, checkpoint)
        cfg = McapProcessingConfig.model_validate(provenance["config"])
        select_mcap(
            raw,
            destination,
            cfg,
            checkpoint,
            metadata_output=selected.get("format_metadata_output"),
        )
    h = hashlib.sha256()
    with destination.open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            checkpoint()
            h.update(block)
    if destination.stat().st_size != file.object.size or h.hexdigest() != file.object.sha256:
        destination.unlink()
        raise ValueError("MCAP_REBUILD_HASH_MISMATCH: frozen parser output changed")
