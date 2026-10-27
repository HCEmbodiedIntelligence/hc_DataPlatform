"""Versioned export adapters; upload format never selects the delivery writer.

Adding a format registers its contract and writer here. All adapters use the same
scoped selections, annotation snapshots, durable jobs, integrity checks and ZIP.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class ExportFormat:
    name: str
    archive: str
    title: str
    homogeneous: bool
    writer: Callable
    originals: bool = False
    reader_version: str = "original-bytes/1"


_FORMATS: dict[str, ExportFormat] = {}


def register(adapter):
    if not re.fullmatch(r"[a-z][a-z0-9_]*", adapter.name) or not re.fullmatch(
        r"[a-z][a-z0-9_]*_collection_zip/[1-9][0-9]*", adapter.archive
    ):
        raise ValueError("export adapter requires a versioned archive contract")
    if adapter.name in _FORMATS or any(a.archive == adapter.archive for a in _FORMATS.values()):
        raise ValueError("duplicate export adapter")
    _FORMATS[adapter.name] = adapter


def get_format(name):
    try:
        return _FORMATS[name]
    except KeyError as exc:
        raise ValueError(f"unsupported export format: {name}") from exc


def from_archive(archive):
    if archive == "mcap_collection_zip/1":
        return ExportFormat("mcap", archive, "MCAP（历史原话题）", False, legacy_mcap, True, "mcap/1")
    if archive == "mcap_collection_zip/2":
        return ExportFormat("mcap", archive, "MCAP（历史断段策略）", False, legacy_selected_mcap,
                            reader_version="hc-selected-mcap/2")
    if archive == "holobrain_collection_zip/1":
        return ExportFormat("holobrain", archive, "HoloBrain（历史断段策略）", False, legacy_holobrain,
                            reader_version="hc_tj_19dof_robo_orchard_0.1.0/v1")
    for adapter in _FORMATS.values():
        if adapter.archive == archive:
            return adapter
    raise ValueError(f"unsupported export archive: {archive}")


def formats():
    return [
        {
            "id": a.name,
            "title": a.title,
            "archive_format": a.archive,
            "supports_annotations": True,
            "requires_originals": a.originals,
        }
        for a in _FORMATS.values()
    ]


def lerobot(root, selections, *, storage, modalities, checkpoint, processor_version=None):
    from .collection_export import write_collection

    return write_collection(
        root,
        selections,
        modalities=modalities,
        checkpoint=checkpoint,
        processor_version=processor_version,
    )


def mcap(root, selections, *, storage, modalities, checkpoint):
    from .normalized_export import write_mcap_collection

    return write_mcap_collection(root, selections, storage=storage, checkpoint=checkpoint)


def legacy_mcap(root, selections, *, storage, modalities, checkpoint):
    from .original_export import write_mcap_collection
    return write_mcap_collection(root, selections, storage=storage, checkpoint=checkpoint)


def legacy_selected_mcap(root, selections, *, storage, modalities, checkpoint):
    from .normalized_export import write_mcap_collection
    return write_mcap_collection(root, selections, storage=storage, checkpoint=checkpoint,
                                 compact_invalid=False)


def holobrain(root, selections, *, storage, modalities, checkpoint):
    from .holobrain_export import write_collection
    return write_collection(root, selections, storage=storage, modalities=modalities, checkpoint=checkpoint)


def legacy_holobrain(root, selections, *, storage, modalities, checkpoint):
    from .holobrain_export import write_collection
    return write_collection(root, selections, storage=storage, modalities=modalities,
                            checkpoint=checkpoint, compact_invalid=False)


def original(root, selections, *, storage, modalities, checkpoint):
    from .original_export import write_original_collection

    return write_original_collection(root, selections, storage=storage, checkpoint=checkpoint)


register(
    ExportFormat(
        "lerobot",
        "lerobot_collection_zip/1",
        "LeRobot",
        True,
        lerobot,
        reader_version="lerobot/0.6.1",
    )
)
register(
    ExportFormat(
        "mcap",
        "mcap_collection_zip/3",
        "MCAP",
        False,
        mcap,
        reader_version="hc-selected-mcap/2",
    )
)
register(ExportFormat("original", "original_collection_zip/1", "原始文件", False, original, True))
register(ExportFormat("holobrain", "holobrain_collection_zip/2", "HoloBrain（19维，含腰部）", False, holobrain,
                      reader_version="hc_tj_19dof_robo_orchard_0.1.0/v1"))
