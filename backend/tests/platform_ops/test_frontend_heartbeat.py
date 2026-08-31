from __future__ import annotations

import pytest

from hc_data_platform.platform_ops.frontend_heartbeat import frontend_identity_from_environment

DIGEST = f"sha256:{'a' * 64}"


def _environment() -> dict[str, str]:
    return {
        "HC_INSTANCE_ID": "11111111-1111-4111-8111-111111111111",
        "HC_NODE_NAME": "frontend-pod-1",
        "HC_POD_NAME": "frontend-pod-1",
        "HC_KUBERNETES_NODE_NAME": "worker-node-a",
        "HC_RELEASE_ID": "platform-v0.1.0",
        "HC_RELEASE_MANIFEST_DIGEST": DIGEST,
        "HC_FRONTEND_IMAGE_DIGEST": DIGEST,
        "HC_PLATFORM_VERSION": "0.1.0",
    }


def test_frontend_sidecar_registers_the_ui_digest_on_the_same_pod_identity() -> None:
    identity = frontend_identity_from_environment(_environment())
    assert identity.role == "frontend"
    assert identity.node_name == identity.pod_name == "frontend-pod-1"
    assert identity.release_manifest_digest == identity.component_image_digest == DIGEST


def test_frontend_sidecar_fails_closed_on_missing_or_unsafe_identity() -> None:
    incomplete = _environment()
    del incomplete["HC_FRONTEND_IMAGE_DIGEST"]
    with pytest.raises(RuntimeError, match="incomplete"):
        frontend_identity_from_environment(incomplete)
    unsafe = _environment()
    unsafe["HC_RELEASE_MANIFEST_DIGEST"] = "unreleased"
    with pytest.raises(RuntimeError, match="unreleased"):
        frontend_identity_from_environment(unsafe)
