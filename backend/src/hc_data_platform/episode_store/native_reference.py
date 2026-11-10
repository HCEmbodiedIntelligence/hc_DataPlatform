"""Frozen Episode source references reuse immutable Raw bytes instead of copying images."""

import copy
from pathlib import Path
from tempfile import TemporaryDirectory

from .models import canonical
from .native_capture import file_entry
from .selected_cache import materialize_selected, original_reference

PATH = "hc/recording/native-reference.json"
FORMAT = "episode-native-reference/1"


def build_reference(root, provenance, start_ns, end_ns):
    original = original_reference(provenance)
    document = {
        "format": FORMAT,
        "original": original.model_dump(mode="json"),
        "start_ns": start_ns,
        "end_ns": end_ns,
        "selected_sha256": provenance["selected_mcap"]["sha256"],
    }
    target = Path(root) / PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(canonical(document))
    return file_entry(Path(root), PATH)


def evidence(package):
    from .normalized_export import selected_source

    _, provenance = selected_source(package)
    return {
        "data": package.receipt.data.model_dump(mode="json"),
        "source_document_sha256": next(
            e.sha256 for e in package.manifest.files if e.path == "hc/source.json"
        ),
        "original": original_reference(provenance).model_dump(mode="json"),
        "selected_sha256": provenance["selected_mcap"]["sha256"],
    }


def ready(package, storage):
    expected = evidence(package)
    if any(e.path == PATH for e in package.manifest.files):
        document = package.document(PATH)
        if (
            document.get("format") != FORMAT
            or document.get("original") != expected["original"]
            or document.get("selected_sha256") != expected["selected_sha256"]
        ):
            raise ValueError("native source reference differs from the committed Episode")
        return True
    key = package.receipt.data.package_prefix.rstrip("/") + "/native-reference-v1/verification.json"
    if storage.head(key) is None:
        return False
    from .native_sources import read_json

    document = read_json(storage, key)
    return {k: v for k, v in document.items() if k != "selection_metadata_output"} == {
        "format": FORMAT,
        **expected,
    }


def verify_reference(package, storage, checkpoint=lambda: None):
    """Publish only a small proof after Raw reconstructs the exact frozen selection."""
    from .normalized_export import selected_source

    expected = evidence(package)
    file, original_provenance = selected_source(package)
    provenance = copy.deepcopy(original_provenance)
    with TemporaryDirectory(prefix="native-reference-check-") as temporary:
        from mcap.reader import make_reader

        from .original_export import copy_original

        before = Path(temporary) / "existing.mcap"
        copy_original(storage, file, before, checkpoint)
        with before.open("rb") as stream:
            metadata = next(
                m.metadata
                for m in make_reader(stream).iter_metadata()
                if m.name == "hc.capture.format"
            )
        provenance["selected_mcap"]["format_metadata_output"] = metadata["output"]
        materialize_selected(
            storage, file, provenance, Path(temporary) / "selected.mcap", checkpoint, rebuild=True
        )
    key = package.receipt.data.package_prefix.rstrip("/") + "/native-reference-v1/verification.json"
    document = {
        "format": FORMAT,
        **expected,
        "selection_metadata_output": provenance["selected_mcap"]["format_metadata_output"],
    }
    if storage.head(key) is not None:
        from .native_sources import read_json

        if read_json(storage, key) != document:
            raise ValueError("native reference proof differs from this Episode")
    else:
        storage.put_json(key, document, if_none_match=True)
    return document


def export_provenance(package, storage, provenance):
    key = package.receipt.data.package_prefix.rstrip("/") + "/native-reference-v1/verification.json"
    if storage.head(key) is None:
        return provenance
    if not ready(package, storage):
        raise ValueError("native source proof differs from the committed Episode")
    from .native_sources import read_json

    document = read_json(storage, key)
    result = copy.deepcopy(provenance)
    result["selected_mcap"]["format_metadata_output"] = document["selection_metadata_output"]
    return result


def retire_redundant_supplement(package, storage):
    """Remove only the obsolete verified migration's exact extra files."""
    from hc_data_platform.object_storage.immutable import verify_object

    from .native_capture import AUDIT, MANIFEST, MCAP
    from .native_sources import read_json, reference

    if any(e.path == MANIFEST for e in package.manifest.files) or not ready(package, storage):
        raise ValueError("a verified Raw reference is required; primary payloads are protected")
    expected = evidence(package)
    original = expected["original"]["object"]
    verify_object(storage, original["key"], original["size"], original["sha256"])
    prefix = package.receipt.data.package_prefix.rstrip("/") + "/native-retained-v1"
    marker = (
        package.receipt.data.package_prefix.rstrip("/")
        + "/native-reference-v1/retired-supplement.json"
    )
    if storage.head(marker) is None:
        receipt = read_json(storage, prefix + "/receipt.json")
        proof = read_json(storage, prefix + "/verification.json")
        if (
            receipt["data"] != expected["data"]
            or proof.get("receipt") != receipt
            or proof.get("status") != "VERIFIED"
        ):
            raise ValueError("supplement verification is missing or belongs to another version")
        manifest = reference(prefix, receipt["manifest"])
        verify_object(storage, manifest.object.key, manifest.object.size, manifest.object.sha256)
        document = read_json(storage, manifest.object.key)
        entries = document["files"] + [receipt["manifest"]]
        if {e["path"] for e in entries} != {MCAP, AUDIT, MANIFEST}:
            raise ValueError("unexpected supplemental payload inventory")
        objects = []
        for entry in entries:
            ref = reference(prefix, entry).object
            verify_object(storage, ref.key, ref.size, ref.sha256)
            objects.append({"key": ref.key, "size": ref.size, "sha256": ref.sha256})
        import hashlib

        for name in ("receipt.json", "verification.json"):
            key = prefix + "/" + name
            value = b"".join(storage.read_chunks(key))
            objects.append(
                {"key": key, "size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
            )
        storage.put_json(
            marker, {"format": FORMAT, "evidence": expected, "objects": objects}, if_none_match=True
        )
    plan = read_json(storage, marker)
    if plan["evidence"] != expected:
        raise ValueError("retirement proof differs from current immutable version")
    allowed = {
        prefix + "/" + name for name in (MCAP, AUDIT, MANIFEST, "receipt.json", "verification.json")
    }
    reclaimed = 0
    for item in plan["objects"]:
        if item["key"] not in allowed:
            raise ValueError("retirement object escaped the exact supplemental inventory")
        if storage.head(item["key"]) is not None:
            verify_object(storage, item["key"], item["size"], item["sha256"])
            storage.delete_object(item["key"])
            if storage.head(item["key"]) is not None:
                raise RuntimeError("supplement deletion did not complete")
            reclaimed += item["size"]
    return reclaimed
