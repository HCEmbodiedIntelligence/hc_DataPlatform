from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

import yaml

BACKEND = Path(__file__).parents[2]
REPOSITORY = BACKEND.parent
CHART = REPOSITORY / "deploy" / "helm" / "hc-data-platform"


def _yaml(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(path.read_text(encoding="utf-8")))


def test_values_contain_secret_references_but_no_secret_material() -> None:
    text = (CHART / "values.yaml").read_text(encoding="utf-8")
    values = yaml.safe_load(text)
    assert set(values["backend"]["existingSecrets"]) == {
        "postgres",
        "objectStore",
        "application",
    }
    forbidden_keys = {"password", "token", "secret", "secretKey", "cursorSecret"}

    def walk(value: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in forbidden_keys and not key.endswith("Key"):
                    raise AssertionError(f"inline secret-like value at {'.'.join((*path, key))}")
                walk(item, (*path, key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, (*path, str(index)))

    walk(values)
    assert "valueFrom:" in (CHART / "templates" / "_helpers.tpl").read_text(encoding="utf-8")


def test_api_and_worker_have_probes_resources_and_rolling_updates() -> None:
    for name in ("backend-api.yaml", "backend-worker.yaml"):
        template = (CHART / "templates" / name).read_text(encoding="utf-8")
        assert "type: RollingUpdate" in template
        assert "startupProbe:" in template
        assert "readinessProbe:" in template
        assert "livenessProbe:" in template
        assert "resources:" in template
        assert "securityContext:" in template
        assert 'command: ["opentelemetry-instrument"]' in template
        assert "secretKeyRef:" not in template  # centralized, identical secret contract
    assert "hc-data-backend-api" in (CHART / "templates" / "backend-api.yaml").read_text(
        encoding="utf-8"
    )
    assert "hc-data-backend-worker" in (CHART / "templates" / "backend-worker.yaml").read_text(
        encoding="utf-8"
    )
    assert 'backendImage" .Values.backend.api.image' in (
        CHART / "templates" / "backend-api.yaml"
    ).read_text(encoding="utf-8")
    assert 'backendImage" .Values.backend.worker.image' in (
        CHART / "templates" / "backend-worker.yaml"
    ).read_text(encoding="utf-8")
    helpers = (CHART / "templates" / "_helpers.tpl").read_text(encoding="utf-8")
    assert helpers.count("secretKeyRef:") == 9
    assert "HC_DATA_SOURCE_CREDENTIAL_KEY" in helpers
    assert "HC_AUTH_ABUSE_HMAC_SECRET" in helpers
    assert "HC_AUTH_TURNSTILE_SECRET" in helpers
    assert "HC_AUTH_SMTP_PASSWORD" in helpers
    assert "HC_AUTO_ANNOTATION_PROVIDER_API_KEY" in helpers

    worker = (CHART / "templates" / "backend-worker.yaml").read_text(encoding="utf-8")
    readiness = worker.split("readinessProbe:", maxsplit=1)[1].split("livenessProbe:", maxsplit=1)[
        0
    ]
    assert "HC_WORKFLOW_ACTIVITY_FACTORY" in readiness
    assert "HC_TEMPORAL_TARGET" in readiness
    assert "socket.create_connection" in readiness
    assert "os.kill(1, 0)" not in readiness

    values = _yaml(CHART / "values.yaml")
    assert values["backend"]["api"]["image"] != values["backend"]["worker"]["image"]
    configmap = (CHART / "templates" / "backend-configmap.yaml").read_text(encoding="utf-8")
    assert "workflowActivityFactory" in values["backend"]["config"]
    assert "serviceVersion" in values["backend"]["config"]
    assert "HC_WORKFLOW_ACTIVITY_FACTORY" in configmap
    assert "HC_OUTBOX_SCOPES" in configmap
    assert "HC_ARTIFACT_PREFIX" in configmap
    assert "HC_AUTO_ANNOTATION_PROVIDER_ENDPOINT" in configmap
    assert "HC_AUTO_ANNOTATION_PROVIDER_MODELS" in configmap
    assert "HC_PASSWORD_MIN_LENGTH" in configmap
    assert "HC_SESSION_IDLE_TTL_SECONDS" in configmap
    assert "HC_SESSION_ABSOLUTE_TTL_SECONDS" in configmap
    assert "HC_SESSION_TOUCH_INTERVAL_SECONDS" in configmap
    assert "HC_MAX_ACTIVE_SESSIONS" in configmap
    assert "HC_AUTH_ABUSE_ENABLED" in configmap
    assert "HC_AUTH_TRUSTED_PROXY_CIDRS" in configmap
    assert "HC_AUTH_CHALLENGE_PROVIDER" in configmap
    assert "HC_AUTH_TURNSTILE_SITE_KEY" in configmap
    assert "HC_AUTH_TURNSTILE_EXPECTED_HOSTNAMES" in configmap
    assert "HC_AUTH_RECOVERY_ENABLED" in configmap
    assert "HC_AUTH_RECOVERY_PUBLIC_BASE_URL" in configmap
    assert "HC_AUTH_SMTP_HOST" in configmap
    assert values["backend"]["config"]["maxActiveSessions"] == "5"
    media_rate = values["ingress"]["previewMediaRateLimit"]
    assert media_rate == {
        "enabled": True,
        "requestsPerSecond": "30",
        "burstMultiplier": "4",
        "connections": "8",
    }
    ingress = (CHART / "templates" / "ingress.yaml").read_text(encoding="utf-8")
    assert "-preview-media" in ingress
    assert "nginx.ingress.kubernetes.io/limit-rps" in ingress
    assert "nginx.ingress.kubernetes.io/limit-burst-multiplier" in ingress
    assert "nginx.ingress.kubernetes.io/limit-connections" in ingress
    assert "path: /api/v1/previews/sessions/" in ingress
    for environment in ("staging", "production"):
        example = _yaml(
            REPOSITORY / "deploy" / "environments" / f"{environment}/values.example.yaml"
        )
        session_config = example["backend"]["config"]
        outbox_scopes = json.loads(session_config["outboxScopes"])
        assert outbox_scopes
        assert all(len(scope.split("/")) == 3 for scope in outbox_scopes)
        assert session_config["sessionIdleTtlSeconds"] == "1800"
        assert session_config["sessionAbsoluteTtlSeconds"] == "86400"
        assert session_config["sessionTouchIntervalSeconds"] == "60"
        assert session_config["maxActiveSessions"] == "5"
        assert session_config["authAbuseEnabled"] == "true"
        assert session_config["authClientIpMode"] == "trusted_proxy"
        assert session_config["authTrustedProxyCidrs"]
        assert session_config["autoAnnotationProviderEndpoint"].startswith("https://")
        assert json.loads(session_config["autoAnnotationProviderModels"])
        assert session_config["authChallengeProvider"] == "turnstile"
        assert session_config["authTurnstileSiteKey"]
        assert session_config["authTurnstileExpectedHostnames"]
        assert session_config["authRecoveryEnabled"] == "true"
        assert session_config["authRecoveryPublicBaseUrl"].startswith("https://")
        assert session_config["authRecoveryEmailFrom"]
        assert session_config["authSmtpHost"]
        assert session_config["authSmtpUsername"]
    assert "service.version" in configmap


def test_validation_values_use_immutable_images_and_external_secrets() -> None:
    validation = _yaml(CHART / "values-ci.yaml")
    for component in ("api", "worker"):
        image = validation["backend"][component]["image"]
        assert "tag" not in image
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", image["digest"])
    serialized = yaml.safe_dump(validation)
    assert not re.search(r"(?i)(password|access-token):", serialized)
    defaults = _yaml(CHART / "values.yaml")
    assert all(item["name"] for item in defaults["backend"]["existingSecrets"].values())


def test_upgrade_rollback_script_is_bounded_and_non_destructive() -> None:
    script = (REPOSITORY / "deploy" / "scripts" / "exercise-upgrade-rollback.sh").read_text(
        encoding="utf-8"
    )
    assert "upgrade --install" in script
    assert "rollback" in script
    assert "rollout status" in script
    assert "existing healthy pilot release is required" in script
    assert "rm -rf" not in script
    assert not re.search(r"kubectl\s+delete", script)
