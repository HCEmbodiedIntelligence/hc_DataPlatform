"""Semantic comparison shared by publication, instance checks and actual ingestion."""

from __future__ import annotations

import copy
from typing import Any

from .axes import source_axis_order
from .common import Document, digest, reject
from .models import DeviceMapping, OutputContract, SchemeDraft


def structure_summary(info: Document) -> Document:
    # Dataset length and capture time are batch properties, never schema identity.
    return {
        "algorithm": "lerobot-input-structure/v1",
        "codebase_version": info.get("codebase_version"),
        "fps": info.get("fps"),
        "features": info.get("features", {}),
    }


def adapter_for(document: Document, source_format: str) -> Document:
    adapter = next(
        (item for item in document["adapters"] if item["source_format"] == source_format), None
    )
    if adapter is None:
        reject("CAPTURE_FORMAT_UNSUPPORTED", "方案不支持当前输入格式", source_format=source_format)
    return copy.deepcopy(adapter)


def effective_adapter(document: Document, source_format: str, mapping: DeviceMapping) -> Document:
    adapter = adapter_for(document, source_format)
    sources_map = mapping.sources.get(source_format, {})
    if source_format == "LEROBOT_V3":
        allowed = set(document["output"]["features"])
        if set(sources_map) - allowed:
            reject("CAPTURE_MAPPING_INVALID", "设备映射只能修改方案已声明字段的来源")
        adapter["feature_sources"] = {
            **adapter.get("feature_sources", {}),
            **sources_map,
        }
        sources = [
            adapter["feature_sources"].get(name, name) for name in document["output"]["features"]
        ]
        if len(set(sources)) != len(sources):
            reject("CAPTURE_MAPPING_INVALID", "不同逻辑字段不能复用同一个输入字段")
    else:
        if document.get("input_mode") == "AUTO":
            mcap_sources = mapping.sources.get("MCAP", {})
            bundle_sources = mapping.sources.get("CAPTURE_BUNDLE", {})
            if any(k in bundle_sources and bundle_sources[k] != v for k, v in mcap_sources.items()):
                reject("CAPTURE_MAPPING_INVALID", "MCAP 与 MCAP＋MP4 共用同一组设备话题")
            sources_map = {**bundle_sources, **mcap_sources}
        cfg = adapter["configuration"]
        allowed = (
            {"state", "action"}
            | {c["camera_id"] for c in cfg["cameras"]}
            | set(cfg.get("numeric_sources", {}))
        )
        if set(sources_map) - allowed:
            reject("CAPTURE_MAPPING_INVALID", "设备映射只能修改状态、动作或已声明相机的话题")
        for role in ("state", "action"):
            cfg[role + "_topic"] = sources_map.get(role, cfg.get(role + "_topic"))
        for camera in cfg["cameras"]:
            original = camera["topic"]
            camera["topic"] = sources_map.get(camera["camera_id"], original)
            if cfg.get("primary_camera") == original:
                cfg["primary_camera"] = camera["topic"]
        for name, signal in cfg.get("numeric_sources", {}).items():
            signal["topic"] = sources_map.get(name, signal["topic"])
    return adapter


def compare_lerobot(output: OutputContract, adapter: Document, info: Document) -> Document:
    differences: list[Document] = []

    def compare(path: str, expected: Any, actual: Any) -> None:
        if expected != actual:
            differences.append({"path": path, "expected": expected, "actual": actual})

    compare("codebase_version", adapter["codebase_version"], info.get("codebase_version"))
    compare("fps", output.frequency_hz, info.get("fps"))
    presence, axis_reorders = {}, {}
    for name, feature in output.features.items():
        source = adapter.get("feature_sources", {}).get(name, name)
        observed = info.get("features", {}).get(source)
        presence[name] = observed is not None
        if observed is None:
            if feature.required:
                differences.append(
                    {
                        "path": f"features.{name}",
                        "expected": "required",
                        "actual": "not_collected",
                        "source": source,
                    }
                )
            continue
        expected = feature.model_dump(mode="json")
        order = source_axis_order(name, expected, observed)
        if order is not None:
            axis_reorders[name] = {
                "source": source,
                "source_names": observed["names"],
                "output_names": expected["names"],
                "indices": order,
            }
        for field in ("dtype", "shape", "names"):
            actual = (
                expected["names"] if field == "names" and order is not None else observed.get(field)
            )
            compare(f"features.{name}.{field}", expected[field], actual)
        # Legacy sources lack units. The published per-axis declaration is then
        # the explicit interpretation; any source unit declaration must agree.
        if "units" in observed:
            units = observed["units"]
            if order is not None and len(units) == len(order):
                units = [units[i] for i in order]
            compare(f"features.{name}.units", expected["units"], units)
        for field, value in feature.media.items():
            compare(f"features.{name}.info.{field}", value, observed.get("info", {}).get(field))
    return {
        "differences": differences,
        "presence": presence,
        "axis_reorders": axis_reorders,
        "input_structure": structure_summary(info),
        "input_structure_sha256": digest(structure_summary(info)),
    }


def validate_document(document: Document) -> SchemeDraft:
    return SchemeDraft.model_validate({"expected_revision": 0, **document})


def output_hash(document: Document) -> str:
    return digest(document["output"])
