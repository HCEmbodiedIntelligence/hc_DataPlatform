from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, cast

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from hc_data_platform.platform_control.release_contract import AdjacentReleaseEdgeV1
from hc_data_platform.platform_control.release_feed import (
    PlatformReleaseFeedV1,
    PlatformReleaseManifestV1,
    ReleaseFeedCandidateV1,
    ReleaseFeedError,
    ReleaseFeedVerifier,
    canonical_json_bytes,
    public_key_sha256,
    sign_release_feed,
)

NOW = datetime(2026, 8, 29, 10, tzinfo=timezone.utc)
SHA = "a" * 64
DIGEST = f"sha256:{SHA}"
IMAGE = f"registry.example/hc/component@{DIGEST}"


def _edge(**updates: object) -> AdjacentReleaseEdgeV1:
    document: dict[str, Any] = {
        "edge_id": "0.1.0-to-0.1.1",
        "source_version": "0.1.0",
        "target_version": "0.1.1",
        "release_type": "patch",
        "status": "READY_FOR_CANARY",
        "target_artifacts_built_and_signed": True,
        "database": {
            "change_mode": "no_schema_change",
            "source_migration_count": 108,
            "source_manifest_sha256": SHA,
            "target_migration_count": 108,
            "target_manifest_sha256": SHA,
            "expand_migrations": [],
            "data_migration": "none",
            "contract_migrations": [],
            "old_app_on_target_schema": "PASS",
            "new_app_on_source_schema": "PASS",
            "database_downgrade_allowed": False,
        },
        "temporal": {
            "source_patch_ids": ["patch-one-v1"],
            "target_patch_ids": ["patch-one-v1"],
            "added_patch_ids": [],
            "removed_patch_ids": [],
            "existing_history_replay": "PASS",
            "source_worker_on_target_history": "PASS",
            "target_worker_on_source_history": "PASS",
            "worker_build_id_routing": "ENABLED",
            "production_server_version_range": ">=1.25,<2",
        },
        "api": {
            "source_openapi_sha256": SHA,
            "target_openapi_sha256": SHA,
            "old_client_on_target_server": "PASS",
            "target_client_on_source_server": "PASS",
            "removed_operations": [],
            "removed_schema_fields": [],
            "generated_client_drift_check": "required",
        },
        "configuration": {
            "source_values_sha256": SHA,
            "target_values_sha256": SHA,
            "added_required_keys_without_defaults": [],
            "removed_keys": [],
            "old_app_with_target_config": "PASS",
            "target_app_with_source_config": "PASS",
            "secret_key_removal_allowed": False,
        },
        "rollback": {
            "before_contract_application": "application_digest_rollback_allowed",
            "after_contract_application": "not_applicable_no_contract_migration",
            "exact_source_artifacts_required": True,
            "database_down_migration_allowed": False,
            "temporal_source_build_must_remain_available": True,
        },
        "release_blockers": [],
    }
    document.update(updates)
    return AdjacentReleaseEdgeV1.model_validate(document)


def _manifest(**updates: object) -> PlatformReleaseManifestV1:
    document: dict[str, object] = {
        "release_id": "platform-v0.1.1",
        "semantic_version": "0.1.1",
        "git_commit": "1" * 40,
        "chart_version": "0.1.1",
        "images": {
            "frontend": IMAGE,
            "api": IMAGE,
            "worker": IMAGE,
            "media_worker": IMAGE,
        },
        "database": {
            "migration_count": 108,
            "migration_manifest_sha256": SHA,
            "expand_migrations": [],
            "contract_migrations": [],
        },
        "openapi_sha256": SHA,
        "generated_client_sha256": SHA,
        "configuration_sha256": SHA,
        "temporal": {
            "server_version_range": ">=1.25,<2",
            "task_queues": ["hc-data-pipeline", "hc-media-pipeline"],
            "source_build_id": "hc-platform-0.1.0",
            "target_build_id": "hc-platform-0.1.1",
            "patch_set_sha256": SHA,
        },
        "supply_chain": {
            "sbom_sha256": SHA,
            "provenance_sha256": SHA,
            "chart_package_sha256": SHA,
            "dr_evidence_bundle_sha256": SHA,
        },
        "minimum_source_version": "0.1.0",
        "published_at": NOW,
    }
    document.update(updates)
    return PlatformReleaseManifestV1.model_validate(document)


def _feed(*, sequence: int = 7, edge: AdjacentReleaseEdgeV1 | None = None):
    manifest = _manifest()
    candidate = ReleaseFeedCandidateV1(
        manifest_sha256=hashlib.sha256(canonical_json_bytes(manifest)).hexdigest(),
        manifest=manifest,
        compatibility=edge or _edge(),
    )
    return PlatformReleaseFeedV1(sequence=sequence, generated_at=NOW, candidates=(candidate,))


def test_signed_release_feed_verifies_pinned_key_forward_sequence_and_compatibility() -> None:
    private_key = Ed25519PrivateKey.generate()
    signed = sign_release_feed(_feed(), private_key=private_key)
    fingerprint = public_key_sha256(private_key.public_key())
    verifier = ReleaseFeedVerifier({fingerprint: private_key.public_key()})

    verified = verifier.verify(signed, minimum_sequence=7, current_version="0.1.0")

    assert verified.feed.sequence == 7
    assert verified.feed.candidates[0].manifest.release_id == "platform-v0.1.1"
    assert verified.signer_public_key_sha256 == fingerprint


def test_forged_untrusted_and_rolled_back_release_feeds_are_rejected() -> None:
    trusted = Ed25519PrivateKey.generate()
    attacker = Ed25519PrivateKey.generate()
    verifier = ReleaseFeedVerifier({public_key_sha256(trusted.public_key()): trusted.public_key()})

    with pytest.raises(ReleaseFeedError) as untrusted:
        verifier.verify(
            sign_release_feed(_feed(), private_key=attacker),
            minimum_sequence=7,
            current_version="0.1.0",
        )
    assert untrusted.value.code == "RELEASE_FEED_SIGNER_UNTRUSTED"

    signed = sign_release_feed(_feed(sequence=6), private_key=trusted)
    with pytest.raises(ReleaseFeedError) as rollback:
        verifier.verify(signed, minimum_sequence=7, current_version="0.1.0")
    assert rollback.value.code == "RELEASE_FEED_ROLLBACK_REJECTED"

    forged = signed.model_copy(update={"feed": signed.feed.model_copy(update={"sequence": 9})})
    with pytest.raises(ReleaseFeedError) as tampered:
        verifier.verify(forged, minimum_sequence=6, current_version="0.1.0")
    assert tampered.value.code == "RELEASE_FEED_DIGEST_MISMATCH"


def test_downgrade_and_incompatible_candidates_fail_closed() -> None:
    private_key = Ed25519PrivateKey.generate()
    verifier = ReleaseFeedVerifier(
        {public_key_sha256(private_key.public_key()): private_key.public_key()}
    )
    signed = sign_release_feed(_feed(), private_key=private_key)
    with pytest.raises(ReleaseFeedError) as downgrade:
        verifier.verify(signed, minimum_sequence=1, current_version="0.1.1")
    assert downgrade.value.code == "RELEASE_FEED_DOWNGRADE_REJECTED"

    blocked_edge = cast(
        AdjacentReleaseEdgeV1,
        _edge().model_copy(
            update={
                "status": "BLOCKED",
                "target_artifacts_built_and_signed": False,
                "release_blockers": ("TEMPORAL_ROUTING_MISSING",),
            }
        ),
    )
    incompatible = sign_release_feed(_feed(edge=blocked_edge), private_key=private_key)
    with pytest.raises(ReleaseFeedError) as blocked:
        verifier.verify(incompatible, minimum_sequence=1, current_version="0.1.0")
    assert blocked.value.code == "RELEASE_FEED_COMPATIBILITY_REJECTED"


def test_release_manifest_rejects_tag_images_noncanonical_identity_and_naive_time() -> None:
    with pytest.raises(ValidationError):
        _manifest(
            images={"frontend": "repo:latest", "api": IMAGE, "worker": IMAGE, "media_worker": IMAGE}
        )
    with pytest.raises(ValidationError, match="canonical semantic-version"):
        _manifest(release_id="platform-v0.1.1-alias")
    with pytest.raises(ValidationError, match="timezone-aware"):
        _manifest(published_at=datetime(2026, 8, 29, 10))
