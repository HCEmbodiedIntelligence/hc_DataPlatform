"""Reconcile ingress metadata using immutable robot and capture-schema evidence."""

NORMALIZED_PROCESSOR = "hc-lerobot-collection/2"
_MISSING_ROBOT_TYPES = (None, "", "unknown")


def normalize_capture_metadata(sources, infos):
    """Update export copies only; incompatible physical contracts still fail preflight.

    A missing robot label is not evidence that two robots are interchangeable.
    Require the same committed robot identity and frozen capture output before
    reconciling it or the two known scalar/vector ingress representations.
    """
    inputs = [source.package.manifest.input for source in sources]
    robot = inputs[0].robot_id
    capture = infos[0].get("hc.capture_contract")
    schema = inputs[0].schema_snapshot_id
    if (
        robot in _MISSING_ROBOT_TYPES
        or not capture
        or not capture.get("features")
        or any(item.robot_id != robot or item.schema_snapshot_id != schema for item in inputs)
        or any(info.get("hc.capture_contract") != capture for info in infos)
    ):
        return None
    robot_types = [info.get("robot_type") for info in infos]
    if any(value is not None and not isinstance(value, str) for value in robot_types):
        return None
    known = {value for value in robot_types if value not in _MISSING_ROBOT_TYPES}
    if len(known) > 1:
        return None
    robot_type = next(iter(known), "unknown")
    members = []
    for selection, info in zip(sources, infos, strict=True):
        changes = []

        def update(document, key, value, field, changes=changes):
            if document.get(key) != value:
                changes.append({"field": field, "source": document.get(key), "output": value})
                document[key] = value

        update(info, "robot_type", robot_type, "robot_type")
        for name, declared in capture["features"].items():
            feature = info["features"].get(name, {})
            # Source wrappers may differ only around the exact frozen output.
            if any(
                feature.get(key) != declared.get(key)
                for key in ("dtype", "shape", "names", "units")
            ):
                continue
            encoding = info.get("hc.structured_features", {}).get(name, {})
            if (
                encoding.get("encoding") == "vector"
                and encoding.get("value_key") in {"positions", "values"}
                and encoding.get("names") == declared.get("names")
                and encoding.get("units") == declared.get("units")
            ):
                update(encoding, "value_key", "values", f"hc.structured_features.{name}.value_key")
            shapes = info.get("hc.source_shapes", {})
            if (
                feature.get("shape") == [1]
                and feature.get("dtype") in {"float32", "float64", "int32", "int64", "bool"}
                and shapes.get(name) in ([], [1])
            ):
                update(shapes, name, [1], f"hc.source_shapes.{name}")
        members.append(
            {"data_version_id": selection.package.receipt.data.data_version_id, "changes": changes}
        )
    if not any(member["changes"] for member in members):
        return None
    return {
        "format": "hc-collection-metadata-normalization/1",
        "robot_id": robot,
        "schema_snapshot_id": schema,
        "members": members,
        "source_metadata": "hc/episodes/{episode_index:06d}/meta/info.json",
    }
