"""Digest-bound release preflight for strict production capacity evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from .capacity_evidence import validate_capacity_evidence

MAX_EVIDENCE_BYTES = 1_000_000


class CapacityGateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CapacityGateResult:
    evidence_sha256: str
    evidence_bytes: int


def validate_capacity_evidence_file(
    path: Path,
    *,
    expected_sha256: str,
) -> CapacityGateResult:
    if not expected_sha256.startswith("sha256:") or len(expected_sha256) != 71:
        raise CapacityGateError("expected capacity evidence SHA-256 is invalid")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise CapacityGateError("capacity evidence file is unavailable") from exc
    if size < 1 or size > MAX_EVIDENCE_BYTES:
        raise CapacityGateError("capacity evidence file size is outside the allowed range")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise CapacityGateError("capacity evidence file cannot be read") from exc
    actual_sha256 = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    if actual_sha256 != expected_sha256:
        raise CapacityGateError("capacity evidence SHA-256 does not match the release input")
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CapacityGateError("capacity evidence is not valid UTF-8 JSON") from exc
    validation = validate_capacity_evidence(document)
    if not validation.accepted:
        detail = "; ".join(validation.errors[:20])
        raise CapacityGateError(f"capacity evidence rejected: {detail}")
    return CapacityGateResult(
        evidence_sha256=actual_sha256,
        evidence_bytes=len(payload),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate pinned production capacity evidence")
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--expected-sha256", required=True)
    args = parser.parse_args()
    try:
        result = validate_capacity_evidence_file(
            args.evidence,
            expected_sha256=args.expected_sha256,
        )
    except CapacityGateError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    print(
        json.dumps(
            {
                "status": "PASS",
                "schema_version": "hc-capacity-gate-result/v1",
                "evidence_sha256": result.evidence_sha256,
                "evidence_bytes": result.evidence_bytes,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
