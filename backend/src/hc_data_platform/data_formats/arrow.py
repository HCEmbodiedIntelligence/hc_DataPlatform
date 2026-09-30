"""LeRobot value and Arrow encodings, independent of export orchestration."""

from __future__ import annotations

import io
from typing import Any

from hc_data_platform.core.errors import problem

from .json import canonical_json_bytes


def require_arrow() -> tuple[Any, Any]:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - exercised in minimal deployments
        raise problem(
            status=503,
            code="EXPORT_DEPENDENCY_MISSING",
            title="Export dependency is missing",
            detail="Install the backend data extra to enable native dataset exports.",
        ) from exc
    return pa, pq


class FeatureShapeError(ValueError):
    pass


def feature_type(value: Any, pa: Any) -> tuple[Any, str, tuple[int, ...]]:
    if isinstance(value, bool):
        return pa.bool_(), "bool", ()
    if isinstance(value, int) and not isinstance(value, bool):
        return pa.int64(), "int64", ()
    if isinstance(value, float):
        return pa.float64(), "float64", ()
    if isinstance(value, str):
        return pa.string(), "string", ()
    if isinstance(value, bytes):
        return pa.binary(), "binary", ()
    if isinstance(value, (list, tuple)):
        if not value:
            raise FeatureShapeError("empty modality arrays have no stable element type")
        child_type, dtype, child_shape = feature_type(value[0], pa)
        for child in value[1:]:
            current_type, current_dtype, current_shape = feature_type(child, pa)
            if current_type != child_type or current_dtype != dtype or current_shape != child_shape:
                raise FeatureShapeError("modality arrays must be rectangular and typed")
        return pa.list_(child_type, len(value)), dtype, (len(value), *child_shape)
    raise FeatureShapeError(f"unsupported LeRobot modality value type: {type(value).__name__}")


def lerobot_value(value: Any) -> tuple[Any, dict[str, Any] | None]:
    """Keep vectors numeric and preserve other structured modalities losslessly."""
    if not isinstance(value, dict):
        return value, None
    for key in ("positions", "values"):
        vector = value.get(key)
        if (
            set(value) <= {key, "names", "units"}
            and isinstance(vector, (list, tuple))
            and vector
            and all(isinstance(item, (int, float)) for item in vector)
        ):
            return list(vector), {
                "encoding": "vector",
                "value_key": key,
                "names": value.get("names"),
                **({"units": value["units"]} if "units" in value else {}),
            }
    if set(value) == {"position_xyz", "orientation_wxyz"}:
        return [*value["position_xyz"], *value["orientation_wxyz"]], {"encoding": "pose"}
    # Keep non-vector structured values lossless in auxiliary JSON features.
    return canonical_json_bytes(value).decode("utf-8"), {"encoding": "json"}


def parquet_bytes(table: Any, pq: Any) -> bytes:
    output = io.BytesIO()
    pq.write_table(
        table,
        output,
        compression="NONE",
        use_dictionary=False,
        write_statistics=False,
        version="2.6",
        data_page_version="1.0",
    )
    return output.getvalue()
