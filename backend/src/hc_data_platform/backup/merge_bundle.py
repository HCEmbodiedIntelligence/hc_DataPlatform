"""Portable business-data bundles with bounded I/O and non-overwriting S3 import."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from botocore.exceptions import ClientError
from psycopg import Connection

from hc_data_platform.backup.merge_database import (
    MergeError,
    canonical,
    dump_rows,
    lock_tables,
    schema_description,
    schema_digest,
    validate_audit_chains,
)

FORMAT = "hc-platform-merge/v1"
CHUNK = 8 * 1024 * 1024
MAX_OBJECT_BYTES = 5 * 1024**3
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_OBJECTS = 200_000


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def _private_file(path: Path) -> Any:
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")


def write_json(path: Path, value: Any) -> None:
    with _private_file(path) as stream:
        stream.write(canonical(value) + b"\n")


def _inventory(client: Any, bucket: str) -> list[dict[str, Any]]:
    if client.get_bucket_versioning(Bucket=bucket).get("Status"):
        raise MergeError("MERGE_VERSIONED_BUCKET_UNSUPPORTED")
    if client.list_multipart_uploads(Bucket=bucket, MaxUploads=1).get("Uploads"):
        raise MergeError("MERGE_MULTIPART_UPLOAD_ACTIVE")
    records = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            if obj["Size"] > MAX_OBJECT_BYTES:
                raise MergeError("MERGE_OBJECT_EXCEEDS_5_GIB")
            records.append(
                {
                    "key": obj["Key"],
                    "size": obj["Size"],
                    "etag": obj["ETag"],
                    "modified": obj["LastModified"].isoformat(),
                }
            )
            if len(records) > MAX_OBJECTS:
                raise MergeError("MERGE_OBJECT_COUNT_LIMIT")
    return sorted(records, key=lambda item: item["key"])


def export_bundle(
    connection: Connection[Any],
    client: Any,
    bucket: str,
    directory: Path,
    *,
    credential_key: str,
    progress: Any = None,
) -> dict[str, Any]:
    """Hold a consistent DB snapshot; reject any object inventory drift before sealing."""
    if directory.exists():
        raise MergeError("MERGE_EXPORT_DIRECTORY_EXISTS")
    directory.mkdir(mode=0o700, parents=True)
    (directory / "tables").mkdir(mode=0o700)
    (directory / "objects").mkdir(mode=0o700)
    manifest: dict[str, Any] = {
        "format": FORMAT,
        "bundle_id": str(uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "bucket": bucket,
        "credential_key_sha256": hashlib.sha256(credential_key.encode()).hexdigest(),
        "tables": {},
        "objects": [],
    }
    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        description = schema_description(connection)
        lock_tables(connection, description["tables"], writing=False)
        validate_audit_chains(connection)
        manifest["schema_sha256"] = schema_digest(description)
        manifest["schema"] = description
        inventory = _inventory(client, bucket)
        for index, name in enumerate(sorted(description["tables"])):
            relative = f"tables/{index:04d}.jsonl.gz"
            path = directory / relative
            count = dump_rows(connection, name, path)
            path.chmod(0o600)
            manifest["tables"][name] = {
                "path": relative,
                "rows": count,
                "sha256": file_hash(path),
                "bytes": path.stat().st_size,
            }
        for index, obj in enumerate(inventory):
            relative = "objects/" + hashlib.sha256(obj["key"].encode()).hexdigest()
            response = client.get_object(Bucket=bucket, Key=obj["key"], IfMatch=obj["etag"])
            digest, size = hashlib.sha256(), 0
            body = response["Body"]
            try:
                with _private_file(directory / relative) as output:
                    while block := body.read(CHUNK):
                        size += len(block)
                        if size > obj["size"]:
                            raise MergeError("MERGE_OBJECT_CHANGED_DURING_EXPORT")
                        digest.update(block)
                        output.write(block)
            finally:
                body.close()
            if size != obj["size"] or response.get("ETag") != obj["etag"]:
                raise MergeError("MERGE_OBJECT_CHANGED_DURING_EXPORT")
            manifest["objects"].append(
                {
                    "key": obj["key"],
                    "path": relative,
                    "bytes": size,
                    "sha256": digest.hexdigest(),
                    "content_type": response.get("ContentType", "application/octet-stream"),
                    "metadata": response.get("Metadata", {}),
                }
            )
            if progress and (index % 100 == 0 or index == len(inventory) - 1):
                progress(
                    {"stage": "export_objects", "completed": index + 1, "total": len(inventory)}
                )
        if _inventory(client, bucket) != inventory:
            raise MergeError("MERGE_OBJECT_INVENTORY_CHANGED")
    # A partial export never has a manifest and cannot be imported.
    write_json(directory / "manifest.json", manifest)
    with _private_file(directory / "manifest.sha256") as output:
        output.write((file_hash(directory / "manifest.json") + "\n").encode())
    return {
        "status": "exported",
        "bundle_id": manifest["bundle_id"],
        "tables": len(manifest["tables"]),
        "objects": len(manifest["objects"]),
        "object_bytes": sum(obj["bytes"] for obj in manifest["objects"]),
    }


def _path(directory: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not re.fullmatch(
        r"(?:tables/[0-9]{4}\.jsonl\.gz|objects/[0-9a-f]{64})", relative
    ):
        raise MergeError("MERGE_BUNDLE_PATH_INVALID")
    path = directory / relative
    if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
        raise MergeError("MERGE_BUNDLE_PATH_INVALID")
    return path


def load_bundle(directory: Path, *, bucket: str, credential_key: str) -> dict[str, Any]:
    path = directory / "manifest.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_MANIFEST_BYTES:
        raise MergeError("MERGE_MANIFEST_INVALID")
    expected = (directory / "manifest.sha256").read_text().strip()
    if file_hash(path) != expected:
        raise MergeError("MERGE_MANIFEST_CHECKSUM_INVALID")
    manifest: dict[str, Any] = json.loads(path.read_bytes())
    if (
        manifest.get("format") != FORMAT
        or manifest.get("bucket") != bucket
        or not re.fullmatch(r"[0-9a-f-]{36}", manifest.get("bundle_id", ""))
    ):
        raise MergeError("MERGE_FORMAT_OR_BUCKET_MISMATCH")
    if manifest.get("credential_key_sha256") != hashlib.sha256(credential_key.encode()).hexdigest():
        raise MergeError("MERGE_DATA_SOURCE_CREDENTIAL_KEY_MISMATCH")
    if schema_digest(manifest["schema"]) != manifest["schema_sha256"]:
        raise MergeError("MERGE_SCHEMA_CHECKSUM_INVALID")
    if set(manifest["tables"]) != set(manifest["schema"]["tables"]):
        raise MergeError("MERGE_TABLE_INVENTORY_INVALID")
    if len(manifest["objects"]) > MAX_OBJECTS:
        raise MergeError("MERGE_OBJECT_COUNT_LIMIT")
    entries = [*manifest["tables"].values(), *manifest["objects"]]
    paths: set[str] = set()
    keys: set[str] = set()
    for item in manifest["objects"]:
        key = item["key"]
        if not isinstance(key, str) or not key or len(key.encode()) > 1024 or key in keys:
            raise MergeError("MERGE_OBJECT_INVENTORY_INVALID")
        keys.add(key)
        if item["bytes"] > MAX_OBJECT_BYTES:
            raise MergeError("MERGE_OBJECT_EXCEEDS_5_GIB")
    for item in entries:
        relative = item["path"]
        if relative in paths:
            raise MergeError("MERGE_BUNDLE_PATH_DUPLICATED")
        paths.add(relative)
        artifact = _path(directory, relative)
        if artifact.stat().st_size != item["bytes"] or file_hash(artifact) != item["sha256"]:
            raise MergeError("MERGE_ARTIFACT_CHECKSUM_INVALID")
    return manifest


def _existing_object(client: Any, bucket: str, item: dict[str, Any]) -> bool:
    try:
        response = client.get_object(Bucket=bucket, Key=item["key"])
    except ClientError as exc:
        if exc.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise
    body, digest, size = response["Body"], hashlib.sha256(), 0
    try:
        if response["ContentLength"] != item["bytes"]:
            raise MergeError("MERGE_OBJECT_CONTENT_CONFLICT")
        while block := body.read(CHUNK):
            size += len(block)
            if size > item["bytes"]:
                raise MergeError("MERGE_OBJECT_CONTENT_CONFLICT")
            digest.update(block)
    finally:
        body.close()
    if size != item["bytes"] or digest.hexdigest() != item["sha256"]:
        raise MergeError("MERGE_OBJECT_CONTENT_CONFLICT")
    return True


def check_objects(client: Any, bucket: str, manifest: dict[str, Any]) -> dict[str, int]:
    _inventory(client, bucket)
    same = 0
    missing_bytes = 0
    for item in manifest["objects"]:
        if _existing_object(client, bucket, item):
            same += 1
        else:
            missing_bytes += item["bytes"]
    return {
        "existing_identical": same,
        "to_copy": len(manifest["objects"]) - same,
        "bytes_to_copy": missing_bytes,
    }


def copy_objects(
    client: Any,
    bucket: str,
    directory: Path,
    manifest: dict[str, Any],
    *,
    progress: Any = None,
) -> dict[str, int]:
    copied = 0
    for index, item in enumerate(manifest["objects"]):
        if _existing_object(client, bucket, item):
            continue
        with (directory / item["path"]).open("rb") as source:
            try:
                client.put_object(
                    Bucket=bucket,
                    Key=item["key"],
                    Body=source,
                    ContentLength=item["bytes"],
                    ContentType=item["content_type"],
                    Metadata=item["metadata"],
                    IfNoneMatch="*",
                )
            except ClientError as exc:
                if exc.response["Error"]["Code"] not in {"PreconditionFailed", "412"}:
                    raise
                # Another writer won the conditional put. Accept only exact content.
        if not _existing_object(client, bucket, item):
            raise MergeError("MERGE_OBJECT_COPY_NOT_VISIBLE")
        copied += 1
        if progress and (index % 100 == 0 or index == len(manifest["objects"]) - 1):
            progress(
                {
                    "stage": "import_objects",
                    "completed": index + 1,
                    "total": len(manifest["objects"]),
                }
            )
    return {"copied": copied, "verified": len(manifest["objects"])}
