"""Complete semantic identity; quality policy and per-batch presence are not schema."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hc_data_platform.episode_store.models import digest

Document = dict[str, Any]
if TYPE_CHECKING:
    from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot


def semantic_schema(
    project_id: str,
    dataset_id: str,
    output: Document,
    fields: dict[str, str],
    *,
    frequency_hz: float | None = None,
) -> DatasetSchemaSnapshot:
    from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot

    from .models import OutputContract

    contract = OutputContract.model_validate(output).model_dump(mode="json")
    # Optional features belong to the logical schema even when this batch omitted
    # them. Presence is stored with the Episode, never in the schema identity.
    fields = dict(fields)
    recording = "/observation/state" in fields
    for name, feature in contract["features"].items():
        if not feature["required"]:
            topic = (
                (
                    "/observation/state"
                    if name == "observation.state"
                    else "/action"
                    if name == "action"
                    else name
                )
                if recording
                else native_topic(name)
            )
            fields.setdefault(topic, "json")
    return DatasetSchemaSnapshot.create(
        project_id=project_id,
        dataset_id=dataset_id,
        schema_snapshot_id="capture-" + digest(contract),
        frequency_hz=frequency_hz or contract["frequency_hz"] or 30,
        fields=fields,
        semantic_contract=contract,
    )


def native_topic(name: str) -> str:
    from hc_data_platform.data_formats.feature_names import native_feature_topic

    if name == "observation.state":
        return "/humanoid/observation/state"
    if name == "action":
        return "/humanoid/action"
    if name.startswith("observation.images."):
        return "/camera/" + name.removeprefix("observation.images.") + "/image"
    return native_feature_topic(name)


def present_fields(
    schema: DatasetSchemaSnapshot, presence: Document, capture: Document | None = None
) -> dict[str, str]:
    from hc_data_platform.data_formats.feature_names import feature_names

    fields = dict(schema.fields)
    # Reviewed legacy datasets keep their original physical schema. Their new
    # package still carries the server-frozen output and per-upload presence.
    contract = schema.semantic_contract or (capture or {}).get("output")
    if contract is not None:
        from .models import OutputContract

        contract = OutputContract.model_validate(contract).model_dump(mode="json")
        if (
            schema.semantic_contract is not None
            and capture is not None
            and structure_contract(capture.get("output", {})) != structure_contract(contract)
        ):
            raise ValueError("capture output differs from the frozen semantic schema")
        mapping = feature_names(sorted(fields))
        for topic, name in mapping.items():
            feature = contract["features"].get(name)
            if feature and not feature["required"] and presence.get(name) is False:
                fields.pop(topic)
    return fields


def structure_contract(output: Document) -> Document:
    """Compare field semantics without historical capture or media rate hints."""
    import copy

    result = copy.deepcopy(output)
    result.pop("frequency_hz", None)
    for feature in result.get("features", {}).values():
        feature.get("media", {}).pop("video.fps", None)
    return result


def capture_presence(metadata: Document) -> Document:
    validation = metadata.get("capture_validation") or {}
    if not isinstance(validation, dict) or not isinstance(validation.get("presence", {}), dict):
        raise ValueError("CAPTURE_VALIDITY_INVALID: malformed presence evidence")
    return dict(validation.get("presence", {}))


def project_info(info: Document, snapshot: Document | None) -> Document:
    """A derived interpretation; never rewrite source metadata or source files."""
    if snapshot is None:
        return info
    from .axes import source_axis_order

    adapter, output = snapshot["effective_adapter"], snapshot["output"]
    sources = adapter.get("feature_sources", {})
    features = {
        k: v
        for k, v in info["features"].items()
        if k in {"index", "frame_index", "episode_index", "task_index", "timestamp"}
    }
    for name, declaration in output["features"].items():
        original = info["features"].get(sources.get(name, name))
        if original is not None:
            features[name] = {
                **original,
                **{
                    k: declaration[k]
                    for k in ("units", "role", "validity_feature", "required", "missing")
                    if k in declaration
                },
            }
            if source_axis_order(name, declaration, original) is not None:
                features[name]["names"] = declaration["names"]
    if info.get("robot_type") == "unitree_g1":
        if not {"observation.state", "action"}.issubset(features):
            raise ValueError(
                "CAPTURE_FORMAT_UNSUPPORTED: map G1 source columns to observation.state/action"
            )
        # Explicit source mappings opt into the generic layout validator; retain
        # original robot identity in the immutable Raw and capture snapshot.
        return {**info, "robot_type": "generic", "features": features}
    return {**info, "features": features}
