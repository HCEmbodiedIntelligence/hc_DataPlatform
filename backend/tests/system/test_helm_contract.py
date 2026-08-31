from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
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
    assert helpers.count("secretKeyRef:") == 10
    assert 'define "hc-data-platform.backendApiSecretEnv"' in helpers
    assert "HC_DATA_SOURCE_CREDENTIAL_KEY" in helpers
    assert "HC_AUTH_ABUSE_HMAC_SECRET" in helpers
    assert "HC_AUTH_TURNSTILE_SECRET" in helpers
    assert "HC_AUTH_SMTP_PASSWORD" in helpers
    assert "HC_AUTO_ANNOTATION_PROVIDER_API_KEY" in helpers

    worker = (CHART / "templates" / "backend-worker.yaml").read_text(encoding="utf-8")
    readiness = worker.split("readinessProbe:", maxsplit=1)[1].split("livenessProbe:", maxsplit=1)[
        0
    ]
    assert "test -f /tmp/hc-runtime/worker-ready" in readiness
    assert "HC_WORKFLOW_ACTIVITY_FACTORY" not in readiness
    assert "HC_TEMPORAL_TARGET" not in readiness
    assert "socket.create_connection" not in readiness
    assert "import hc_data_platform.runtime" not in readiness
    assert "os.kill(1, 0)" not in readiness

    values = _yaml(CHART / "values.yaml")
    assert values["backend"]["api"]["image"] != values["backend"]["worker"]["image"]
    configmap = (CHART / "templates" / "backend-configmap.yaml").read_text(encoding="utf-8")
    assert "workflowActivityFactory" in values["backend"]["config"]
    assert "serviceVersion" not in values["backend"]["config"]
    assert "HC_WORKFLOW_ACTIVITY_FACTORY" in configmap
    assert "HC_RELEASE_ID" in configmap
    assert "HC_PLATFORM_ENVIRONMENT_ID" in configmap
    assert "HC_SECRET_BUNDLE_REVISION" in configmap
    assert "HC_RELEASE_MANIFEST_DIGEST" in configmap
    assert "HC_MIGRATION_MANIFEST_DIGEST" in configmap
    assert "HC_OUTBOX_SCOPES" in configmap
    assert "HC_STORAGE_INVENTORY_SCOPES" in configmap
    assert "HC_STORAGE_INVENTORY_INTERVAL_SECONDS" in configmap
    assert "HC_MEDIA_MAINTENANCE_INTERVAL_SECONDS" in configmap
    assert "HC_ALIGNED_MEDIA_PUBLICATION_ORPHAN_TTL_MINUTES" in configmap
    assert "HC_MEDIA_GLOBAL_MAX_CONCURRENT_GENERATIONS" in configmap
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
    assert values["backend"]["config"]["platformEnvironmentId"] == "hc-production"
    assert values["backend"]["config"]["alignedMediaPublicationOrphanTtlMinutes"] == "30"
    assert json.loads(values["backend"]["config"]["alignedMediaAllowedProfiles"]) == [
        "canonical-h264-crf20-v1"
    ]
    assert values["backend"]["mediaWorker"]["globalMaxConcurrentGenerations"] == 4
    assert values["backend"]["worker"]["maxConcurrentActivities"] == 2
    assert values["backend"]["mediaWorker"]["maxConcurrentActivities"] == 2
    ingress = (CHART / "templates" / "ingress.yaml").read_text(encoding="utf-8")
    assert "/api/v1/previews/" not in ingress
    for environment in ("staging", "production"):
        example = _yaml(
            REPOSITORY / "deploy" / "environments" / f"{environment}/values.example.yaml"
        )
        session_config = example["backend"]["config"]
        outbox_scopes = json.loads(session_config["outboxScopes"])
        assert outbox_scopes
        assert all(len(scope.split("/")) == 3 for scope in outbox_scopes)
        inventory_scopes = json.loads(session_config["storageInventoryScopes"])
        assert inventory_scopes == outbox_scopes
        assert session_config["storageInventoryIntervalSeconds"] == "3600"
        assert session_config["mediaMaintenanceIntervalSeconds"] == "300"
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
        assert session_config["platformEnvironmentId"] == f"hc-{environment}"
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", session_config["secretBundleRevision"])
    dev_example = _yaml(REPOSITORY / "deploy/environments/dev/values.example.yaml")
    assert dev_example["backend"]["config"]["platformEnvironmentId"] == "hc-dev"
    ci_values = _yaml(CHART / "values-ci.yaml")
    assert ci_values["backend"]["config"]["platformEnvironmentId"] == "hc-ci"
    assert re.fullmatch(
        r"sha256:[0-9a-f]{64}", ci_values["backend"]["config"]["secretBundleRevision"]
    )
    assert (
        len(
            {
                values["backend"]["config"]["platformEnvironmentId"],
                dev_example["backend"]["config"]["platformEnvironmentId"],
                ci_values["backend"]["config"]["platformEnvironmentId"],
                *(
                    _yaml(
                        REPOSITORY
                        / "deploy"
                        / "environments"
                        / f"{environment}/values.example.yaml"
                    )["backend"]["config"]["platformEnvironmentId"]
                    for environment in ("staging", "production")
                ),
            }
        )
        == 4
    )
    assert "service.version" in configmap
    assert ".Values.global.releaseId" in configmap
    assert ".Values.global.releaseManifestDigest" in configmap
    for template_name, role, values_path in (
        ("backend-api.yaml", "api", ".Values.backend.api.image.digest"),
        ("backend-worker.yaml", "worker", ".Values.backend.worker.image.digest"),
        (
            "backend-media-worker.yaml",
            "media-worker",
            ".Values.backend.mediaWorker.image.digest",
        ),
    ):
        deployment = (CHART / "templates" / template_name).read_text(encoding="utf-8")
        assert "hc-data-platform.io/release-manifest-digest" in deployment
        assert "HC_COMPONENT_ROLE" in deployment
        assert f"value: {role}" in deployment
        assert "HC_COMPONENT_IMAGE_DIGEST" in deployment
        assert values_path in deployment
        assert "HC_INSTANCE_ID" in deployment
        assert "fieldPath: metadata.uid" in deployment
        assert "HC_POD_NAME" in deployment
        assert "fieldPath: metadata.name" in deployment
        assert "HC_KUBERNETES_NODE_NAME" in deployment
        assert "fieldPath: spec.nodeName" in deployment


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


def test_whole_backup_job_is_explicit_private_and_restartable() -> None:
    values = _yaml(CHART / "values.yaml")
    job = values["backend"]["backupJob"]
    assert job["enabled"] is False
    assert job["operation"] == "create"
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", job["image"]["digest"])
    assert job["staging"]["existingClaim"] == ""
    template = (CHART / "templates/backend-backup-job.yaml").read_text(encoding="utf-8")
    assert 'command: ["hc-platform"]' in template
    assert "persistentVolumeClaim:" in template
    assert "private-staging-bootstrap" in template
    assert "automountServiceAccountToken: false" in template
    assert "readOnlyRootFilesystem: true" not in template  # centralized security context
    assert "backupContainerSecurityContext" in template
    assert "secretKeyRef:" not in template
    assert "envFrom:" in template
    assert "defaultMode: 0440" in template
    assert "age-identity.txt" in template
    assert 'work / "age-identity.txt"' not in template
    assert "backoffLimit:" in template


def test_restore_job_requires_approval_checkpoint_and_read_only_runtime() -> None:
    values = _yaml(CHART / "values.yaml")
    job = values["backend"]["restoreJob"]
    assert job["enabled"] is False
    assert job["operation"] == "execute"
    assert job["reconciledAt"] == ""
    assert job["backupId"] == ""
    assert job["targetEnvironment"] == ""
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", job["image"]["digest"])
    assert job["staging"]["existingClaim"] == ""
    template = (CHART / "templates/backend-restore-job.yaml").read_text(encoding="utf-8")
    assert 'command: ["hc-platform"]' in template
    assert "backend.restoreJob.operation must be execute or reconcile" in template
    assert "- {{ $operation }}" in template
    assert "--approval-signature" in template
    assert "--reconciled-at" in template
    assert "restore-reconciliation-report.json" in template
    assert "--checkpoint" in template
    assert "restore-checkpoint.json" in template
    assert "persistentVolumeClaim:" in template
    assert "private-restore-bootstrap" in template
    assert 'private = Path("/restore-private/runtime")' in template
    assert "automountServiceAccountToken: false" in template
    assert "serviceAccountToken:" in template
    assert "kube-root-ca.crt" in template
    assert "temporalTlsSecret" in template
    assert "HC_RESTORE_TEMPORAL_TLS_CA_FILE" in template
    assert "temporal-client.crt" in template
    assert "temporal-client.key" in template
    assert "backupContainerSecurityContext" in template
    assert "envFrom:" in template
    assert "defaultMode: 0440" in template
    assert "HC_RESTORE_EXECUTION_OWNER_ID" not in template
    assert "backoffLimit:" in template


def test_planned_migration_job_requires_signed_observation_and_private_checkpoint() -> None:
    values = _yaml(CHART / "values.yaml")
    job = values["backend"]["migrationJob"]
    assert job["enabled"] is False
    assert job["operation"] == "advance"
    assert job["objectMigrationMode"] == "reuse_external"
    assert job["backoffLimit"] == 0
    assert job["fencingToken"] == 0
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", job["image"]["digest"])
    assert job["staging"]["existingClaim"] == ""
    template = (CHART / "templates/backend-migration-job.yaml").read_text(encoding="utf-8")
    assert 'command: ["hc-platform"]' in template
    assert "backend.migrationJob.operation must be plan, advance, or status" in template
    assert "backend.migrationJob.objectMigrationMode must be reuse_external" in template
    assert "HC_MIGRATION_EXPECTED_OBJECT_MODE" in template
    assert "migration-cutover-approval.json" in template
    assert "migration-cutover-approval.sig" in template
    assert "migration-observation.json" in template
    assert "migration-observation.sig" in template
    assert "migration-checkpoint.json" in template
    assert "immutable migration input conflict" in template
    assert "automountServiceAccountToken: false" in template
    assert "defaultMode: 0440" in template
    assert "persistentVolumeClaim:" in template
    assert "secretKeyRef:" not in template
    assert "backoffLimit:" in template


def test_high_availability_requires_spread_pdb_and_bounded_graceful_drain() -> None:
    values = _yaml(CHART / "values.yaml")
    ha = values["highAvailability"]
    assert ha == {
        "enabled": False,
        "nodeTopologyKey": "kubernetes.io/hostname",
        "zoneTopologyKey": "topology.kubernetes.io/zone",
        "nodeMinDomains": 2,
        "zoneMinDomains": 2,
        "maxSkew": 1,
    }
    production = _yaml(REPOSITORY / "deploy/environments/production/values.example.yaml")
    assert production["highAvailability"]["enabled"] is True
    assert production["backend"]["mediaWorker"]["replicaCount"] == 2
    assert values["backend"]["worker"]["probes"]["readiness"]["timeoutSeconds"] == 2

    helpers = (CHART / "templates/_helpers.tpl").read_text(encoding="utf-8")
    assert 'define "hc-data-platform.haTopologySpreadConstraints"' in helpers
    assert "minDomains:" in helpers
    assert "whenUnsatisfiable: DoNotSchedule" in helpers
    assert "pod-template-hash" in helpers
    for filename, component in (
        ("frontend.yaml", "frontend"),
        ("backend-api.yaml", "api"),
        ("backend-worker.yaml", "worker"),
        ("backend-media-worker.yaml", "media-worker"),
    ):
        template = (CHART / "templates" / filename).read_text(encoding="utf-8")
        assert "haTopologySpreadConstraints" in template
        assert f'"component" "{component}"' in template

    pdb = (CHART / "templates/backend-pdb.yaml").read_text(encoding="utf-8")
    assert "replicas>=2 and 1<=minAvailable<replicas" in pdb
    assert "highAvailability requires podDisruptionBudget.enabled=true" in pdb
    assert "nodeMinDomains>=2, zoneMinDomains>=2, and maxSkew=1" in pdb
    assert "distinct non-empty node and zone topology keys" in pdb
    assert "requires a non-zero backend.config.secretBundleRevision sha256 digest" in pdb
    assert "frontend drainDelaySeconds must be positive" in pdb
    assert "drainDelaySeconds + gracefulShutdownSeconds" in pdb

    api = (CHART / "templates/backend-api.yaml").read_text(encoding="utf-8")
    assert "--timeout-graceful-shutdown" in api
    assert "drainDelaySeconds" in api
    worker = (CHART / "templates/backend-worker.yaml").read_text(encoding="utf-8")
    media = (CHART / "templates/backend-media-worker.yaml").read_text(encoding="utf-8")
    assert "HC_WORKER_GRACEFUL_SHUTDOWN_SECONDS" in worker
    assert "HC_WORKER_GRACEFUL_SHUTDOWN_SECONDS" in media
    assert "HC_WORKER_MAX_CONCURRENT_WORKFLOW_TASKS" in worker
    assert "HC_WORKER_MAX_CONCURRENT_WORKFLOW_TASKS" in media
    assert "HC_WORKER_MAX_CACHED_WORKFLOWS" in worker
    assert "HC_WORKER_MAX_CACHED_WORKFLOWS" in media
    assert values["backend"]["worker"]["maxConcurrentWorkflowTasks"] == 4
    assert values["backend"]["worker"]["maxCachedWorkflows"] == 16
    assert values["backend"]["mediaWorker"]["maxConcurrentWorkflowTasks"] == 4
    assert values["backend"]["mediaWorker"]["maxCachedWorkflows"] == 16
    assert "test -f /tmp/hc-runtime/worker-ready" in worker
    assert "test -f /tmp/hc-runtime/worker-ready" in media
    assert "HC_WORKER_READINESS_FILE" in worker
    assert "HC_WORKER_READINESS_FILE" in media
    assert "import hc_data_platform.runtime" not in worker
    assert "import hc_data_platform.runtime" not in media
    assert 'command: ["/bin/sh", "-c", "sleep 10"]' not in worker
    frontend = (CHART / "templates/frontend.yaml").read_text(encoding="utf-8")
    assert "drainDelaySeconds" in frontend
    assert "kill -QUIT 1" in frontend


def test_api_and_worker_autoscaling_is_bounded_and_production_enabled() -> None:
    values = _yaml(CHART / "values.yaml")
    production = _yaml(REPOSITORY / "deploy/environments/production/values.example.yaml")
    for component, maximum in (("api", 10), ("worker", 20)):
        autoscaling = values["backend"][component]["autoscaling"]
        assert autoscaling["enabled"] is False
        assert autoscaling["minReplicas"] == 2
        assert autoscaling["maxReplicas"] == maximum
        assert autoscaling["targetCPUUtilizationPercentage"] == 70
        assert autoscaling["behavior"]["scaleDown"]["stabilizationWindowSeconds"] == 300
        assert production["backend"][component]["autoscaling"]["enabled"] is True

        deployment = (CHART / "templates" / f"backend-{component}.yaml").read_text(encoding="utf-8")
        assert f"not .Values.backend.{component}.autoscaling.enabled" in deployment

    template = (CHART / "templates/backend-hpa.yaml").read_text(encoding="utf-8")
    assert "apiVersion: autoscaling/v2" in template
    assert "minReplicas>=2 and maxReplicas>minReplicas" in template
    assert "requires resources.requests.cpu" in template
    assert "averageUtilization:" in template
    assert "behavior:" in template
    pdb = (CHART / "templates/backend-pdb.yaml").read_text(encoding="utf-8")
    assert ".Values.backend.api.autoscaling.minReplicas" in pdb
    assert ".Values.backend.worker.autoscaling.minReplicas" in pdb


def test_capacity_gate_is_external_digest_bound_and_fail_closed() -> None:
    values = _yaml(CHART / "values.yaml")
    gate = values["backend"]["capacityGate"]
    assert gate["enabled"] is False
    assert gate["backoffLimit"] == 0
    assert gate["evidenceConfigMap"]["name"] == ""
    assert gate["evidenceConfigMap"]["sha256"] == ""
    production = _yaml(REPOSITORY / "deploy/environments/production/values.example.yaml")
    production_gate = production["backend"]["capacityGate"]
    assert production_gate["enabled"] is True
    assert production_gate["evidenceConfigMap"]["name"].startswith("replace-with-")
    assert production_gate["evidenceConfigMap"]["sha256"] == f"sha256:{'0' * 64}"

    template = (CHART / "templates/backend-capacity-gate-job.yaml").read_text(encoding="utf-8")
    assert '"helm.sh/hook": pre-install,pre-upgrade' in template
    assert '"helm.sh/hook-weight": "-20"' in template
    assert '"helm.sh/hook-delete-policy": before-hook-creation' in template
    assert "hc_data_platform.core.capacity_gate" in template
    assert "--expected-sha256" in template
    assert "exact lowercase sha256 digest" in template
    assert "configMap:" in template
    assert "automountServiceAccountToken: false" in template
    assert "readOnly: true" in template
    assert 'backendImage" .Values.backend.api.image' in template
    assert "secretKeyRef:" not in template


def test_helm_renders_autoscalers_and_rejects_invalid_capacity_inputs() -> None:
    helm = shutil.which("helm")
    if helm is None:
        pytest.skip("helm is required for rendered autoscaling contract")
    common = [
        helm,
        "template",
        "contract",
        str(CHART),
        "-f",
        str(CHART / "values-ci.yaml"),
    ]
    rendered = subprocess.run(
        [
            *common,
            "--set",
            "backend.api.autoscaling.enabled=true",
            "--set",
            "backend.worker.autoscaling.enabled=true",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    documents = [item for item in yaml.safe_load_all(rendered.stdout) if item]
    hpas = [item for item in documents if item["kind"] == "HorizontalPodAutoscaler"]
    assert {item["metadata"]["labels"]["app.kubernetes.io/component"] for item in hpas} == {
        "api",
        "worker",
    }
    deployments = {
        item["metadata"]["labels"].get("app.kubernetes.io/component"): item
        for item in documents
        if item["kind"] == "Deployment"
    }
    assert "replicas" not in deployments["api"]["spec"]
    assert "replicas" not in deployments["worker"]["spec"]
    expected_empty_dirs = {
        "frontend": {"tmp": "64Mi"},
        "api": {"tmp": "256Mi"},
        "worker": {"tmp": "20Gi", "runtime": "16Mi"},
        "media-worker": {"staging": "20Gi", "runtime": "16Mi"},
    }
    for component, expected in expected_empty_dirs.items():
        volumes = {
            volume["name"]: volume["emptyDir"]["sizeLimit"]
            for volume in deployments[component]["spec"]["template"]["spec"]["volumes"]
            if "emptyDir" in volume
        }
        assert volumes == expected

    invalid_hpa = subprocess.run(
        [
            *common,
            "--set",
            "backend.api.autoscaling.enabled=true",
            "--set",
            "backend.api.autoscaling.minReplicas=1",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert invalid_hpa.returncode != 0
    assert "minReplicas>=2" in invalid_hpa.stderr

    invalid_staging = subprocess.run(
        [
            *common,
            "--set-string",
            "backend.worker.stagingSizeLimit=",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert invalid_staging.returncode != 0
    assert "backend.worker.stagingSizeLimit is required" in invalid_staging.stderr

    invalid_gate = subprocess.run(
        [
            *common,
            "--set",
            "backend.capacityGate.enabled=true",
            "--set",
            "backend.capacityGate.evidenceConfigMap.name=capacity-evidence",
            "--set",
            "backend.capacityGate.evidenceConfigMap.sha256=invalid",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert invalid_gate.returncode != 0
    assert "exact lowercase sha256 digest" in invalid_gate.stderr


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


def test_release_identity_sidecar_is_same_pod_minimum_privilege() -> None:
    values = _yaml(CHART / "values.yaml")
    assert values["frontend"]["identitySidecar"]["resources"]["limits"] == {
        "cpu": "100m",
        "memory": "96Mi",
    }
    template = (CHART / "templates/frontend.yaml").read_text(encoding="utf-8")
    sidecar = template.split("- name: release-identity-heartbeat", maxsplit=1)[1]
    assert 'command: ["hc-frontend-heartbeat"]' in sidecar
    assert "HC_FRONTEND_IMAGE_DIGEST" in sidecar
    assert "http://127.0.0.1:8080/healthz" in sidecar
    assert "fieldPath: metadata.uid" in sidecar
    assert ".Values.frontend.identitySidecar.resources" in sidecar
    assert sidecar.count("secretKeyRef:") == 1
    assert "automountServiceAccountToken: false" in template


def test_expand_and_contract_migration_jobs_are_separate_fail_closed_gates() -> None:
    values = _yaml(CHART / "values.yaml")
    migration = values["backend"]["migration"]
    assert migration["enabled"] is True
    assert migration["contract"] == {"enabled": False, "approvalDigest": ""}

    template = (CHART / "templates/backend-schema-migrations.yaml").read_text(encoding="utf-8")
    expand, contract = template.split("---", maxsplit=1)
    assert '"helm.sh/hook": pre-install,pre-upgrade' in expand
    assert 'args: ["upgrade-expand"]' in expand
    assert "pg_advisory" not in template  # locking is inside the migration executable
    assert "args:\n            - upgrade-contract" in contract
    assert "--approval-digest" in contract
    assert "hc-data-platform.io/migration-phase: contract" in contract
    assert "helm.sh/hook" not in contract
    assert contract.count("secretKeyRef:") == 1
    assert template.count("automountServiceAccountToken: false") == 2


def test_helm_rejects_unapproved_contract_and_renders_approved_job() -> None:
    helm = shutil.which("helm")
    if helm is None:
        pytest.skip("helm is required for rendered migration contract")
    common = [helm, "template", "contract", str(CHART), "-f", str(CHART / "values-ci.yaml")]
    rejected = subprocess.run(
        [*common, "--set", "backend.migration.contract.enabled=true"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode != 0
    assert "backend.migration.contract.approvalDigest is required" in rejected.stderr

    approved = subprocess.run(
        [
            *common,
            "--set",
            "backend.migration.contract.enabled=true",
            "--set-string",
            f"backend.migration.contract.approvalDigest=sha256:{'a' * 64}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    documents = [item for item in yaml.safe_load_all(approved.stdout) if item]
    migration_jobs = [
        item
        for item in documents
        if item["kind"] == "Job"
        and item["metadata"]["labels"].get("app.kubernetes.io/component")
        in {"schema-migration", "schema-contract"}
    ]
    assert {
        item["metadata"]["labels"]["app.kubernetes.io/component"] for item in migration_jobs
    } == {
        "schema-migration",
        "schema-contract",
    }
