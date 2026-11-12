"""Freeze OSS versions at registration and seal the file digest on its first QC read."""

import hashlib
import json
import re


def receipt_key(root, path):
    return f"{root}/verified-mcap/{hashlib.sha256(path.encode()).hexdigest()}.json"


def read_receipt(storage, entry):
    from .service import read_bounded

    if not storage.head(entry["verification_key"]):
        return None
    receipt = json.loads(read_bounded(storage, entry["verification_key"], 16 * 1024))
    if (receipt.get("source_etag") != entry["source_etag"] or receipt.get("size") != entry["size"]
            or receipt.get("object_key") != entry["object_key"]
            or not re.fullmatch("[0-9a-f]{64}", str(receipt.get("sha256")))):
        raise ValueError("MCAP verification receipt differs from its registered OSS version")
    return receipt["sha256"]


def require_version(storage, key, size, etag):
    meta = storage.head(key)
    if meta is None or meta.size != size or meta.etag != etag:
        raise ValueError("OSS_IMPORT_SOURCE_CHANGED: registered OSS object version or size changed")


def materialize(storage, entry, target, checkpoint):
    from hc_data_platform.robot_ingest.raw_recording import verify

    if "verification_key" not in entry:
        verify(storage, entry["object_key"], entry["size"], entry["sha256"], checkpoint, target)
        return entry
    key, size, etag = entry["object_key"], entry["size"], entry["source_etag"]
    from hc_data_platform.storage.oss_zip import is_member

    member_reader = getattr(storage, "read_registered_zip_member", None) if is_member(key) else None
    if member_reader is None:
        require_version(storage, key, size, etag)
        body = storage.read_chunks(key)
    else:
        # The member size is checked against its directory and every range GET is conditional.
        body = member_reader(key, size, etag)
    digest, count = hashlib.sha256(), 0
    for block in body:
        count += len(block)
        if count > size:
            raise ValueError("OSS_IMPORT_SOURCE_CHANGED: source exceeds registered size")
        digest.update(block)
        if target is not None:
            target.write(block)
        checkpoint()
    if count != size:
        raise ValueError("OSS_IMPORT_SOURCE_CHANGED: source length changed during verification")
    if member_reader is None:
        require_version(storage, key, size, etag)
    sha = digest.hexdigest()
    if entry.get("sha256") is not None and sha != entry["sha256"]:
        raise ValueError("MCAP object differs from its verified SHA-256")
    if target is None:
        return {**entry, "sha256": sha}
    receipt = {"object_key": key, "size": size, "source_etag": etag, "sha256": sha}
    try:
        storage.put_json(entry["verification_key"], receipt, if_none_match=True)
    except Exception:
        if read_receipt(storage, entry) != sha:
            raise
    return {**entry, "sha256": sha}
