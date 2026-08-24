"""Inspect and prove the persisted Raw, verification, QC, alignment, and Lance lineage."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3
import lance
import psycopg
import pyarrow.parquet as parquet
from botocore.config import Config
from mcap.reader import make_reader
from psycopg.rows import dict_row

from tests.system.hf_humanoid_demo.convert import (
    ACTION_TOPIC,
    CAMERA_TOPIC,
    STATE_TOPIC,
    extract_jpeg_frames,
)
from tests.system.wave2.fixture import RunScope


def _json_value(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def _query(
    connection: psycopg.Connection[Any], statement: str, parameters: tuple[object, ...]
) -> list[dict[str, Any]]:
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(statement, parameters)
        return [dict(row) for row in cursor.fetchall()]


def _object_keys(client: Any, bucket: str, prefixes: tuple[str, ...]) -> list[dict[str, object]]:
    objects: list[dict[str, object]] = []
    paginator = client.get_paginator("list_objects_v2")
    for prefix in prefixes:
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                objects.append(
                    {
                        "key": item["Key"],
                        "size": item["Size"],
                        "etag": str(item["ETag"]).strip('"'),
                    }
                )
    return sorted(objects, key=lambda item: str(item["key"]))


def _read_object(client: Any, bucket: str, key: str) -> bytes:
    response = client.get_object(Bucket=bucket, Key=key)
    body = response["Body"]
    try:
        return bytes(body.read())
    finally:
        body.close()


def _first_messages(raw_mcap: bytes) -> dict[str, dict[str, object]]:
    first: dict[str, dict[str, object]] = {}
    for _schema, channel, message in make_reader(io.BytesIO(raw_mcap)).iter_messages():
        if channel.topic not in first:
            first[channel.topic] = {
                "timestamp_ns": message.log_time,
                "payload_sha256": hashlib.sha256(message.data).hexdigest(),
                "payload": json.loads(message.data),
            }
    return first


def _assert_close(left: list[float], right: list[float], label: str) -> None:
    if len(left) != len(right) or any(
        abs(first - second) > 1e-8 for first, second in zip(left, right, strict=True)
    ):
        raise AssertionError(f"{label} values differ between Hugging Face and Raw MCAP")


def _validate_camera_stream(raw_mcap: bytes, source_video: Path) -> dict[str, object]:
    source_frames = extract_jpeg_frames(source_video)
    with source_video.open("rb") as source:
        source_video_bytes = source.read()
    attachments = tuple(make_reader(io.BytesIO(raw_mcap)).iter_attachments())
    if (
        len(attachments) != 1
        or attachments[0].media_type != "video/mp4"
        or attachments[0].data != source_video_bytes
    ):
        raise AssertionError("Raw MCAP does not preserve the exact Hugging Face source MP4")
    observed = 0
    first_sha256 = ""
    last_sha256 = ""
    total_bytes = 0
    for _schema, channel, message in make_reader(io.BytesIO(raw_mcap)).iter_messages():
        if channel.topic != CAMERA_TOPIC:
            continue
        payload = json.loads(message.data)
        frame_index = int(payload["frame_index"])
        if frame_index != observed:
            raise AssertionError("Raw MCAP camera frame_index is not contiguous")
        jpeg = base64.b64decode(payload["data_base64"], validate=True)
        digest = hashlib.sha256(jpeg).hexdigest()
        if payload["jpeg_sha256"] != digest or jpeg != source_frames[frame_index]:
            raise AssertionError(
                f"Raw MCAP camera frame {frame_index} differs from the Hugging Face video"
            )
        first_sha256 = first_sha256 or digest
        last_sha256 = digest
        total_bytes += len(jpeg)
        observed += 1
    if observed != len(source_frames) or observed != 469:
        raise AssertionError(f"expected 469 camera frames, found {observed}")
    return {
        "frames": observed,
        "jpeg_bytes": total_bytes,
        "first_frame_sha256": first_sha256,
        "last_frame_sha256": last_sha256,
        "source_mp4_attachment_name": attachments[0].name,
        "source_mp4_attachment_bytes": len(attachments[0].data),
        "source_mp4_attachment_sha256": hashlib.sha256(attachments[0].data).hexdigest(),
    }


def _summarize_first_messages(
    messages: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    summarized = json.loads(json.dumps(messages))
    camera_payload = summarized[CAMERA_TOPIC]["payload"]
    encoded = camera_payload.pop("data_base64")
    camera_payload["data_base64"] = f"<omitted {len(encoded)} base64 characters>"
    return summarized


def inspect_run(args: argparse.Namespace) -> dict[str, object]:
    scope = RunScope.create(args.run_id)
    postgres_dsn = args.postgres_dsn
    with psycopg.connect(postgres_dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.project_id', %s, false)", (scope.project_id,))
            cursor.execute("SELECT set_config('app.region_code', %s, false)", (scope.region_code,))
        rollout_objects = _query(
            connection,
            """
            SELECT rollout_id, object_key, manifest_key, source_sha256, crc64,
                   file_size, status, committed_at
            FROM ingest.rollout_objects
            WHERE project_id = %s AND region_code = %s
            ORDER BY committed_at
            """,
            (scope.project_id, scope.region_code),
        )
        outbox_events = _query(
            connection,
            """
            SELECT event_id::text, event_type, occurred_at, published_at,
                   publish_attempts, last_error, envelope
            FROM core.outbox_events
            WHERE project_id = %s AND region_code = %s
            ORDER BY occurred_at
            """,
            (scope.project_id, scope.region_code),
        )
        verification = _query(
            connection,
            """
            SELECT report_sha256, rollout_id, source_sha256, object_key, status,
                   report_json, created_at
            FROM raw_verification_reports
            WHERE project_id = %s AND region_code = %s
            ORDER BY created_at
            """,
            (scope.project_id, scope.region_code),
        )
        quality = _query(
            connection,
            """
            SELECT report_sha256, rollout_id, source_sha256, profile_id,
                   profile_version, engine_version, status, report_json, created_at
            FROM qc_reports
            WHERE project_id = %s AND region_code = %s
            ORDER BY created_at
            """,
            (scope.project_id, scope.region_code),
        )
        alignment = _query(
            connection,
            """
            SELECT rollout_id, source_sha256, converter_version, attempt_id,
                   content_sha256, schema_sha256, row_count, staging_uri,
                   staging_format, status, manifest_json, updated_at
            FROM aligned_fragment_attempts
            WHERE project_id = %s AND region_code = %s
            ORDER BY updated_at
            """,
            (scope.project_id, scope.region_code),
        )
        datasets = _query(
            connection,
            """
            SELECT dataset_id, schema_snapshot_id, frequency_hz, fingerprint,
                   dataset_uri, current_version, created_at, updated_at
            FROM lance_datasets
            WHERE project_id = %s
            ORDER BY dataset_id
            """,
            (scope.project_id,),
        )
        versions = _query(
            connection,
            """
            SELECT dataset_id, version, schema_snapshot_id, frequency_hz,
                   content_hash, dataset_uri, lance_version, storage_commit_id,
                   rollout_id, source_sha256, converter_version, committed_rollouts,
                   receipt_json, created_at
            FROM lance_dataset_versions
            WHERE project_id = %s
            ORDER BY dataset_id, version
            """,
            (scope.project_id,),
        )

    if len(rollout_objects) != 1:
        raise AssertionError(f"expected one committed rollout, found {len(rollout_objects)}")
    if not outbox_events or any(item["published_at"] is None for item in outbox_events):
        raise AssertionError("the upload outbox event was not durably published")
    if len(verification) != 1 or verification[0]["status"] != "RAW_VERIFIED":
        raise AssertionError("Raw MCAP verification did not succeed")
    if len(quality) != 1 or quality[0]["status"] != "PASS":
        raise AssertionError("QC did not pass")
    if len(alignment) != 1 or alignment[0]["status"] != "READY":
        raise AssertionError("the aligned Arrow fragment is not READY")
    if len(datasets) != 1 or len(versions) != 1 or datasets[0]["current_version"] != 1:
        raise AssertionError("the first logical Lance version was not indexed")

    s3 = boto3.client(
        "s3",
        endpoint_url=args.minio_endpoint,
        aws_access_key_id=args.minio_access_key,
        aws_secret_access_key=args.minio_secret_key,
        region_name=args.object_store_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    object_key = str(rollout_objects[0]["object_key"])
    manifest_key = str(rollout_objects[0]["manifest_key"])
    raw_mcap = _read_object(s3, args.bucket, object_key)
    stored_manifest = json.loads(_read_object(s3, args.bucket, manifest_key))
    if hashlib.sha256(raw_mcap).hexdigest() != rollout_objects[0]["source_sha256"]:
        raise AssertionError("Raw object bytes differ from the committed SHA-256")

    local_mcap = args.output_dir / "bundle" / "unitree-g1-episode-000000.mcap"
    if raw_mcap != local_mcap.read_bytes():
        raise AssertionError("MinIO Raw object differs from the locally converted MCAP")
    source_row = (
        parquet.read_table(args.output_dir / "source" / "episode_000000.parquet")
        .slice(0, 1)
        .to_pylist()[0]
    )
    messages = _first_messages(raw_mcap)
    _assert_close(
        [float(item) for item in source_row["states"]],
        [float(item) for item in messages[STATE_TOPIC]["payload"]["values"]],  # type: ignore[index]
        "state",
    )
    _assert_close(
        [float(item) for item in source_row["action"]],
        [float(item) for item in messages[ACTION_TOPIC]["payload"]["values"]],  # type: ignore[index]
        "action",
    )
    camera_evidence = _validate_camera_stream(
        raw_mcap, args.output_dir / "source" / "episode_000000.mp4"
    )

    raw_report = verification[0]["report_json"]
    raw_topics = {item["topic"]: item for item in raw_report["topics"]}
    if (
        set(raw_topics) != {STATE_TOPIC, ACTION_TOPIC, CAMERA_TOPIC}
        or raw_topics[CAMERA_TOPIC]["message_count"] != 469
        or raw_topics[CAMERA_TOPIC]["decodable"] is not True
    ):
        raise AssertionError("Raw verification did not validate the complete camera topic")
    qc_report = quality[0]["report_json"]
    qc_topics = {item["topic"]: item for item in qc_report["topic_metrics"]}
    if (
        set(qc_topics) != {STATE_TOPIC, ACTION_TOPIC, CAMERA_TOPIC}
        or qc_topics[CAMERA_TOPIC]["message_count"] != 469
    ):
        raise AssertionError("QC did not measure the complete camera topic")

    dataset_uri = str(datasets[0]["dataset_uri"])
    lance_dataset = lance.dataset(
        dataset_uri,
        version=int(versions[0]["lance_version"]),
        storage_options={
            "aws_endpoint": args.minio_endpoint,
            "aws_access_key_id": args.minio_access_key,
            "aws_secret_access_key": args.minio_secret_key,
            "aws_region": args.object_store_region,
            "allow_http": str(args.minio_endpoint.startswith("http://")).lower(),
        },
    )
    table = lance_dataset.to_table()
    first_lance_row = table.slice(0, 1).to_pylist()[0]
    expected_rows = int(alignment[0]["row_count"])
    if table.num_rows != expected_rows or expected_rows != 469:
        raise AssertionError(f"expected 469 Lance rows, found {table.num_rows}")
    valid_rows = sum(bool(value) for value in table["sample_valid"].to_pylist())
    if valid_rows != expected_rows:
        raise AssertionError(f"only {valid_rows}/{expected_rows} Lance steps are valid")
    for topic in (STATE_TOPIC, ACTION_TOPIC, CAMERA_TOPIC):
        reference = str(first_lance_row["modalities"][topic])
        if messages[topic]["payload_sha256"] not in reference:
            raise AssertionError(f"Lance {topic} does not point to its Raw MCAP payload")

    evidence: dict[str, object] = {
        "status": "PASS",
        "run_id": scope.run_id,
        "scope": {"project_id": scope.project_id, "region_code": scope.region_code},
        "checks": {
            "hf_first_state_values_match_raw_mcap": True,
            "hf_first_action_values_match_raw_mcap": True,
            "all_hf_video_frames_match_raw_mcap": True,
            "exact_hf_mp4_is_preserved_as_raw_attachment": True,
            "raw_camera_topic_is_decodable": True,
            "qc_measured_all_camera_timestamps": True,
            "raw_bytes_match_local_conversion": True,
            "raw_sha256_matches_commit": True,
            "lance_references_match_raw_message_sha256": True,
            "lance_row_count_matches_alignment": True,
            "all_lance_rows_are_valid": True,
        },
        "pipeline": {
            "upload_commit": rollout_objects,
            "outbox": outbox_events,
            "raw_verification": {
                "database": verification,
                "record_count": raw_report.get("record_count"),
                "message_count": raw_report.get("message_count"),
                "topics": raw_report.get("topics"),
                "findings": raw_report.get("findings"),
            },
            "qc": {
                "database": quality,
                "topic_metrics": qc_report.get("topic_metrics"),
                "findings": qc_report.get("findings"),
            },
            "alignment": alignment,
            "lance_catalog": {"datasets": datasets, "versions": versions},
        },
        "storage": {
            "bucket": args.bucket,
            "raw_object_key": object_key,
            "manifest_object_key": manifest_key,
            "objects": _object_keys(
                s3,
                args.bucket,
                (
                    scope.raw_prefix,
                    f"lance/{scope.project_id}/",
                    f"lance/_attempts/{scope.project_id}/",
                ),
            ),
            "lance_dataset_uri": dataset_uri,
            "lance_physical_version": lance_dataset.version,
            "lance_rows": table.num_rows,
            "lance_valid_rows": valid_rows,
            "lance_schema": str(table.schema),
            "first_lance_row": first_lance_row,
        },
        "lineage_sample": {
            "hugging_face": {
                "frame_index": source_row["frame_index"],
                "state_width": len(source_row["states"]),
                "action_width": len(source_row["action"]),
                "state_head": source_row["states"][:3],
                "action_head": source_row["action"][:3],
                "video": camera_evidence,
            },
            "raw_mcap_first_messages": _summarize_first_messages(messages),
            "stored_manifest": stored_manifest,
        },
        "implementation_limitations": [
            "Lance modality fields currently store mcap:// lineage references, "
            "not decoded vectors.",
            "QC decodes JSON/JPEG camera frames and evaluates all topic timing, "
            "but it does not inspect JSON action vector values.",
        ],
    }
    return _json_value(evidence)  # type: ignore[return-value]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--postgres-dsn",
        default=os.environ.get("HC_TEST_POSTGRES_DSN", "postgresql://hc:hc@127.0.0.1:5432/hc_data"),
    )
    parser.add_argument(
        "--minio-endpoint",
        default=os.environ.get("HC_MINIO_ENDPOINT", "http://127.0.0.1:19000"),
    )
    parser.add_argument("--bucket", default=os.environ.get("HC_MINIO_BUCKET", "hc-data-local"))
    parser.add_argument(
        "--minio-access-key", default=os.environ.get("HC_MINIO_ACCESS_KEY", "minio")
    )
    parser.add_argument(
        "--minio-secret-key",
        default=os.environ.get("HC_MINIO_SECRET_KEY", "minio-local-only"),
    )
    parser.add_argument(
        "--object-store-region", default=os.environ.get("HC_OBJECT_STORE_REGION", "us-east-1")
    )
    args = parser.parse_args()
    evidence = inspect_run(args)
    output_path = args.output_dir / "inspection-evidence.json"
    output_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
