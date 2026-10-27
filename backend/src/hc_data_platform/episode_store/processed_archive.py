"""Prepare old independent packages for a new training archive without mutating them."""

import shutil
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pyarrow.parquet as pq

from hc_data_platform.data_formats.lerobot_layout import DATA_PATH

from .capture_selection import compact_capture_rows
from .frame_policy import PROCESSING_POLICY, usable_row
from .models import canonical, digest
from .validation import validate_package


@contextmanager
def processed_archive(package, directory, checkpoint):
    with package.file(DATA_PATH) as path:
        needs_selection = any(
            not usable_row(row)
            for batch in pq.ParquetFile(path).iter_batches()
            for row in batch.to_pylist()
        )
    if not needs_selection:
        yield package, False
        return
    with TemporaryDirectory(prefix="processed-archive-", dir=directory) as temporary:
        root = Path(temporary)
        for entry in package.manifest.files:
            checkpoint()
            target = root / entry.path
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.file(entry.path) as path:
                shutil.copyfile(path, target)
        for relative, document in (
            ("hc/parent_manifest.json", package.manifest),
            ("hc/parent_commit.json", package.receipt),
        ):
            (root / relative).write_bytes(canonical(document.model_dump(mode="json")))
        compact_capture_rows(root)
        source = package.manifest.input.model_copy(
            update={
                "processor_version": "hc-episode-archive/2",
                "configuration_sha256": digest(
                    {
                        "parent": package.receipt.manifest_sha256,
                        "frame_selection_policy": PROCESSING_POLICY,
                    }
                ),
            }
        )
        manifest = validate_package(root, source)

        @contextmanager
        def file(relative):
            yield root / relative

        yield SimpleNamespace(manifest=manifest, file=file, receipt=package.receipt), True
