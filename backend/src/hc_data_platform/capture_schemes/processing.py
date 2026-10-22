"""Processing consumes immutable, server-owned Raw snapshots, never current defaults."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from .common import Document, digest, encoded, reject
from .contracts import compare_lerobot
from .models import Adapter, OutputContract, VersionReference

Scope = tuple[str, str, str]


def unresolved(code: str = "CAPTURE_PLAN_REQUIRED", detail: str = "任务采集方案待配置") -> Document:
    return {
        "error": code,
        "detail": detail,
        "details": {},
        "source": "TASK_PLAN_UNRESOLVED",
        "retryable": False,
    }


def validate_snapshot(snapshot: Document, scope: Scope, raw: Document) -> Document:
    """Verify the stored receipt and its lineage; client configuration is not authority."""
    if snapshot.get("error"):
        return {**snapshot, "retryable": False}
    expected = dict(zip(("organization_id", "project_id", "region_code"), scope, strict=True))
    expected.update(
        {k: raw[k] for k in ("dataset_id", "collection_task_id", "robot_id", "source_format")}
    )
    if "recording_id" in raw:
        expected["recording_id"] = raw["recording_id"]
    if snapshot.get("schema_version") != "capture-recording-snapshot/v1":
        return unresolved("CAPTURE_VERSION_MISMATCH", "历史配置没有完整任务快照，需要显式迁移")
    if (
        any(
            type(snapshot.get(key)) is not int or snapshot[key] < 1
            for key in ("plan_revision", "mapping_version")
        )
        or any(
            not isinstance(snapshot.get(key), str) or not snapshot[key]
            for key in ("recording_id", "source", "prepared_at")
        )
        or any(
            not isinstance(snapshot.get(key), str)
            or len(snapshot[key]) != 64
            or any(char not in "0123456789abcdef" for char in snapshot[key])
            for key in ("plan_sha256", "mapping_sha256")
        )
    ):
        return unresolved("CAPTURE_VERSION_MISMATCH", "冻结快照缺少完整版本与录制来源")
    content = {
        k: v for k, v in snapshot.items() if k not in {"snapshot_sha256", "reference_origin"}
    }
    if (
        any(snapshot.get(k) != v for k, v in expected.items())
        or digest(content) != snapshot.get("snapshot_sha256")
        or digest(snapshot.get("output")) != snapshot.get("output_contract_sha256")
    ):
        return unresolved("CAPTURE_VERSION_MISMATCH", "Raw 归属或快照摘要不一致")
    try:
        VersionReference.model_validate(snapshot["scheme"])
        output = OutputContract.model_validate(snapshot["output"])
        adapter: Any = TypeAdapter(Adapter).validate_python(snapshot["effective_adapter"])
        if adapter.source_format != raw["source_format"]:
            return unresolved("CAPTURE_FORMAT_UNSUPPORTED", "快照适配器与 Raw 格式不一致")
        if adapter.source_format in {"MCAP", "CAPTURE_BUNDLE"} and (
            adapter.configuration.fps != output.frequency_hz
            or (
                adapter.configuration.semantic_output != output.model_dump(mode="json")
                if adapter.configuration.semantic_output is not None
                else adapter.configuration.time_source != output.time_source
            )
        ):
            return unresolved("CAPTURE_VERSION_MISMATCH", "快照的时间轴与输出合同不一致")
    except (ValidationError, KeyError, TypeError):
        return unresolved("CAPTURE_VERSION_MISMATCH", "冻结快照格式无效")
    return snapshot


def resolve_raw_snapshot(db: Any, scope: Scope, raw_source_id: str) -> Document:
    row = db.execute(
        """SELECT dataset_id,collection_task_id,robot_id,source_format,raw_status
        FROM ingest.raw_sources
        WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s""",
        (*scope, raw_source_id),
    ).fetchone()
    if row is None:
        return unresolved("RAW_SOURCE_NOT_FOUND", "当前范围内没有该 Raw")
    if row[4] != "COMMITTED":
        return unresolved("RAW_SOURCE_UNAVAILABLE", "该 Raw 未提交或已删除")
    raw = dict(
        zip(("dataset_id", "collection_task_id", "robot_id", "source_format"), row[:4], strict=True)
    )
    saved = db.execute(
        """SELECT document FROM ingest.raw_capture_snapshots
        WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s""",
        (*scope, raw_source_id),
    ).fetchone()
    robot = db.execute(
        """SELECT upload_document->'processing_config_snapshot' FROM ingest.robot_ingest_uploads
        WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s""",
        (*scope, raw_source_id),
    ).fetchone()
    snapshot = saved[0] if saved else robot[0] if robot else None
    if snapshot is None:
        return unresolved()
    if saved and robot and robot[0] and saved[0] != robot[0]:
        return unresolved("CAPTURE_VERSION_MISMATCH", "同一 Raw 存在不同冻结快照")
    result = validate_snapshot(dict(snapshot), scope, raw)
    if (
        result.get("source") == "TASK_PLAN_UNRESOLVED"
        and result.get("error") == "CAPTURE_FORMAT_UNSUPPORTED"
    ):
        from .recovery import resolved_recovery

        return resolved_recovery(db, scope, raw_source_id, raw, result)
    return result


def save_raw_snapshot(db: Any, scope: Scope, raw_source_id: str, snapshot: Document) -> None:
    """Called by the upload owner in its Raw commit transaction, including problem receipts."""
    db.execute(
        """INSERT INTO ingest.raw_capture_snapshots
        (organization_id,project_id,region_code,raw_source_id,document,content_sha256)
        VALUES(%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT DO NOTHING""",
        (*scope, raw_source_id, encoded(snapshot), digest(snapshot)),
    )
    row = db.execute(
        """SELECT document,content_sha256 FROM ingest.raw_capture_snapshots
        WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s""",
        (*scope, raw_source_id),
    ).fetchone()
    if row is None or row[0] != snapshot or row[1] != digest(snapshot):
        reject("CAPTURE_VERSION_MISMATCH", "同一 Raw 的冻结快照不可更改")


def require_snapshot(snapshot: Document) -> Document:
    if snapshot.get("error"):
        reject(
            snapshot["error"],
            snapshot.get("detail", "采集方案不可用"),
            **snapshot.get("details", {}),
        )
    return snapshot


def validate_lerobot(root: Path, index: int, output: OutputContract, adapter: Document) -> Document:
    """Reusable publication/processing validation after checksum-verified localization.

    Metadata comparison includes every declared feature. The reader checks actual
    Parquet types/values, all timeline indexes and decoded media, including extras.
    """
    from hc_data_platform.lerobot_imports.reader import read_episode

    info = json.loads((root / "meta/info.json").read_bytes())
    comparison = compare_lerobot(output, adapter, info)
    if comparison["differences"]:
        reject("CAPTURE_STRUCTURE_MISMATCH", "实际数据与采集方案不一致", **comparison)
    _, _, episode = read_episode(
        root,
        index,
        preserve_features=True,
        capture_snapshot={"output": output.model_dump(mode="json"), "effective_adapter": adapter},
    )
    return {**comparison, "frame_count": episode.frame_count, "frequency_hz": episode.fps}


def validate_mcap_output(output: OutputContract, config: Any, provenance: Document) -> Document:
    """Compare all supported output semantics with full-file MCAP decode evidence."""
    differences: list[Document] = []
    presence: Document = {}
    actual_clock = (config.semantic_output or {}).get("time_source", config.time_source)
    if output.frequency_hz != config.fps or output.time_source != actual_clock:
        differences.append(
            {
                "path": "time_source/frequency_hz",
                "expected": {
                    "time_source": output.time_source,
                    "frequency_hz": output.frequency_hz,
                },
                "actual": provenance.get("config"),
            }
        )
    observed_fps = provenance.get("source_frequency_hz")
    if (
        config.alignment_policy == "PRESERVE"
        and observed_fps is not None
        and abs(observed_fps / output.frequency_hz - 1) > 0.01
    ):
        differences.append(
            {"path": "frequency_hz", "expected": output.frequency_hz, "actual": observed_fps}
        )
    actual = provenance.get("output_features", {})
    for name, feature in output.features.items():
        observed = actual.get(name)
        presence[name] = observed is not None
        if observed is None:
            if feature.required:
                differences.append(
                    {"path": name, "expected": "required", "actual": "not_collected"}
                )
            continue
        expected = feature.model_dump(mode="json")
        for key in ("dtype", "shape", "names", "units"):
            if key == "names" and expected[key] is None:
                continue
            if key == "units" and not expected[key]:
                continue
            if observed.get(key) != expected[key]:
                differences.append(
                    {
                        "path": f"{name}.{key}",
                        "expected": expected[key],
                        "actual": observed.get(key),
                    }
                )
        for key, value in feature.media.items():
            if observed.get("info", {}).get(key) != value:
                differences.append(
                    {
                        "path": f"{name}.media.{key}",
                        "expected": value,
                        "actual": observed.get("info", {}).get(key),
                    }
                )
    if differences:
        reject("CAPTURE_STRUCTURE_MISMATCH", "MCAP 实际输出与方案不一致", differences=differences)
    return {"presence": presence, "input_structure_sha256": digest(actual)}
