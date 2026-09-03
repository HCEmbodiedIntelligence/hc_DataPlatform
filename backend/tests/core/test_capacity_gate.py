from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from hc_data_platform.core.capacity_evidence import CapacityEvidenceValidation
from hc_data_platform.core.capacity_gate import (
    CapacityGateError,
    validate_capacity_evidence_file,
)


def _digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def test_capacity_gate_requires_exact_digest_and_accepted_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b'{"schema_version":"hc-capacity-evidence/v1"}\n'
    evidence = tmp_path / "capacity.json"
    evidence.write_bytes(payload)
    monkeypatch.setattr(
        "hc_data_platform.core.capacity_gate.validate_capacity_evidence",
        lambda _document: CapacityEvidenceValidation(accepted=True, errors=()),
    )

    result = validate_capacity_evidence_file(evidence, expected_sha256=_digest(payload))

    assert result.evidence_sha256 == _digest(payload)
    assert result.evidence_bytes == len(payload)
    with pytest.raises(CapacityGateError, match="does not match"):
        validate_capacity_evidence_file(evidence, expected_sha256=f"sha256:{'0' * 64}")


def test_capacity_gate_preserves_strict_validator_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"{}\n"
    evidence = tmp_path / "capacity.json"
    evidence.write_bytes(payload)
    monkeypatch.setattr(
        "hc_data_platform.core.capacity_gate.validate_capacity_evidence",
        lambda _document: CapacityEvidenceValidation(
            accepted=False,
            errors=("$.run.environment_kind must be PRODUCTION or PRODUCTION_LIKE",),
        ),
    )

    with pytest.raises(CapacityGateError, match="PRODUCTION_LIKE"):
        validate_capacity_evidence_file(evidence, expected_sha256=_digest(payload))


def test_capacity_gate_rejects_invalid_json_before_contract_validation(tmp_path: Path) -> None:
    payload = b"not-json\n"
    evidence = tmp_path / "capacity.json"
    evidence.write_bytes(payload)

    with pytest.raises(CapacityGateError, match="UTF-8 JSON"):
        validate_capacity_evidence_file(evidence, expected_sha256=_digest(payload))
