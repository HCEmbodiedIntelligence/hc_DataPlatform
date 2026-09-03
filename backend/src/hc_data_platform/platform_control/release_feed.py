"""Signed, monotonic release-feed contract for the external release controller."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .release_contract import AdjacentReleaseEdgeV1

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
ReleaseId = Annotated[
    str,
    StringConstraints(pattern=r"^platform-v[A-Za-z0-9][A-Za-z0-9._-]{0,119}$"),
]
ImageReference = Annotated[
    str,
    StringConstraints(
        min_length=10,
        max_length=512,
        pattern=r"^[^@\s]+@sha256:[0-9a-f]{64}$",
    ),
]
SafeName = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,126}[A-Za-z0-9]$"),
]
_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReleaseImagesV1(_StrictModel):
    frontend: ImageReference
    api: ImageReference
    worker: ImageReference
    media_worker: ImageReference


class ReleaseDatabaseV1(_StrictModel):
    migration_count: int = Field(gt=0)
    migration_manifest_sha256: Sha256
    expand_migrations: tuple[SafeName, ...]
    contract_migrations: tuple[SafeName, ...]


class ReleaseTemporalV1(_StrictModel):
    server_version_range: str = Field(min_length=1, max_length=127)
    task_queues: tuple[SafeName, ...] = Field(min_length=1)
    source_build_id: SafeName
    target_build_id: SafeName
    patch_set_sha256: Sha256


class ReleaseSupplyChainV1(_StrictModel):
    sbom_sha256: Sha256
    provenance_sha256: Sha256
    chart_package_sha256: Sha256
    dr_evidence_bundle_sha256: Sha256


class PlatformReleaseManifestV1(_StrictModel):
    format_version: Literal["hc-platform-release/v1"] = "hc-platform-release/v1"
    release_id: ReleaseId
    semantic_version: str
    git_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    chart_version: str
    images: ReleaseImagesV1
    database: ReleaseDatabaseV1
    openapi_sha256: Sha256
    generated_client_sha256: Sha256
    configuration_sha256: Sha256
    temporal: ReleaseTemporalV1
    supply_chain: ReleaseSupplyChainV1
    minimum_source_version: str
    published_at: datetime

    @model_validator(mode="after")
    def validate_versions_and_identity(self) -> PlatformReleaseManifestV1:
        target = _parse_semver(self.semantic_version)
        source = _parse_semver(self.minimum_source_version)
        _parse_semver(self.chart_version)
        if target <= source:
            raise ValueError("release semantic version must be newer than its minimum source")
        if self.release_id != f"platform-v{self.semantic_version}":
            raise ValueError("release ID must be the canonical semantic-version identity")
        if self.published_at.tzinfo is None:
            raise ValueError("release publication time must be timezone-aware")
        if len(set(self.temporal.task_queues)) != len(self.temporal.task_queues):
            raise ValueError("release Temporal task queues must be unique")
        if len(set((*self.database.expand_migrations, *self.database.contract_migrations))) != (
            len(self.database.expand_migrations) + len(self.database.contract_migrations)
        ):
            raise ValueError("release migration phase entries must be unique")
        return self


class ReleaseFeedCandidateV1(_StrictModel):
    manifest_sha256: Sha256
    manifest: PlatformReleaseManifestV1
    compatibility: AdjacentReleaseEdgeV1

    @model_validator(mode="after")
    def bind_manifest_to_compatibility_edge(self) -> ReleaseFeedCandidateV1:
        actual = hashlib.sha256(canonical_json_bytes(self.manifest)).hexdigest()
        if actual != self.manifest_sha256:
            raise ValueError("release manifest digest does not match its canonical bytes")
        if self.compatibility.target_version != self.manifest.semantic_version:
            raise ValueError("compatibility target does not match the release manifest")
        if self.compatibility.source_version != self.manifest.minimum_source_version:
            raise ValueError("compatibility source does not match the release manifest")
        return self


class PlatformReleaseFeedV1(_StrictModel):
    format_version: Literal["hc-platform-release-feed/v1"] = "hc-platform-release-feed/v1"
    sequence: int = Field(gt=0)
    generated_at: datetime
    candidates: tuple[ReleaseFeedCandidateV1, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_order_and_uniqueness(self) -> PlatformReleaseFeedV1:
        if self.generated_at.tzinfo is None:
            raise ValueError("release feed generation time must be timezone-aware")
        versions = [_parse_semver(item.manifest.semantic_version) for item in self.candidates]
        if versions != sorted(versions):
            raise ValueError("release feed candidates must be ordered by semantic version")
        if len(set(versions)) != len(versions):
            raise ValueError("release feed candidate versions must be unique")
        return self


class ReleaseFeedSignatureV1(_StrictModel):
    format_version: Literal["hc-platform-release-feed-signature/v1"] = (
        "hc-platform-release-feed-signature/v1"
    )
    algorithm: Literal["Ed25519"] = "Ed25519"
    public_key_sha256: Sha256
    feed_sha256: Sha256
    signature_base64url: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")


class SignedPlatformReleaseFeedV1(_StrictModel):
    feed: PlatformReleaseFeedV1
    signature: ReleaseFeedSignatureV1


class VerifiedPlatformReleaseFeedV1(_StrictModel):
    feed: PlatformReleaseFeedV1
    feed_sha256: Sha256
    signer_public_key_sha256: Sha256


class ReleaseFeedError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def canonical_json_bytes(model: BaseModel) -> bytes:
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def public_key_sha256(public_key: Ed25519PublicKey) -> str:
    return hashlib.sha256(public_key.public_bytes_raw()).hexdigest()


def sign_release_feed(
    feed: PlatformReleaseFeedV1,
    *,
    private_key: object,
) -> SignedPlatformReleaseFeedV1:
    """Test/build helper accepting an Ed25519 private key without persisting it."""

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if not isinstance(private_key, Ed25519PrivateKey):
        raise TypeError("release feeds require an Ed25519 private key")
    payload = canonical_json_bytes(feed)
    signature = private_key.sign(payload)
    return SignedPlatformReleaseFeedV1(
        feed=feed,
        signature=ReleaseFeedSignatureV1(
            public_key_sha256=public_key_sha256(private_key.public_key()),
            feed_sha256=hashlib.sha256(payload).hexdigest(),
            signature_base64url=base64.urlsafe_b64encode(signature).decode().rstrip("="),
        ),
    )


class ReleaseFeedVerifier:
    def __init__(self, trusted_public_keys: Mapping[str, Ed25519PublicKey]) -> None:
        if not trusted_public_keys:
            raise ValueError("at least one release-feed signing key must be pinned")
        for fingerprint, key in trusted_public_keys.items():
            if fingerprint != public_key_sha256(key):
                raise ValueError("release-feed signing key fingerprint mismatch")
        self._trusted_public_keys = dict(trusted_public_keys)

    def verify(
        self,
        signed: SignedPlatformReleaseFeedV1,
        *,
        minimum_sequence: int,
        current_version: str,
    ) -> VerifiedPlatformReleaseFeedV1:
        payload = canonical_json_bytes(signed.feed)
        feed_sha256 = hashlib.sha256(payload).hexdigest()
        if feed_sha256 != signed.signature.feed_sha256:
            raise ReleaseFeedError(
                "RELEASE_FEED_DIGEST_MISMATCH",
                "the release feed digest does not match its canonical content",
            )
        public_key = self._trusted_public_keys.get(signed.signature.public_key_sha256)
        if public_key is None:
            raise ReleaseFeedError(
                "RELEASE_FEED_SIGNER_UNTRUSTED",
                "the release feed signer is not pinned",
            )
        try:
            signature = base64.urlsafe_b64decode(signed.signature.signature_base64url + "==")
        except (binascii.Error, ValueError) as exc:
            raise ReleaseFeedError(
                "RELEASE_FEED_SIGNATURE_INVALID",
                "the release feed signature encoding is invalid",
            ) from exc
        try:
            public_key.verify(signature, payload)
        except InvalidSignature as exc:
            raise ReleaseFeedError(
                "RELEASE_FEED_SIGNATURE_INVALID",
                "the release feed signature did not verify",
            ) from exc
        if signed.feed.sequence < minimum_sequence:
            raise ReleaseFeedError(
                "RELEASE_FEED_ROLLBACK_REJECTED",
                "the release feed sequence is older than the trusted checkpoint",
            )
        current = _parse_semver(current_version)
        if any(
            _parse_semver(item.manifest.semantic_version) <= current
            for item in signed.feed.candidates
        ):
            raise ReleaseFeedError(
                "RELEASE_FEED_DOWNGRADE_REJECTED",
                "the release feed contains a non-forward target",
            )
        for candidate in signed.feed.candidates:
            edge = candidate.compatibility
            if edge.status not in {"READY_FOR_CANARY", "RELEASED"}:
                raise ReleaseFeedError(
                    "RELEASE_FEED_COMPATIBILITY_REJECTED",
                    "a release candidate is not approved by the compatibility matrix",
                )
            if not edge.target_artifacts_built_and_signed or edge.release_blockers:
                raise ReleaseFeedError(
                    "RELEASE_FEED_COMPATIBILITY_REJECTED",
                    "a release candidate retains unsigned artifacts or blockers",
                )
        return VerifiedPlatformReleaseFeedV1(
            feed=signed.feed,
            feed_sha256=feed_sha256,
            signer_public_key_sha256=signed.signature.public_key_sha256,
        )


def _parse_semver(value: str) -> tuple[int, int, int]:
    match = _SEMVER.fullmatch(value)
    if match is None:
        raise ValueError("release versions must be stable semantic versions")
    return tuple(int(component) for component in match.groups())  # type: ignore[return-value]


__all__ = [
    "PlatformReleaseFeedV1",
    "PlatformReleaseManifestV1",
    "ReleaseFeedCandidateV1",
    "ReleaseFeedError",
    "ReleaseFeedSignatureV1",
    "ReleaseFeedVerifier",
    "ReleaseImagesV1",
    "SignedPlatformReleaseFeedV1",
    "VerifiedPlatformReleaseFeedV1",
    "canonical_json_bytes",
    "public_key_sha256",
    "sign_release_feed",
]
