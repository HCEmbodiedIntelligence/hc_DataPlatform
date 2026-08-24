"""Fail-closed validation for production end-to-end capacity evidence."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

TARGET_BYTES_PER_DAY = 5_000_000_000_000
HEADROOM_RATIO = 1.30
REQUIRED_BYTES_PER_SECOND = TARGET_BYTES_PER_DAY * HEADROOM_RATIO / 86_400
MINIMUM_DURATION_SECONDS = 30 * 60
MAXIMUM_ERROR_RATE = 0.01
MAXIMUM_RETRY_RATE = 0.05
MAXIMUM_RESOURCE_P95_PERCENT = 95.0

REQUIRED_STAGES = frozenset(
    {
        "api",
        "postgresql",
        "object_storage",
        "temporal",
        "quality_control",
        "lance",
        "preview",
        "export",
    }
)
BYTE_THROUGHPUT_STAGES = frozenset({"object_storage", "lance", "preview", "export"})
REQUIRED_RESOURCES = frozenset(
    {"api", "postgresql", "object_storage", "temporal_worker", "lance", "preview", "export"}
)
REQUIRED_RECOVERY_FAULTS = frozenset({"object_storage_interruption", "worker_restart"})


@dataclass(frozen=True, slots=True)
class CapacityEvidenceValidation:
    accepted: bool
    errors: tuple[str, ...]


def _mapping(value: object, path: str, errors: list[str]) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        errors.append(f"{path} must be an object with string keys")
        return None
    return value


def _exact_keys(
    value: Mapping[str, object], expected: frozenset[str], path: str, errors: list[str]
) -> None:
    actual = frozenset(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        errors.append(f"{path} is missing keys: {missing}")
    if unknown:
        errors.append(f"{path} has {len(unknown)} unknown key(s)")


def _number(value: object, path: str, errors: list[str], *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        errors.append(f"{path} must be a finite number")
        return math.nan
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        errors.append(f"{path} must be finite and at least {minimum}")
    return result


def _integer(value: object, path: str, errors: list[str], *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        errors.append(f"{path} must be an integer at least {minimum}")
        return -1
    return value


def _nonempty_string(value: object, path: str, errors: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path} must be a non-empty string")
        return ""
    return value.strip()


def _timestamp(value: object, path: str, errors: list[str]) -> datetime | None:
    encoded = _nonempty_string(value, path, errors)
    if not encoded:
        return None
    try:
        parsed = datetime.fromisoformat(encoded.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{path} must be an ISO-8601 timestamp")
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        errors.append(f"{path} must include a timezone")
        return None
    return parsed


def _percentiles(
    value: object,
    path: str,
    errors: list[str],
    *,
    with_minimum: bool,
) -> Mapping[str, float] | None:
    result = _mapping(value, path, errors)
    if result is None:
        return None
    expected = frozenset(
        {"minimum", "p50", "p95", "p99"} if with_minimum else {"p50", "p95", "p99"}
    )
    _exact_keys(result, expected, path, errors)
    values = {key: _number(result.get(key), f"{path}.{key}", errors) for key in expected}
    ordered = [values[key] for key in (["minimum"] if with_minimum else []) + ["p50", "p95", "p99"]]
    if all(math.isfinite(item) for item in ordered) and ordered != sorted(ordered):
        errors.append(f"{path} percentiles must be monotonic")
    return values


def _rates(value: Mapping[str, object], path: str, errors: list[str]) -> None:
    operations = _integer(
        value.get("operations_total"), f"{path}.operations_total", errors, minimum=1
    )
    failures = _integer(value.get("errors_total"), f"{path}.errors_total", errors)
    retries = _integer(value.get("retries_total"), f"{path}.retries_total", errors)
    error_rate = _number(value.get("error_rate"), f"{path}.error_rate", errors)
    retry_rate = _number(value.get("retry_rate"), f"{path}.retry_rate", errors)
    if (
        operations > 0
        and failures >= 0
        and not math.isclose(error_rate, failures / operations, rel_tol=0.0, abs_tol=1e-9)
    ):
        errors.append(f"{path}.error_rate does not match errors_total / operations_total")
    if (
        operations > 0
        and retries >= 0
        and not math.isclose(retry_rate, retries / operations, rel_tol=0.0, abs_tol=1e-9)
    ):
        errors.append(f"{path}.retry_rate does not match retries_total / operations_total")
    if math.isfinite(error_rate) and error_rate > MAXIMUM_ERROR_RATE:
        errors.append(f"{path}.error_rate exceeds {MAXIMUM_ERROR_RATE}")
    if math.isfinite(retry_rate) and retry_rate > MAXIMUM_RETRY_RATE:
        errors.append(f"{path}.retry_rate exceeds {MAXIMUM_RETRY_RATE}")


def validate_capacity_evidence(document: object) -> CapacityEvidenceValidation:
    """Accept only complete, sustained, production-like full-chain PASS evidence."""

    errors: list[str] = []
    root = _mapping(document, "$", errors)
    if root is None:
        return CapacityEvidenceValidation(accepted=False, errors=tuple(errors))
    _exact_keys(
        root,
        frozenset(
            {
                "schema_version",
                "run",
                "target",
                "workload",
                "end_to_end",
                "stages",
                "saturation",
                "recovery",
                "cleanup",
                "verdict",
            }
        ),
        "$",
        errors,
    )
    if root.get("schema_version") != "hc-capacity-evidence/v1":
        errors.append("$.schema_version must equal hc-capacity-evidence/v1")

    run = _mapping(root.get("run"), "$.run", errors)
    duration = math.nan
    if run is not None:
        _exact_keys(
            run,
            frozenset(
                {
                    "run_id",
                    "deployment_id",
                    "environment_kind",
                    "mock_mode",
                    "started_at",
                    "finished_at",
                    "duration_seconds",
                }
            ),
            "$.run",
            errors,
        )
        _nonempty_string(run.get("run_id"), "$.run.run_id", errors)
        _nonempty_string(run.get("deployment_id"), "$.run.deployment_id", errors)
        if run.get("environment_kind") not in {"PRODUCTION", "PRODUCTION_LIKE"}:
            errors.append("$.run.environment_kind must be PRODUCTION or PRODUCTION_LIKE")
        if run.get("mock_mode") != "off":
            errors.append("$.run.mock_mode must equal off")
        started = _timestamp(run.get("started_at"), "$.run.started_at", errors)
        finished = _timestamp(run.get("finished_at"), "$.run.finished_at", errors)
        duration = _number(
            run.get("duration_seconds"),
            "$.run.duration_seconds",
            errors,
            minimum=MINIMUM_DURATION_SECONDS,
        )
        if started is not None and finished is not None:
            measured = (finished - started).total_seconds()
            if measured < MINIMUM_DURATION_SECONDS:
                errors.append("$.run timestamps span less than 30 minutes")
            if math.isfinite(duration) and not math.isclose(
                measured, duration, rel_tol=0.0, abs_tol=1.0
            ):
                errors.append("$.run.duration_seconds does not match its timestamps")

    target = _mapping(root.get("target"), "$.target", errors)
    required_throughput = math.nan
    if target is not None:
        _exact_keys(
            target,
            frozenset({"bytes_per_day", "headroom_ratio", "required_bytes_per_second"}),
            "$.target",
            errors,
        )
        if target.get("bytes_per_day") != TARGET_BYTES_PER_DAY:
            errors.append(f"$.target.bytes_per_day must equal {TARGET_BYTES_PER_DAY}")
        headroom = _number(target.get("headroom_ratio"), "$.target.headroom_ratio", errors)
        if not math.isclose(headroom, HEADROOM_RATIO, rel_tol=0.0, abs_tol=1e-12):
            errors.append(f"$.target.headroom_ratio must equal {HEADROOM_RATIO}")
        required_throughput = _number(
            target.get("required_bytes_per_second"),
            "$.target.required_bytes_per_second",
            errors,
        )
        if not math.isclose(
            required_throughput, REQUIRED_BYTES_PER_SECOND, rel_tol=0.0, abs_tol=0.001
        ):
            errors.append(
                "$.target.required_bytes_per_second does not equal the 5 TB/day + 30% target"
            )

    workload = _mapping(root.get("workload"), "$.workload", errors)
    completed_bytes = -1
    if workload is not None:
        _exact_keys(
            workload,
            frozenset(
                {"object_sizes_bytes", "concurrency", "completed_rollouts", "completed_bytes"}
            ),
            "$.workload",
            errors,
        )
        sizes = workload.get("object_sizes_bytes")
        if not isinstance(sizes, Sequence) or isinstance(sizes, str | bytes) or not sizes:
            errors.append("$.workload.object_sizes_bytes must be a non-empty array")
        else:
            for index, size in enumerate(sizes):
                _integer(size, f"$.workload.object_sizes_bytes[{index}]", errors, minimum=1)
        _integer(workload.get("concurrency"), "$.workload.concurrency", errors, minimum=1)
        _integer(
            workload.get("completed_rollouts"),
            "$.workload.completed_rollouts",
            errors,
            minimum=1,
        )
        completed_bytes = _integer(
            workload.get("completed_bytes"), "$.workload.completed_bytes", errors, minimum=1
        )
        if (
            completed_bytes >= 0
            and math.isfinite(duration)
            and math.isfinite(required_throughput)
            and completed_bytes < required_throughput * duration
        ):
            errors.append("$.workload.completed_bytes is below the sustained target window")

    end_to_end = _mapping(root.get("end_to_end"), "$.end_to_end", errors)
    if end_to_end is not None:
        _exact_keys(
            end_to_end,
            frozenset(
                {
                    "throughput_bytes_per_second",
                    "latency_seconds",
                    "operations_total",
                    "errors_total",
                    "retries_total",
                    "error_rate",
                    "retry_rate",
                }
            ),
            "$.end_to_end",
            errors,
        )
        throughput = _percentiles(
            end_to_end.get("throughput_bytes_per_second"),
            "$.end_to_end.throughput_bytes_per_second",
            errors,
            with_minimum=True,
        )
        _percentiles(
            end_to_end.get("latency_seconds"),
            "$.end_to_end.latency_seconds",
            errors,
            with_minimum=False,
        )
        _rates(end_to_end, "$.end_to_end", errors)
        if (
            throughput is not None
            and math.isfinite(required_throughput)
            and throughput["minimum"] < required_throughput
        ):
            errors.append("$.end_to_end throughput minimum is below the capacity target")

    stages = _mapping(root.get("stages"), "$.stages", errors)
    if stages is not None:
        _exact_keys(stages, REQUIRED_STAGES, "$.stages", errors)
        for name in sorted(REQUIRED_STAGES):
            stage = _mapping(stages.get(name), f"$.stages.{name}", errors)
            if stage is None:
                continue
            _exact_keys(
                stage,
                frozenset(
                    {
                        "status",
                        "latency_seconds",
                        "throughput_bytes_per_second",
                        "operations_total",
                        "errors_total",
                        "retries_total",
                        "error_rate",
                        "retry_rate",
                    }
                ),
                f"$.stages.{name}",
                errors,
            )
            if stage.get("status") != "PASS":
                errors.append(f"$.stages.{name}.status must equal PASS")
            _percentiles(
                stage.get("latency_seconds"),
                f"$.stages.{name}.latency_seconds",
                errors,
                with_minimum=False,
            )
            stage_throughput = stage.get("throughput_bytes_per_second")
            if name in BYTE_THROUGHPUT_STAGES or stage_throughput is not None:
                _percentiles(
                    stage_throughput,
                    f"$.stages.{name}.throughput_bytes_per_second",
                    errors,
                    with_minimum=True,
                )
            _rates(stage, f"$.stages.{name}", errors)

    saturation = _mapping(root.get("saturation"), "$.saturation", errors)
    if saturation is not None:
        _exact_keys(saturation, REQUIRED_RESOURCES, "$.saturation", errors)
        for name in sorted(REQUIRED_RESOURCES):
            resource = _mapping(saturation.get(name), f"$.saturation.{name}", errors)
            if resource is None:
                continue
            _exact_keys(
                resource,
                frozenset(
                    {
                        "cpu_percent_p95",
                        "memory_percent_p95",
                        "disk_percent_p95",
                        "network_percent_p95",
                        "saturated",
                    }
                ),
                f"$.saturation.{name}",
                errors,
            )
            for metric in (
                "cpu_percent_p95",
                "memory_percent_p95",
                "disk_percent_p95",
                "network_percent_p95",
            ):
                observed = _number(resource.get(metric), f"$.saturation.{name}.{metric}", errors)
                if math.isfinite(observed) and observed >= MAXIMUM_RESOURCE_P95_PERCENT:
                    errors.append(
                        f"$.saturation.{name}.{metric} must be below {MAXIMUM_RESOURCE_P95_PERCENT}"
                    )
            if resource.get("saturated") is not False:
                errors.append(f"$.saturation.{name}.saturated must be false")

    recovery = _mapping(root.get("recovery"), "$.recovery", errors)
    if recovery is not None:
        _exact_keys(
            recovery,
            frozenset({"faults_injected", "all_recovered", "duplicate_side_effects"}),
            "$.recovery",
            errors,
        )
        raw_faults = recovery.get("faults_injected")
        faults = (
            frozenset(raw_faults)
            if isinstance(raw_faults, Sequence)
            and not isinstance(raw_faults, str | bytes)
            and all(isinstance(item, str) for item in raw_faults)
            else frozenset()
        )
        if not faults >= REQUIRED_RECOVERY_FAULTS:
            errors.append(
                "$.recovery.faults_injected must include object_storage_interruption "
                "and worker_restart"
            )
        if recovery.get("all_recovered") is not True:
            errors.append("$.recovery.all_recovered must be true")
        if recovery.get("duplicate_side_effects") != 0:
            errors.append("$.recovery.duplicate_side_effects must equal zero")

    cleanup = _mapping(root.get("cleanup"), "$.cleanup", errors)
    if cleanup is not None:
        _exact_keys(
            cleanup,
            frozenset({"verified", "residual_database_rows", "residual_objects"}),
            "$.cleanup",
            errors,
        )
        if cleanup.get("verified") is not True:
            errors.append("$.cleanup.verified must be true")
        if cleanup.get("residual_database_rows") != 0:
            errors.append("$.cleanup.residual_database_rows must equal zero")
        if cleanup.get("residual_objects") != 0:
            errors.append("$.cleanup.residual_objects must equal zero")

    verdict = _mapping(root.get("verdict"), "$.verdict", errors)
    if verdict is not None:
        _exact_keys(verdict, frozenset({"end_to_end_5tb_per_day"}), "$.verdict", errors)
        if verdict.get("end_to_end_5tb_per_day") != "PASS":
            errors.append("$.verdict.end_to_end_5tb_per_day must equal PASS")

    return CapacityEvidenceValidation(accepted=not errors, errors=tuple(errors))
