"""Bounded, deterministic parser for untrusted package manifests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from pydantic import ValidationError

from hc_data_platform.core.errors import ProblemException, problem

from .models import (
    ManifestDiscoveryV1,
    ManifestIdentifiersV1,
    ManifestPreflightResultV1,
    ManifestTimeRangeV1,
    RolloutManifestV1,
)
from .ports import ObjectStoragePort

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 16
MAX_JSON_NODES = 50_000


class _DuplicateJsonKey(ValueError):
    pass


class ManifestParserPort(Protocol):
    def parse(self, manifest_key: str) -> ManifestPreflightResultV1: ...


class ObjectStorageManifestParser:
    """Read an immutable commit marker with a hard byte cap before JSON decoding."""

    def __init__(
        self,
        storage: ObjectStoragePort,
        *,
        max_bytes: int = MAX_MANIFEST_BYTES,
    ) -> None:
        if not 1 <= max_bytes <= MAX_MANIFEST_BYTES:
            raise ValueError("Manifest parser byte limit is invalid")
        self._storage = storage
        self._max_bytes = max_bytes

    def parse(self, manifest_key: str) -> ManifestPreflightResultV1:
        metadata = self._storage.head(manifest_key)
        if metadata is None:
            raise problem(
                status=404,
                code="MANIFEST_OBJECT_NOT_FOUND",
                title="Manifest object not found",
                detail="The immutable Manifest commit marker does not exist.",
                retryable=True,
            )
        if metadata.size > self._max_bytes:
            raise problem(
                status=413,
                code="MANIFEST_TOO_LARGE",
                title="Manifest is too large",
                detail=f"Manifest JSON must not exceed {self._max_bytes} bytes.",
            )
        body = bytearray()
        for chunk in self._storage.read_chunks(manifest_key, chunk_size=64 * 1024):
            body.extend(chunk)
            if len(body) > self._max_bytes:
                raise problem(
                    status=413,
                    code="MANIFEST_TOO_LARGE",
                    title="Manifest is too large",
                    detail=f"Manifest JSON must not exceed {self._max_bytes} bytes.",
                )
        return parse_manifest_bytes(bytes(body), max_bytes=self._max_bytes)


def parse_manifest_bytes(
    body: bytes,
    *,
    max_bytes: int = MAX_MANIFEST_BYTES,
) -> ManifestPreflightResultV1:
    raw = decode_bounded_json(body, max_bytes=max_bytes)
    if not isinstance(raw, Mapping):
        raise _invalid_manifest("The manifest root must be a JSON object.")
    require_decimal_crc64_wire_values(raw)
    try:
        manifest = RolloutManifestV1.model_validate(raw)
    except ValidationError as exc:
        raise _invalid_manifest(
            "The manifest does not satisfy package manifest schema v1.",
            errors=exc.errors(include_url=False, include_input=False),
        ) from exc
    return preflight_manifest(manifest)


def require_decimal_crc64_wire_values(raw: object) -> None:
    """Reject JSON numbers before they can cross a uint64/IEEE-754 boundary."""

    if not isinstance(raw, Mapping):
        return
    _require_crc64_string(raw, "crc64")
    files = raw.get("files")
    if not isinstance(files, Sequence) or isinstance(files, (str, bytes, bytearray)):
        return
    for index, item in enumerate(files):
        if isinstance(item, Mapping):
            _require_crc64_string(item, f"files[{index}].crc64")


def _require_crc64_string(container: Mapping[object, object], location: str) -> None:
    if "crc64" in container and not isinstance(container["crc64"], str):
        raise _invalid_manifest(
            f"{location} must be an unsigned decimal string; JSON numbers cannot safely "
            "represent the full CRC64 range."
        )


def decode_bounded_json(body: bytes, *, max_bytes: int) -> object:
    if not body:
        raise _invalid_manifest("The manifest body is empty.")
    if len(body) > max_bytes:
        raise problem(
            status=413,
            code="MANIFEST_TOO_LARGE",
            title="Manifest is too large",
            detail=f"Manifest JSON must not exceed {max_bytes} bytes.",
        )
    try:
        raw = json.loads(body, object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKey) as exc:
        raise _invalid_manifest("The manifest must be valid UTF-8 JSON.") from exc
    _validate_shape_budget(raw)
    return raw


def preflight_manifest(manifest: RolloutManifestV1) -> ManifestPreflightResultV1:
    try:
        manifest = RolloutManifestV1.model_validate(manifest.model_dump(mode="python"))
    except ValidationError as exc:
        raise _invalid_manifest(
            "The manifest does not satisfy package manifest schema v1.",
            errors=exc.errors(include_url=False, include_input=False),
        ) from exc
    encoded = json.dumps(
        manifest.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    missing = tuple(sorted(set(manifest.expected_topics) - set(manifest.actual_topics)))
    return ManifestPreflightResultV1(
        manifest_fingerprint=hashlib.sha256(encoded).hexdigest(),
        source_fingerprint=manifest.source_fingerprint,
        identifiers=ManifestIdentifiersV1(
            collection_session_id=manifest.collection_session_id,
            recording_request_id=manifest.recording_request_id,
            data_package_id=manifest.data_package_id,
            robot_id=manifest.robot_id,
            pico_instance_id=manifest.pico_instance_id,
        ),
        time_range=ManifestTimeRangeV1(
            start_time=manifest.start_time,
            end_time=manifest.end_time,
        ),
        files=tuple(manifest.files),
        total_file_size=sum(item.size for item in manifest.files),
        discovery=ManifestDiscoveryV1(
            cameras=tuple(sorted(manifest.cameras, key=lambda item: item.camera_id)),
            topics=tuple(sorted(manifest.topics, key=lambda item: item.name)),
            missing_expected_topics=missing,
        ),
        manifest=manifest,
    )


def _validate_shape_budget(value: object) -> None:
    nodes = 0
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise _invalid_manifest("The manifest contains too many JSON values.")
        if depth > MAX_JSON_DEPTH:
            raise _invalid_manifest("The manifest nesting depth exceeds the allowed limit.")
        if isinstance(current, Mapping):
            for key, child in current.items():
                if not isinstance(key, str) or len(key) > 256:
                    raise _invalid_manifest("Manifest object keys must be short strings.")
                stack.append((child, depth + 1))
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
            stack.extend((child, depth + 1) for child in current)
        elif isinstance(current, str) and len(current) > 4096:
            raise _invalid_manifest("Manifest string values must not exceed 4096 characters.")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_manifest(
    detail: str,
    *,
    errors: Sequence[Mapping[str, Any]] | None = None,
) -> ProblemException:
    return problem(
        status=422,
        code="MANIFEST_INVALID",
        title="Manifest is invalid",
        detail=detail,
        details={} if errors is None else {"errors": [dict(item) for item in errors[:50]]},
    )
