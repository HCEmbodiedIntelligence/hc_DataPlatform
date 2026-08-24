"""Deterministic Dataset Schema Snapshot to Arrow/Lance schema compilation."""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from types import ModuleType
from typing import Any, Protocol, cast

from .models import DatasetSchemaSnapshot


class ArrowSchema(Protocol):
    """Small structural type used without making pyarrow a base dependency."""

    def equals(self, other: object, check_metadata: bool = False) -> bool: ...


class SchemaCompilationError(ValueError):
    """Raised when a snapshot contains an unsupported or malformed Arrow type."""


class LanceDependencyError(RuntimeError):
    """Raised when an optional real-adapter dependency is unavailable."""


_PRIMITIVE_TYPES = {
    "null": "null",
    "bool": "bool_",
    "boolean": "bool_",
    "int8": "int8",
    "int16": "int16",
    "int32": "int32",
    "int64": "int64",
    "uint8": "uint8",
    "uint16": "uint16",
    "uint32": "uint32",
    "uint64": "uint64",
    "float16": "float16",
    "float32": "float32",
    "float64": "float64",
    "string": "string",
    "utf8": "string",
    "large_string": "large_string",
    "binary": "binary",
    "large_binary": "large_binary",
    # Decoder outputs are bounded JSON values whose concrete ROS/message shape
    # is not necessarily known when the immutable Dataset schema is registered.
    # Store the canonical JSON text in Lance and recover the typed JSON value at
    # the catalog boundary; this avoids both unsafe object locators and a fake
    # all-string public contract.
    "json": "string",
    "date32": "date32",
    "date64": "date64",
}

JSON_MODALITIES_METADATA_KEY = b"hc.schema.json_modalities"

_PRIMITIVE_CANONICAL = {
    name: "bool" if name in {"bool", "boolean"} else "string" if name == "utf8" else name
    for name in _PRIMITIVE_TYPES
}


def _arrow() -> ModuleType:
    try:
        return importlib.import_module("pyarrow")
    except ModuleNotFoundError as exc:
        raise LanceDependencyError(
            "pyarrow is required to compile a Dataset Schema Snapshot; "
            "install the backend 'data' extra"
        ) from exc


def _split_top_level(value: str, delimiter: str = ",") -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    for index, character in enumerate(value):
        if character in "<[(":
            depth += 1
        elif character in ">])":
            depth -= 1
            if depth < 0:
                raise SchemaCompilationError(f"unbalanced Arrow type {value!r}")
        elif character == delimiter and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
    if depth != 0:
        raise SchemaCompilationError(f"unbalanced Arrow type {value!r}")
    parts.append(value[start:].strip())
    return parts


def _split_field(value: str) -> tuple[str, str]:
    depth = 0
    for index, character in enumerate(value):
        if character in "<[(":
            depth += 1
        elif character in ">])":
            depth -= 1
        elif character == ":" and depth == 0:
            name, type_name = value[:index].strip(), value[index + 1 :].strip()
            if name and type_name:
                return name, type_name
    raise SchemaCompilationError(f"struct field must be name:type, got {value!r}")


def _normalize_arrow_type(type_name: str) -> str:
    value = re.sub(r"\s+", "", type_name)
    lowered = value.lower()
    primitive = _PRIMITIVE_CANONICAL.get(lowered)
    if primitive is not None:
        return primitive

    match = re.fullmatch(r"(list|large_list)<(.+)>", value, flags=re.IGNORECASE)
    if match:
        return f"{match.group(1).lower()}<{_normalize_arrow_type(match.group(2))}>"

    match = re.fullmatch(r"fixed_size_list<(.+),(\d+)>", value, flags=re.IGNORECASE)
    if match:
        size = int(match.group(2))
        if size <= 0:
            raise SchemaCompilationError("fixed_size_list size must be positive")
        return f"fixed_size_list<{_normalize_arrow_type(match.group(1))},{size}>"

    match = re.fullmatch(r"(?:fixed_size_)?binary\[(\d+)\]", value, flags=re.IGNORECASE)
    if match:
        width = int(match.group(1))
        if width <= 0:
            raise SchemaCompilationError("fixed-size binary width must be positive")
        return f"binary[{width}]"

    match = re.fullmatch(
        r"(timestamp|duration)\[([^,\]]+)(?:,([^\]]+))?\]",
        value,
        flags=re.IGNORECASE,
    )
    if match:
        kind, raw_unit, timezone = match.groups()
        kind = kind.lower()
        unit = raw_unit.lower()
        if unit not in {"s", "ms", "us", "ns"}:
            raise SchemaCompilationError(f"unsupported temporal unit {raw_unit!r}")
        if kind == "duration" and timezone is not None:
            raise SchemaCompilationError("duration types cannot declare a timezone")
        suffix = f",{timezone}" if timezone is not None else ""
        return f"{kind}[{unit}{suffix}]"

    match = re.fullmatch(r"decimal(128|256)\((\d+),(\d+)\)", value, flags=re.IGNORECASE)
    if match:
        bits, raw_precision, raw_scale = match.groups()
        precision, scale = int(raw_precision), int(raw_scale)
        maximum = 38 if bits == "128" else 76
        if not 1 <= precision <= maximum or scale > precision:
            raise SchemaCompilationError(
                f"decimal{bits} precision/scale must satisfy 1 <= precision <= "
                f"{maximum} and scale <= precision"
            )
        return f"decimal{bits}({precision},{scale})"

    match = re.fullmatch(r"map<(.+)>", value, flags=re.IGNORECASE)
    if match:
        parameters = _split_top_level(match.group(1))
        if len(parameters) != 2:
            raise SchemaCompilationError("map types require key and item types")
        return f"map<{_normalize_arrow_type(parameters[0])},{_normalize_arrow_type(parameters[1])}>"

    match = re.fullmatch(r"struct<(.+)>", value, flags=re.IGNORECASE)
    if match:
        children: list[str] = []
        for declaration in _split_top_level(match.group(1)):
            name, child_type = _split_field(declaration)
            children.append(f"{name}:{_normalize_arrow_type(child_type)}")
        return f"struct<{','.join(children)}>"

    raise SchemaCompilationError(f"unsupported Arrow type {type_name!r}")


def _parse_arrow_type(type_name: str, arrow: ModuleType) -> Any:
    value = _normalize_arrow_type(type_name)
    primitive = _PRIMITIVE_TYPES.get(value)
    if primitive is not None:
        return getattr(arrow, primitive)()

    match = re.fullmatch(r"(list|large_list)<(.+)>", value)
    if match:
        item_type = _parse_arrow_type(match.group(2), arrow)
        factory = arrow.list_ if match.group(1) == "list" else arrow.large_list
        return factory(arrow.field("item", item_type, nullable=True))

    match = re.fullmatch(r"fixed_size_list<(.+),(\d+)>", value)
    if match:
        return arrow.list_(
            arrow.field("item", _parse_arrow_type(match.group(1), arrow), nullable=True),
            int(match.group(2)),
        )

    match = re.fullmatch(r"(?:fixed_size_)?binary\[(\d+)\]", value)
    if match:
        return arrow.binary(int(match.group(1)))

    match = re.fullmatch(r"(timestamp|duration)\[([^,\]]+)(?:,([^\]]+))?\]", value)
    if match:
        kind, unit, timezone = match.groups()
        if unit not in {"s", "ms", "us", "ns"}:
            raise SchemaCompilationError(f"unsupported temporal unit {unit!r}")
        if kind == "duration":
            if timezone is not None:
                raise SchemaCompilationError("duration types cannot declare a timezone")
            return arrow.duration(unit)
        return arrow.timestamp(unit, tz=timezone)

    match = re.fullmatch(r"decimal(128|256)\((\d+),(\d+)\)", value)
    if match:
        bits, precision, scale = (int(item) for item in match.groups())
        factory = arrow.decimal128 if bits == 128 else arrow.decimal256
        return factory(precision, scale)

    match = re.fullmatch(r"map<(.+)>", value)
    if match:
        parameters = _split_top_level(match.group(1))
        if len(parameters) != 2:
            raise SchemaCompilationError("map types require key and item types")
        return arrow.map_(
            _parse_arrow_type(parameters[0], arrow),
            _parse_arrow_type(parameters[1], arrow),
        )

    match = re.fullmatch(r"struct<(.+)>", value)
    if match:
        children = []
        for declaration in _split_top_level(match.group(1)):
            name, child_type = _split_field(declaration)
            children.append(arrow.field(name, _parse_arrow_type(child_type, arrow), nullable=True))
        return arrow.struct(children)

    raise SchemaCompilationError(f"unsupported Arrow type {type_name!r}")


def canonical_schema_spec(fields: dict[str, str]) -> tuple[tuple[str, str], ...]:
    """Return a stable, order-independent logical schema representation."""

    if not fields:
        raise SchemaCompilationError("a dataset schema must contain at least one modality")
    normalized: list[tuple[str, str]] = []
    for name, type_name in sorted(fields.items()):
        if not name or name.startswith("__hc_"):
            raise SchemaCompilationError(f"invalid or reserved modality name {name!r}")
        normalized_type = _normalize_arrow_type(type_name)
        normalized.append((name, normalized_type))
    return tuple(normalized)


def validate_schema_fields(fields: dict[str, str]) -> None:
    """Validate the supported type grammar without requiring the data extra."""

    canonical_schema_spec(fields)


def compute_schema_fingerprint(fields: dict[str, str]) -> str:
    """Hash the canonical logical schema without depending on a PyArrow release."""

    payload = {
        "compiler": "hc-arrow-schema/v1",
        "fields": canonical_schema_spec(fields),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def compile_arrow_schema(snapshot: DatasetSchemaSnapshot) -> ArrowSchema:
    """Compile a deterministic Arrow schema suitable for a shared Lance dataset."""

    expected = compute_schema_fingerprint(snapshot.fields)
    if snapshot.fingerprint != expected:
        raise SchemaCompilationError(
            "schema snapshot fingerprint does not match its canonical fields"
        )
    arrow = _arrow()
    canonical_fields = canonical_schema_spec(snapshot.fields)
    modality_fields = [
        arrow.field(name, _parse_arrow_type(type_name, arrow), nullable=True)
        for name, type_name in canonical_fields
    ]
    nullable_ints = [
        arrow.field(item.name, arrow.int64(), nullable=True) for item in modality_fields
    ]
    source_timestamp_lists = [
        arrow.field(item.name, arrow.list_(arrow.int64()), nullable=False)
        for item in modality_fields
    ]
    required_bools = [
        arrow.field(item.name, arrow.bool_(), nullable=False) for item in modality_fields
    ]
    metadata = {
        b"hc.schema.compiler": b"hc-arrow-schema/v1",
        b"hc.schema.fingerprint": snapshot.fingerprint.encode(),
        b"hc.schema.snapshot_id": snapshot.schema_snapshot_id.encode(),
        JSON_MODALITIES_METADATA_KEY: json.dumps(
            [name for name, type_name in canonical_fields if type_name == "json"],
            separators=(",", ":"),
        ).encode(),
    }
    schema = arrow.schema(
        [
            arrow.field("rollout_id", arrow.string(), nullable=False),
            arrow.field("step_index", arrow.int64(), nullable=False),
            arrow.field("timestamp_ns", arrow.int64(), nullable=False),
            arrow.field("modalities", arrow.struct(modality_fields), nullable=False),
            arrow.field(
                "source_timestamps_ns", arrow.struct(source_timestamp_lists), nullable=False
            ),
            arrow.field("time_error_ns", arrow.struct(nullable_ints), nullable=False),
            arrow.field("valid", arrow.struct(required_bools), nullable=False),
            arrow.field("repeated", arrow.struct(required_bools), nullable=False),
            arrow.field("sample_valid", arrow.bool_(), nullable=False),
        ],
        metadata=metadata,
    )
    return cast(ArrowSchema, schema)
