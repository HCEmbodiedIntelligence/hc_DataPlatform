"""Deterministic Dataset Schema Snapshot to Arrow/Lance schema compilation."""

from __future__ import annotations

import importlib
import json
import re
from types import ModuleType
from typing import Any, Protocol, cast

from .models import DatasetSchemaSnapshot
from .schema_spec import (
    _PRIMITIVE_CANONICAL as _PRIMITIVE_CANONICAL,
)
from .schema_spec import (
    _PRIMITIVE_TYPES as _PRIMITIVE_TYPES,
)
from .schema_spec import (
    SchemaCompilationError as SchemaCompilationError,
)
from .schema_spec import (
    _normalize_arrow_type as _normalize_arrow_type,
)
from .schema_spec import (
    _split_field as _split_field,
)
from .schema_spec import (
    _split_top_level as _split_top_level,
)
from .schema_spec import (
    canonical_schema_spec as canonical_schema_spec,
)
from .schema_spec import (
    compute_schema_fingerprint as compute_schema_fingerprint,
)
from .schema_spec import (
    validate_schema_fields as validate_schema_fields,
)


class ArrowSchema(Protocol):
    """Small structural type used without making pyarrow a base dependency."""

    def equals(self, other: object, check_metadata: bool = False) -> bool: ...


class LanceDependencyError(RuntimeError):
    """Raised when an optional real-adapter dependency is unavailable."""


JSON_MODALITIES_METADATA_KEY = b"hc.schema.json_modalities"


def _arrow() -> ModuleType:
    try:
        return importlib.import_module("pyarrow")
    except ModuleNotFoundError as exc:
        raise LanceDependencyError(
            "pyarrow is required to compile a Dataset Schema Snapshot; "
            "install the backend 'data' extra"
        ) from exc


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
