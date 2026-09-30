"""Bounded read-only expansion and hashing of inventoried physical objects.

Operator-only input: a captured inventory, not public caller-supplied object keys.
This verifies bytes, not sensor semantics, review equivalence or migration readiness.
"""

import hashlib
import json

from hc_data_platform.ingest.models import RolloutManifestV1
from hc_data_platform.ingest.raw_sources import RawSource
from hc_data_platform.lerobot_imports.committed import read_committed_manifest
from hc_data_platform.lerobot_imports.reader import safe_relative
from hc_data_platform.lerobot_imports.service import _raw_content_digest

from .collections import collection_scope
from .migration_inventory import FORMAT, InventoryLimit
from .models import CollectionSnapshot, CommitReceipt, FileEntry, PackageManifest, canonical, digest
from .postgres import PackageConflict, assert_scope


class _ManifestBytes:
    """Feed already bounded immutable bytes into the existing source normalizer."""

    def __init__(self, key, body):
        self.key, self.body = key, body

    def read_chunks(self, key, chunk_size=8 * 1024**2):
        if key != self.key:
            raise ValueError("manifest normalizer requested an unrelated object")
        yield self.body


def validate_raw_file_set(raw, manifest):
    """Recognize the historical web label without changing its hashed inventory.

    The pre-656853ae writer emitted ``lerobot`` in the manifest but hashed
    ``lerobot_v3``/``v3.0``. This is one documented alias, not case folding or
    permission to accept another format's digest.
    """
    declared = manifest.get("source_format")
    historical = (
        declared == "lerobot"
        and manifest.get("source_format_version") == "v3.0"
        and raw.source_format.value == "LEROBOT_V3"
        and raw.source_format_version == "v3.0"
    )
    if (
        not (declared == raw.source_format.value or historical)
        or _raw_content_digest(manifest["files"], raw.source_format.value) != raw.content_hash
    ):
        raise PackageConflict("raw file set hash/format differs from inventory")


class InventoryObjectVerifier:
    def __init__(
        self,
        storage,
        *,
        max_objects=100000,
        max_total_bytes=32 * 1024**3,
        max_manifest_bytes=16 * 1024**2,
    ):
        if any(
            type(n) is not int or n < 1 for n in (max_objects, max_total_bytes, max_manifest_bytes)
        ):
            raise ValueError("physical inventory limits must be positive integers")
        self.storage = storage
        self.max_objects, self.max_bytes, self.max_manifest = (
            max_objects,
            max_total_bytes,
            max_manifest_bytes,
        )
        self.objects, self.issues, self.manifests = {}, [], []
        self.reserved_bytes = 0

    def _read(self, key, expected_size, *, capture=False):
        checksum, size, body = hashlib.sha256(), 0, bytearray()
        for chunk in self.storage.read_chunks(key):
            size += len(chunk)
            if size > expected_size:
                raise PackageConflict("object exceeds its frozen size")
            checksum.update(chunk)
            if capture:
                body.extend(chunk)
        if size != expected_size:
            raise PackageConflict("object is truncated")
        return checksum.hexdigest(), bytes(body)

    def _reserve(self, key, size):
        if key in self.objects:
            return
        if len(self.objects) >= self.max_objects or self.reserved_bytes + size > self.max_bytes:
            raise InventoryLimit("physical inventory exceeds explicit count/byte limits")
        self.reserved_bytes += size

    def _issue(self, kind, identity, code):
        self.issues.append({"kind": kind, "identity": identity, "code": code})

    def object(self, key, size, sha256, origin):
        # Reuse strict hash/positive-size checks without treating object keys as local paths.
        FileEntry(path="object", size=size, sha256=sha256, media_type="application/octet-stream")
        if not isinstance(key, str) or not key or len(key) > 2048:
            raise ValueError("invalid inventoried object key")
        old = self.objects.get(key)
        if old is not None:
            if (old["size"], old["sha256"]) != (size, sha256):
                self._issue("object", key, "CONFLICTING_FROZEN_OBJECT_REFERENCES")
                return
            if origin not in old["references"]:
                old["references"].append(origin)
            return
        self._reserve(key, size)
        record = {
            "key": key,
            "size": size,
            "sha256": sha256,
            "references": [origin],
            "verified": False,
        }
        self.objects[key] = record
        head = self.storage.head(key)
        if head is None or head.size != size:
            self._issue("object", key, "OBJECT_MISSING_OR_SIZE_CHANGED")
            return
        try:
            observed, _ = self._read(key, size)
        except PackageConflict:
            self._issue("object", key, "OBJECT_STREAM_SIZE_CHANGED")
            return
        record["observed_sha256"] = observed
        if observed != sha256:
            self._issue("object", key, "OBJECT_HASH_CHANGED")
        else:
            record["verified"] = True

    def manifest(self, key, origin, *, sha256=None):
        head = self.storage.head(key)
        if head is None:
            raise PackageConflict("source manifest is missing")
        if not 0 < head.size <= self.max_manifest:
            raise InventoryLimit("source manifest exceeds the explicit byte limit")
        self._reserve(key, head.size)
        checksum, body = self._read(key, head.size, capture=True)
        if sha256 is not None and checksum != sha256:
            raise PackageConflict("manifest hash differs from frozen receipt")
        old = self.objects.get(key)
        if old is not None and (old["size"], old["sha256"]) != (head.size, checksum):
            raise PackageConflict("manifest has conflicting object references")
        references = list(old["references"]) if old else []
        if origin not in references:
            references.append(origin)
        self.objects[key] = {
            "key": key,
            "size": head.size,
            "sha256": checksum,
            "observed_sha256": checksum,
            "references": references,
            "verified": True,
        }
        return body

    def raw(self, record, tables):
        raw = RawSource.model_validate(
            {k: record[k] for k in RawSource.model_fields if k in record}
        )
        if raw.raw_status.value != "COMMITTED":
            raise ValueError("only committed raw manifests can be expanded")
        origin = "raw:" + raw.raw_source_id
        body = self.manifest(raw.manifest_key, origin)
        header = json.loads(body)
        schema = header.get("schema_version")
        if schema in {"robot-ingest-committed/v1", "raw-upload-manifest/v1"}:
            manifest, _ = read_committed_manifest(_ManifestBytes(raw.manifest_key, body), raw)
            if any(
                manifest.get(k) != getattr(raw, k)
                for k in (
                    "organization_id",
                    "project_id",
                    "region_code",
                    "dataset_id",
                    "collection_task_id",
                    "robot_id",
                )
            ):
                raise PackageConflict("raw manifest scope/identity differs from inventory")
            files = manifest["files"]
            if schema == "raw-upload-manifest/v1":
                validate_raw_file_set(raw, manifest)
            paths = [safe_relative(f["path"]) for f in files]
            if (
                len(set(paths)) != len(paths)
                or len(paths) != raw.file_count
                or sum(f["size"] for f in files) != raw.total_bytes
            ):
                raise PackageConflict("raw file count/size differs from inventory")
            for item in files:
                key = item.get("object_key") or raw.storage_prefix + "/" + item["path"]
                if not key.startswith(raw.storage_prefix.rstrip("/") + "/"):
                    raise PackageConflict("raw object is outside its committed source prefix")
                self.object(key, item["size"], item["sha256"], origin + ":" + item["path"])
        else:
            # Historical MCAP/capture-envelope commits bind one uploaded object.
            manifest = RolloutManifestV1.model_validate(header)
            matching = [
                r["document"]
                for r in tables["ingest.rollout_objects"]
                if r["document"]["manifest_key"] == raw.manifest_key
            ]
            if len(matching) != 1:
                raise PackageConflict("raw rollout object binding is missing or ambiguous")
            obj = matching[0]
            if (
                manifest.project_id != raw.project_id
                or manifest.task_id != raw.collection_task_id
                or manifest.robot_id != raw.robot_id
                or manifest.rollout_id != obj["rollout_id"]
                or manifest.sha256 != obj["source_sha256"]
                or manifest.sha256 != raw.content_hash
                or manifest.file_size != raw.total_bytes
                or raw.total_bytes != obj["file_size"]
                or raw.file_count != 1
                or not obj["object_key"].startswith(raw.storage_prefix.rstrip("/") + "/")
            ):
                raise PackageConflict("raw MCAP manifest differs from inventory")
            self.object(obj["object_key"], obj["file_size"], obj["source_sha256"], origin)
        self.manifests.append(
            {
                "raw_source_id": raw.raw_source_id,
                "schema": schema,
                "manifest_key": raw.manifest_key,
                "source_format": raw.source_format.value,
                "files": raw.file_count,
                "expanded": True,
            }
        )

    def package(self, record):
        if record["status"] != "COMMITTED":
            self._issue("episode", record["data_version_id"], "PACKAGE_NOT_COMMITTED")
            return
        receipt = CommitReceipt.model_validate(record["receipt_document"])
        data = receipt.data
        assert_scope(data.identity)
        if any(getattr(data.identity, k) != record[k] for k in ("dataset_id", "episode_id")):
            raise PackageConflict("package identity differs from inventory row")
        body = self.manifest(
            data.package_prefix + "/hc/manifest.json",
            data.data_version_id,
            sha256=receipt.manifest_sha256,
        )
        manifest = PackageManifest.model_validate_json(body)
        if (
            manifest.content_hash != data.content_hash
            or manifest.input.data_version_id != data.data_version_id
            or manifest.input.identity != data.identity
            or manifest.input.package_prefix != data.package_prefix
        ):
            raise PackageConflict("package manifest differs from inventory receipt")
        for entry in manifest.files:
            self.object(
                data.package_prefix + "/" + entry.path,
                entry.size,
                entry.sha256,
                data.data_version_id,
            )
        content = canonical(receipt.model_dump(mode="json"))
        self.object(
            data.package_prefix + "/hc/commit.json",
            len(content),
            hashlib.sha256(content).hexdigest(),
            data.data_version_id,
        )

    def verify(self, inventory):
        payload = inventory["inventory"]
        if payload.get("format") != FORMAT or inventory["inventory_sha256"] != digest(payload):
            raise PackageConflict("physical inventory input hash/format differs")
        scope = payload["scope"]
        if collection_scope(scope["dataset_id"]) != tuple(
            scope[k] for k in ("organization_id", "project_id", "region_code", "dataset_id")
        ):
            raise PermissionError("physical inventory is outside the current scope")
        if self.objects or self.manifests or self.issues:
            raise RuntimeError("physical verifier instances are single-use")
        tables = payload["tables"]
        for rows in tables.values():
            for row in rows:
                record = row["document"]
                if any(
                    record.get(k, scope[k]) != scope[k]
                    for k in ("organization_id", "project_id", "region_code")
                ):
                    raise PermissionError("inventory row is outside the exact scope")

        def attempt(kind, identity, operation):
            try:
                operation()
            except InventoryLimit:
                raise
            except (ValueError, KeyError, TypeError):
                self._issue(kind, identity, "REFERENCE_OR_MANIFEST_INVALID")

        for row in tables["ingest.raw_sources"]:
            record = row["document"]
            attempt("raw", record["raw_source_id"], lambda record=record: self.raw(record, tables))
        for row in tables["aligned_media.artifacts"]:
            record = row["document"]
            if record["status"] != "READY":
                self._issue("aligned_media", record["artifact_id"], "MEDIA_NOT_READY")
                continue
            for obj in record["object_manifest"]:
                attempt(
                    "aligned_media",
                    record["artifact_id"],
                    lambda obj=obj, record=record: self.object(
                        obj["key"], obj["size"], obj["sha256"], "media:" + record["artifact_id"]
                    ),
                )
            original = (record.get("timeline_json") or {}).get("original_source")
            primary = [
                obj
                for obj in record["object_manifest"]
                if obj["key"] == record["media_object_key"]
                and obj["sha256"] == record["content_sha256"]
            ]
            if not primary and not (
                original
                and original["object_key"] == record["media_object_key"]
                and original["content_sha256"] == record["content_sha256"]
            ):
                self._issue(
                    "aligned_media", record["artifact_id"], "MEDIA_PRIMARY_REFERENCE_MISSING"
                )
            if original:
                attempt(
                    "aligned_media",
                    record["artifact_id"],
                    lambda original=original, record=record: self.object(
                        original["object_key"],
                        original["size_bytes"],
                        original["content_sha256"],
                        "media-original:" + record["artifact_id"],
                    ),
                )
        for row in tables["ingest.recording_upload_assets"]:
            r = row["document"]
            attempt(
                "recording_asset",
                r["asset_id"],
                lambda r=r: self.object(
                    r["object_key"],
                    r["expected_size"],
                    r["expected_sha256"],
                    "recording:" + r["upload_id"] + ":" + r["asset_id"],
                ),
            )
        for row in tables["ingest.robot_ingest_assets"]:
            r, asset = row["document"], row["document"]["asset_document"]
            if r["state"] != "COMPLETED" or (
                asset["actual_size_bytes"],
                asset["actual_sha256"],
            ) != (asset["expected_size_bytes"], asset["expected_sha256"]):
                self._issue("robot_asset", r["asset_id"], "ROBOT_ASSET_NOT_VERIFIED")
                continue
            attempt(
                "robot_asset",
                r["asset_id"],
                lambda r=r, asset=asset: self.object(
                    r["object_key"],
                    asset["actual_size_bytes"],
                    asset["actual_sha256"],
                    "robot:" + r["upload_id"] + ":" + r["asset_id"],
                ),
            )
        for row in tables["episode_store.versions"]:
            r = row["document"]
            attempt("episode", r["data_version_id"], lambda r=r: self.package(r))
        for row in tables["episode_store.collections"]:
            r = row["document"]
            collection = CollectionSnapshot.model_validate(r["document"])
            if collection.content_hash != r["content_hash"] or any(
                getattr(collection, key) != scope[key] for key in scope
            ):
                raise PackageConflict("inventoried collection hash differs")
            content = canonical(collection.model_dump(mode="json"))
            self.object(
                collection.manifest_key,
                len(content),
                collection.content_hash,
                collection.collection_version_id,
            )
        result = {
            "format": "hc-migration-object-verification/1",
            "inventory_sha256": inventory["inventory_sha256"],
            "scope": scope,
            "objects": sorted(self.objects.values(), key=lambda r: r["key"]),
            "raw_manifests": self.manifests,
            "issues": self.issues,
            "status": "INCOMPLETE"
            if self.issues
            else "EXPANDED_OBJECTS_VERIFIED"
            if self.objects
            else "NO_OBJECTS",
            "unique_objects": len(self.objects),
            "unique_declared_bytes": sum(r["size"] for r in self.objects.values()),
            "reserved_read_bytes": self.reserved_bytes,
            "remaining_checks": [
                "legacy_lance_physical_and_logical_equivalence",
                "export_and_annotation_artifact_expansion",
                "depth_numeric_time_calibration_equivalence",
                "fixed_review_equivalence",
                "batch_publication_and_rollback",
            ],
            "visible_pointers_changed": False,
        }
        return {"verification_sha256": digest(result), "verification": result}
