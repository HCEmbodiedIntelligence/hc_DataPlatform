"""Validate actual Parquet storage and values for every declared source feature."""

import math
from typing import Any


def validate_columns(table: Any, features: dict[str, Any]) -> None:
    import pyarrow as pa

    def values(value: Any, shape: list[int], path: str) -> list[Any]:
        if shape == [1] and not isinstance(value, list):
            return [value]
        if not shape:
            if isinstance(value, list):
                raise ValueError(f"LEROBOT_FEATURE_SHAPE: {path}")
            return [value]
        if not isinstance(value, list) or len(value) != shape[0]:
            raise ValueError(f"LEROBOT_FEATURE_SHAPE: {path}")
        return [item for child in value for item in values(child, shape[1:], path)]

    for name, feature in features.items():
        if feature["dtype"] == "video":
            continue
        if name not in table.column_names:
            raise ValueError(f"LEROBOT_COLUMN_MISSING: {name}")
        kind = table.schema.field(name).type
        while (
            pa.types.is_list(kind)
            or pa.types.is_large_list(kind)
            or pa.types.is_fixed_size_list(kind)
        ):
            kind = kind.value_type
        dtype = feature["dtype"]
        expected = pa.string() if dtype in {"string", "str"} else pa.from_numpy_dtype(dtype)
        if kind != expected:
            raise ValueError(f"LEROBOT_FEATURE_DTYPE: {name}: expected {expected}, actual {kind}")
        for frame, value in enumerate(table[name].to_pylist()):
            items = values(value, feature["shape"], f"{name}[{frame}]")
            if any(v is None for v in items) and feature.get("required") is False:
                raise ValueError(
                    f"CAPTURE_MISSING_REPRESENTATION_UNSUPPORTED: nullable {name}[{frame}]; "
                    "use explicit per-axis validity or omit the optional feature"
                )
            if any(v is None or (isinstance(v, float) and not math.isfinite(v)) for v in items):
                raise ValueError(f"LEROBOT_FEATURE_VALUE: nonfinite or null {name}[{frame}]")
            if (
                feature.get("role") == "validity"
                or name in {"observation.source_available", "observation.source_valid"}
            ) and any(v not in (0, 1, False, True) for v in items):
                raise ValueError(f"LEROBOT_VALIDITY_VALUE: {name}[{frame}]")
