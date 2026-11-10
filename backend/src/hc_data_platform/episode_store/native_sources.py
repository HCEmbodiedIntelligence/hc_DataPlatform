"""Read episode-owned lossless capture; backfill old immutable versions once."""

import json
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from hc_data_platform.object_storage.immutable import MultipartImmutableWriter

from .models import FileEntry
from .native_capture import AUDIT, MANIFEST, MCAP, VERSION, build_native
from .native_plan import NativeFile


def reference(prefix, entry):
    entry = FileEntry.model_validate(entry)
    if entry.path not in {MCAP, AUDIT, MANIFEST}:
        raise ValueError("unexpected native capture object")
    return NativeFile.model_validate(
        {
            "relative_path": entry.path,
            "object": {
                "key": prefix.rstrip("/") + "/" + entry.path,
                "size": entry.size,
                "sha256": entry.sha256,
            },
        }
    )


def read_json(storage, key):
    body = bytearray()
    for block in storage.read_chunks(key):
        body.extend(block)
        if len(body) > 16 * 1024**2:
            raise ValueError("native capture receipt exceeds limit")
    return json.loads(body)


def native_source(
    package, storage, checkpoint=lambda: None, *, allow_backfill=False, verifier=None
):
    data = package.receipt.data
    prefix = data.package_prefix.rstrip("/")
    if any(e.path == MANIFEST for e in package.manifest.files):
        document = package.document(MANIFEST)
        declared = {e.path: e for e in package.manifest.files}
        if any(FileEntry.model_validate(e) != declared.get(e["path"]) for e in document["files"]):
            raise ValueError("native capture differs from committed package")
    else:
        # Historical packages are immutable. A checksummed extension is committed
        # below their existing prefix and bound to the exact data version.
        prefix += "/native-retained-v1"
        key = prefix + "/receipt.json"
        if allow_backfill and storage.head(prefix + "/verification.json") is None:
            backfill(package, storage, prefix, checkpoint, verifier=verifier)
        if storage.head(key) is None:
            if not allow_backfill:
                raise ValueError("native capture requires a verified migration")
            backfill(package, storage, prefix, checkpoint, verifier=verifier)
        receipt = read_json(storage, key)
        if receipt["data"] != data.model_dump(mode="json"):
            raise ValueError("native capture belongs to another data version")
        proof = read_json(storage, prefix + "/verification.json")
        if (
            proof.get("receipt") != receipt
            or proof.get("status") != "VERIFIED"
            or proof.get("method") != "holobrain-rows-sha256/v1"
        ):
            raise ValueError("native capture lacks a matching verification record")
        manifest_ref = reference(prefix, receipt["manifest"])
        from .original_export import copy_original

        with TemporaryDirectory(prefix="native-receipt-") as temporary:
            path = Path(temporary) / "manifest.json"
            copy_original(storage, manifest_ref, path, checkpoint)
            document = json.loads(path.read_bytes())
    if document["format"] != VERSION:
        raise ValueError("unsupported native capture")
    entries = {e["path"]: e for e in document["files"]}
    if len(entries) != len(document["files"]) or MCAP not in entries:
        raise ValueError("invalid native capture inventory")
    return (
        reference(prefix, entries[MCAP]),
        document["provenance"],
        reference(prefix, entries[AUDIT]) if AUDIT in entries else None,
    )


@contextmanager
def staged_native(package, storage, checkpoint=lambda: None):
    from .normalized_export import selected_source
    from .original_export import copy_original
    from .selected_cache import materialize_selected

    source = package.document("hc/source.json")
    file, provenance = selected_source(package)
    origin = provenance["source_origin_ns"]
    with TemporaryDirectory(prefix="native-backfill-") as temporary:
        root = Path(temporary)
        selected = root / "selected.mcap"
        materialize_selected(storage, file, provenance, selected, checkpoint)
        audit = None
        if provenance["selected_mcap"].get("normalization"):
            ref = next(
                e for e in source["inventory"] if e["relative_path"] == "source/normalization.json"
            )
            audit = root / "normalization.json"
            copy_original(storage, NativeFile.model_validate(ref), audit, checkpoint)
        entries = build_native(
            root,
            selected,
            provenance,
            origin + source["start_offset_ns"],
            origin + source["end_offset_ns"],
            audit=audit,
            checkpoint=checkpoint,
        )
        yield root, entries, selected


def backfill(package, storage, prefix, checkpoint, *, verifier=None):
    if verifier is None:
        raise ValueError("native migration requires pre-publication verification")
    with staged_native(package, storage, checkpoint) as (root, entries, selected):
        # Verification must complete before the first persistent write.
        result = verifier(selected, root / MCAP)
        if not isinstance(result, dict) or result.get("status") != "VERIFIED":
            raise ValueError("native migration verification did not pass")
        if result.get("method") != "holobrain-rows-sha256/v1":
            raise ValueError("unsupported native migration verification")
        receipt = {
            "data": package.receipt.data.model_dump(mode="json"),
            "manifest": next(e.model_dump(mode="json") for e in entries if e.path == MANIFEST),
        }
        receipt_key = prefix + "/receipt.json"
        if storage.head(receipt_key) is not None:
            if read_json(storage, receipt_key) != receipt:
                raise ValueError("existing native extension differs from verified staging")
            from .original_export import copy_original

            for index, entry in enumerate(entries):
                copy_original(
                    storage, reference(prefix, entry), root / f"verified-{index}", checkpoint
                )
        else:
            writer = MultipartImmutableWriter(storage)
            for entry in entries:
                with (root / entry.path).open("rb") as stream:
                    writer.write_immutable(
                        prefix + "/" + entry.path,
                        stream,
                        size=entry.size,
                        sha256=entry.sha256,
                        checkpoint=checkpoint,
                    )
            storage.put_json(receipt_key, receipt, if_none_match=True)
        storage.put_json(
            prefix + "/verification.json", {**result, "receipt": receipt}, if_none_match=True
        )


def export_source(package, storage, checkpoint=lambda: None):
    """Unmigrated immutable versions keep their old read path; exports never migrate."""
    if any(e.path == MANIFEST for e in package.manifest.files):
        return native_source(package, storage, checkpoint)
    from .normalized_export import selected_source

    file, provenance = selected_source(package)
    from .native_reference import export_provenance

    provenance = export_provenance(package, storage, provenance)
    entries = package.document("hc/source.json").get("inventory", ())
    audit = next(
        (
            NativeFile.model_validate(e)
            for e in entries
            if e["relative_path"] == "source/normalization.json"
        ),
        None,
    )
    return file, provenance, audit
