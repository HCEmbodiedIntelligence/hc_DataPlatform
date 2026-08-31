from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from hc_data_platform.platform_control.dr_evidence import (
    DisasterRecoveryEvidenceBundleV1,
    DisasterRecoveryEvidenceError,
    DisasterRecoveryExerciseV1,
    sign_dr_evidence,
    verify_dr_evidence,
)

NOW = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
RELEASE = f"sha256:{'a' * 64}"
REFERENCE = "id-hmac-sha256:" + "b" * 64


def _exercise(index: int, **updates: object) -> DisasterRecoveryExerciseV1:
    document: dict[str, object] = {
        "scenario": f"DR7-0{index}",
        "release_manifest_digest": RELEASE,
        "environment_ref": REFERENCE,
        "started_at": NOW - timedelta(hours=2),
        "completed_at": NOW - timedelta(hours=1),
        "result": "PASS",
        "executor_ref": "id-hmac-sha256:" + "c" * 64,
        "approver_ref": "id-hmac-sha256:" + "d" * 64,
        "verifier_ref": "id-hmac-sha256:" + "e" * 64,
        "artifact_digests": [f"sha256:{index:064x}"],
        "cleanup_verified": True,
        "exclusions": [],
    }
    document.update(updates)
    return DisasterRecoveryExerciseV1.model_validate(document)


def _bundle() -> DisasterRecoveryEvidenceBundleV1:
    return DisasterRecoveryEvidenceBundleV1(
        release_manifest_digest=RELEASE,
        generated_at=NOW,
        valid_until=NOW + timedelta(days=30),
        exercises=tuple(_exercise(index) for index in range(1, 7)),
    )


def test_signed_dr_bundle_requires_all_scenarios_and_verifies_release_identity() -> None:
    key = Ed25519PrivateKey.generate()
    signed = sign_dr_evidence(_bundle(), private_key=key)
    fingerprint = signed.signature.public_key_sha256

    verified = verify_dr_evidence(
        signed,
        trusted_public_keys={fingerprint: key.public_key()},
        expected_release_manifest_digest=RELEASE,
        now=NOW + timedelta(days=2),
    )

    assert verified.release_manifest_digest == RELEASE
    assert verified.bundle_sha256 == signed.signature.bundle_sha256
    assert verified.valid_until == NOW + timedelta(days=30)


def test_dr_bundle_rejects_missing_scenario_shared_duties_and_exclusions() -> None:
    with pytest.raises(ValidationError, match="each DR7-01 through DR7-06"):
        DisasterRecoveryEvidenceBundleV1(
            release_manifest_digest=RELEASE,
            generated_at=NOW,
            valid_until=NOW + timedelta(days=1),
            exercises=tuple(_exercise(index) for index in range(1, 6)) + (_exercise(5),),
        )
    with pytest.raises(ValidationError, match="must be distinct"):
        _exercise(1, approver_ref="id-hmac-sha256:" + "c" * 64)
    with pytest.raises(ValidationError, match="cannot retain exclusions"):
        _exercise(1, exclusions=["PROVIDER_DRILL_SKIPPED"])
    with pytest.raises(ValidationError, match="preceding 31 days"):
        DisasterRecoveryEvidenceBundleV1(
            release_manifest_digest=RELEASE,
            generated_at=NOW,
            valid_until=NOW + timedelta(days=1),
            exercises=(
                _exercise(
                    1,
                    started_at=NOW - timedelta(days=32, hours=2),
                    completed_at=NOW - timedelta(days=32),
                ),
            )
            + tuple(_exercise(index) for index in range(2, 7)),
        )


def test_dr_evidence_rejects_tampering_wrong_release_and_expiry() -> None:
    key = Ed25519PrivateKey.generate()
    signed = sign_dr_evidence(_bundle(), private_key=key)
    trusted = {signed.signature.public_key_sha256: key.public_key()}
    tampered_bundle = signed.bundle.model_copy(
        update={"valid_until": signed.bundle.valid_until + timedelta(hours=1)}
    )
    tampered = signed.model_copy(update={"bundle": tampered_bundle})

    with pytest.raises(DisasterRecoveryEvidenceError) as digest_error:
        verify_dr_evidence(
            tampered,
            trusted_public_keys=trusted,
            expected_release_manifest_digest=RELEASE,
            now=NOW,
        )
    assert digest_error.value.code == "DR_EVIDENCE_DIGEST_MISMATCH"

    with pytest.raises(DisasterRecoveryEvidenceError) as release_error:
        verify_dr_evidence(
            signed,
            trusted_public_keys=trusted,
            expected_release_manifest_digest=f"sha256:{'f' * 64}",
            now=NOW,
        )
    assert release_error.value.code == "DR_EVIDENCE_RELEASE_MISMATCH"

    with pytest.raises(DisasterRecoveryEvidenceError) as future:
        verify_dr_evidence(
            signed,
            trusted_public_keys=trusted,
            expected_release_manifest_digest=RELEASE,
            now=NOW - timedelta(seconds=1),
        )
    assert future.value.code == "DR_EVIDENCE_NOT_YET_VALID"

    with pytest.raises(DisasterRecoveryEvidenceError) as expired:
        verify_dr_evidence(
            signed,
            trusted_public_keys=trusted,
            expected_release_manifest_digest=RELEASE,
            now=NOW + timedelta(days=31),
        )
    assert expired.value.code == "DR_EVIDENCE_EXPIRED"
