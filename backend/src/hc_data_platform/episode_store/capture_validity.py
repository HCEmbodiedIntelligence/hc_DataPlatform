"""Per-axis validity remains distinct from numerical zero throughout publication."""

from collections.abc import Iterable, Iterator
from typing import Any

from hc_data_platform.data_formats.feature_names import feature_names
from hc_data_platform.lance_catalog.models import StepRecord


def feature_mask(
    name: str, contract: dict[str, Any], values: dict[str, Any], *, default: bool = True
) -> tuple[bool, ...]:
    declaration = contract["features"][name]
    width = 1 if declaration["role"] in {"rgb", "depth"} else declaration["shape"][0]
    mask = (default,) * width
    value = values.get(name)
    if isinstance(value, dict) and value.get("validity") is not None:
        mask = tuple(value["validity"])
    explicit = declaration.get("validity_feature")
    for key, feature in contract["features"].items():
        if feature["role"] != "validity":
            continue
        names = feature.get("names")
        grouped = isinstance(names, list) and name in names
        if key != explicit and not grouped:
            continue
        raw = values.get(key)
        if isinstance(raw, dict):
            raw = raw.get("values", raw.get("positions"))
        raw = list(raw) if isinstance(raw, (list, tuple)) else [raw]
        if grouped:
            if len(raw) != len(names):
                raise ValueError(f"CAPTURE_VALIDITY_INVALID: {key}")
            raw = [raw[names.index(name)]]
        if len(raw) not in {1, width} or any(v not in (0, 1, False, True) for v in raw):
            raise ValueError(f"CAPTURE_VALIDITY_INVALID: {name}")
        if len(raw) == 1:
            raw *= width
        if len(mask) != width or any(type(v) is not bool for v in mask):
            raise ValueError(f"CAPTURE_VALIDITY_INVALID: {name}")
        mask = tuple(a and bool(b) for a, b in zip(mask, raw, strict=True))
    if len(mask) != width or any(type(v) is not bool for v in mask):
        raise ValueError(f"CAPTURE_VALIDITY_INVALID: {name}")
    return mask


def axis_masks(
    step: StepRecord, contract: dict[str, Any], mapping: dict[str, str]
) -> dict[str, tuple[bool, ...]]:
    inverse = {target: name for name, target in mapping.items()}
    result: dict[str, tuple[bool, ...]] = {}
    values = {target: step.modalities[logical] for logical, target in mapping.items()}
    for target in contract.get("features", {}):
        name = inverse.get(target)
        if name is None:
            continue
        result[name] = feature_mask(target, contract, values, default=step.valid.get(name, False))
    return result


def apply_validity(steps: Iterable[StepRecord], metadata: dict[str, Any]) -> Iterator[StepRecord]:
    contract = (metadata.get("capture") or {}).get("output", {})
    if not contract:
        yield from steps
        return
    for step in steps:
        masks = axis_masks(step, contract, feature_names(sorted(step.modalities)))
        valid = {**step.valid, **{name: all(mask) for name, mask in masks.items()}}
        # Optional unavailable axes do not invalidate unrelated required signals.
        required = {k for k, v in contract["features"].items() if v["required"]}
        mapping = feature_names(sorted(step.modalities))
        required_valid = all(
            valid.get(name, False) for name, target in mapping.items() if target in required
        )
        yield step.model_copy(
            update={
                "axis_validity": masks,
                "valid": valid,
                "sample_valid": step.sample_valid and required_valid,
            }
        )
