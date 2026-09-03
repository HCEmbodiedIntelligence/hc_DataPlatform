"""Signed, short-lived disaster-recovery evidence accepted by release automation."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
OpaqueReference = Annotated[str, StringConstraints(pattern=r"^id-hmac-sha256:[0-9a-f]{64}$")]
ReasonCode = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")]
Scenario = Literal["DR7-01", "DR7-02", "DR7-03", "DR7-04", "DR7-05", "DR7-06"]
_SCENARIOS = frozenset({"DR7-01", "DR7-02", "DR7-03", "DR7-04", "DR7-05", "DR7-06"})


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DisasterRecoveryExerciseV1(_StrictModel):
    format_version: Literal["hc-platform-dr-exercise/v1"] = "hc-platform-dr-exercise/v1"
    scenario: Scenario
    release_manifest_digest: Digest
    environment_ref: OpaqueReference
    started_at: datetime
    completed_at: datetime
    result: Literal["PASS"]
    executor_ref: OpaqueReference
    approver_ref: OpaqueReference
    verifier_ref: OpaqueReference
    artifact_digests: tuple[Digest, ...] = Field(min_length=1, max_length=100)
    cleanup_verified: Literal[True]
    exclusions: tuple[ReasonCode, ...] = ()

    @model_validator(mode="after")
    def validate_evidence(self) -> DisasterRecoveryExerciseV1:
        if self.started_at.tzinfo is None or self.completed_at.tzinfo is None:
            raise ValueError("DR exercise timestamps must be timezone-aware")
        if self.completed_at <= self.started_at:
            raise ValueError("DR exercise completion must follow its start")
        if len({self.executor_ref, self.approver_ref, self.verifier_ref}) != 3:
            raise ValueError("DR executor, approver, and verifier must be distinct")
        if len(set(self.artifact_digests)) != len(self.artifact_digests):
            raise ValueError("DR evidence artifact digests must be unique")
        if self.exclusions:
            raise ValueError("a passing DR exercise cannot retain exclusions")
        return self


class DisasterRecoveryEvidenceBundleV1(_StrictModel):
    format_version: Literal["hc-platform-dr-evidence-bundle/v1"] = (
        "hc-platform-dr-evidence-bundle/v1"
    )
    release_manifest_digest: Digest
    generated_at: datetime
    valid_until: datetime
    exercises: tuple[DisasterRecoveryExerciseV1, ...] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def validate_bundle(self) -> DisasterRecoveryEvidenceBundleV1:
        if self.generated_at.tzinfo is None or self.valid_until.tzinfo is None:
            raise ValueError("DR evidence validity timestamps must be timezone-aware")
        if self.valid_until <= self.generated_at:
            raise ValueError("DR evidence validity window must be positive")
        if self.valid_until - self.generated_at > timedelta(days=31):
            raise ValueError("DR evidence validity cannot exceed 31 days")
        if {item.scenario for item in self.exercises} != _SCENARIOS:
            raise ValueError("DR evidence must contain each DR7-01 through DR7-06 scenario once")
        if any(
            item.release_manifest_digest != self.release_manifest_digest for item in self.exercises
        ):
            raise ValueError("DR exercise identity differs from its evidence bundle")
        if any(item.completed_at > self.generated_at for item in self.exercises):
            raise ValueError("DR evidence cannot be generated before an exercise completes")
        if any(
            self.generated_at - item.completed_at > timedelta(days=31) for item in self.exercises
        ):
            raise ValueError("DR exercises must have completed within the preceding 31 days")
        return self


class DisasterRecoveryEvidenceSignatureV1(_StrictModel):
    format_version: Literal["hc-platform-dr-evidence-signature/v1"] = (
        "hc-platform-dr-evidence-signature/v1"
    )
    algorithm: Literal["Ed25519"] = "Ed25519"
    public_key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature_base64url: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")


class SignedDisasterRecoveryEvidenceV1(_StrictModel):
    bundle: DisasterRecoveryEvidenceBundleV1
    signature: DisasterRecoveryEvidenceSignatureV1


class VerifiedDisasterRecoveryEvidenceV1(_StrictModel):
    release_manifest_digest: Digest
    bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signer_public_key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    valid_until: datetime


class DisasterRecoveryEvidenceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _canonical_bytes(model: BaseModel) -> bytes:
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _key_fingerprint(public_key: Ed25519PublicKey) -> str:
    return hashlib.sha256(public_key.public_bytes_raw()).hexdigest()


def sign_dr_evidence(
    bundle: DisasterRecoveryEvidenceBundleV1, *, private_key: Ed25519PrivateKey
) -> SignedDisasterRecoveryEvidenceV1:
    payload = _canonical_bytes(bundle)
    signature = private_key.sign(payload)
    return SignedDisasterRecoveryEvidenceV1(
        bundle=bundle,
        signature=DisasterRecoveryEvidenceSignatureV1(
            public_key_sha256=_key_fingerprint(private_key.public_key()),
            bundle_sha256=hashlib.sha256(payload).hexdigest(),
            signature_base64url=base64.urlsafe_b64encode(signature).decode().rstrip("="),
        ),
    )


def verify_dr_evidence(
    signed: SignedDisasterRecoveryEvidenceV1,
    *,
    trusted_public_keys: Mapping[str, Ed25519PublicKey],
    expected_release_manifest_digest: str,
    now: datetime | None = None,
) -> VerifiedDisasterRecoveryEvidenceV1:
    observed_at = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        raise ValueError("DR evidence verification time must be timezone-aware")
    payload = _canonical_bytes(signed.bundle)
    digest = hashlib.sha256(payload).hexdigest()
    if digest != signed.signature.bundle_sha256:
        raise DisasterRecoveryEvidenceError(
            "DR_EVIDENCE_DIGEST_MISMATCH", "bundle digest differs from its signature envelope"
        )
    public_key = trusted_public_keys.get(signed.signature.public_key_sha256)
    if public_key is None or _key_fingerprint(public_key) != signed.signature.public_key_sha256:
        raise DisasterRecoveryEvidenceError(
            "DR_EVIDENCE_SIGNER_UNTRUSTED", "evidence signer is not pinned"
        )
    try:
        signature = base64.urlsafe_b64decode(signed.signature.signature_base64url + "==")
    except (binascii.Error, ValueError) as exc:
        raise DisasterRecoveryEvidenceError(
            "DR_EVIDENCE_SIGNATURE_INVALID", "evidence signature encoding is invalid"
        ) from exc
    try:
        public_key.verify(signature, payload)
    except InvalidSignature as exc:
        raise DisasterRecoveryEvidenceError(
            "DR_EVIDENCE_SIGNATURE_INVALID", "evidence signature did not verify"
        ) from exc
    if signed.bundle.release_manifest_digest != expected_release_manifest_digest:
        raise DisasterRecoveryEvidenceError(
            "DR_EVIDENCE_RELEASE_MISMATCH", "evidence belongs to another release manifest"
        )
    if observed_at < signed.bundle.generated_at:
        raise DisasterRecoveryEvidenceError(
            "DR_EVIDENCE_NOT_YET_VALID", "evidence validity begins in the future"
        )
    if observed_at > signed.bundle.valid_until:
        raise DisasterRecoveryEvidenceError("DR_EVIDENCE_EXPIRED", "evidence is no longer valid")
    return VerifiedDisasterRecoveryEvidenceV1(
        release_manifest_digest=signed.bundle.release_manifest_digest,
        bundle_sha256=digest,
        signer_public_key_sha256=signed.signature.public_key_sha256,
        valid_until=signed.bundle.valid_until,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify signed DR7 release-gate evidence")
    parser.add_argument("--document", required=True, type=Path)
    parser.add_argument("--expected-release-manifest-digest", required=True)
    parser.add_argument("--trusted-public-key", action="append", required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    signed = SignedDisasterRecoveryEvidenceV1.model_validate_json(
        args.document.read_text(encoding="utf-8")
    )
    keys: dict[str, Ed25519PublicKey] = {}
    for encoded in args.trusted_public_key:
        try:
            key = Ed25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(encoded + "=="))
        except (binascii.Error, ValueError) as exc:
            raise SystemExit("trusted DR evidence keys must be raw Ed25519 base64url") from exc
        keys[_key_fingerprint(key)] = key
    verified = verify_dr_evidence(
        signed,
        trusted_public_keys=keys,
        expected_release_manifest_digest=args.expected_release_manifest_digest,
    )
    print(verified.model_dump_json())


__all__ = [
    "DisasterRecoveryEvidenceBundleV1",
    "DisasterRecoveryEvidenceError",
    "DisasterRecoveryExerciseV1",
    "SignedDisasterRecoveryEvidenceV1",
    "VerifiedDisasterRecoveryEvidenceV1",
    "sign_dr_evidence",
    "verify_dr_evidence",
]
