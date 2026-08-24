"""Real-network adapter for the BE22 main-chain orchestrator.

The adapter uses only public HTTP endpoints.  It issues project-scoped JWTs only
inside an explicit ``HC_ENVIRONMENT=test`` profile so no production authentication
bypass or seed account is introduced.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx
import jwt

from .fixture import RunScope, render_manifest, scenario
from .orchestrator import StageNotRunnable, StageResult


class ApiStageError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SafeResponse:
    status_code: int
    body: Any
    request_id: str
    etag: str | None


@dataclass(frozen=True, slots=True)
class Wave2TestJwtIssuer:
    issuer: str
    audience: str
    signing_key: str

    @classmethod
    def from_environment(cls) -> Wave2TestJwtIssuer:
        if os.environ.get("HC_ENVIRONMENT") != "test":
            raise RuntimeError("BE22 test JWT issuance requires HC_ENVIRONMENT=test")
        key = os.environ.get("HC_WAVE2_JWT_SIGNING_KEY", "")
        if len(key) < 32:
            raise RuntimeError("HC_WAVE2_JWT_SIGNING_KEY must contain at least 32 characters")
        return cls(
            issuer=os.environ.get("HC_JWT_ISSUER", "https://be22.test.invalid/"),
            audience=os.environ.get("HC_JWT_AUDIENCE", "hc-data-platform"),
            signing_key=key,
        )

    def issue(
        self,
        *,
        subject: str,
        scope: RunScope,
        capabilities: tuple[str, ...],
    ) -> str:
        now = datetime.now(timezone.utc)
        return jwt.encode(
            {
                "iss": self.issuer,
                "aud": self.audience,
                "sub": subject,
                "iat": now,
                "exp": now + timedelta(minutes=30),
                "roles": [],
                "project_ids": [scope.project_id],
                "region_codes": [scope.region_code],
                "capabilities": [],
                "organization_scopes": [
                    {
                        "organization_id": scope.organization_id,
                        "project_id": scope.project_id,
                        "region_code": None,
                        "capabilities": list(capabilities),
                    }
                ],
                "capability_revision": 0,
                "service_identity": False,
            },
            self.signing_key,
            algorithm="HS256",
        )


class HttpMainChainAdapter:
    """Execute all currently public stages and diagnose missing Worker/task hooks."""

    def __init__(
        self,
        base_url: str,
        *,
        issuer: Wave2TestJwtIssuer,
        timeout_seconds: float = 30,
        worker_timeout_seconds: float = 90,
    ) -> None:
        if os.environ.get("HC_ENVIRONMENT") != "test":
            raise RuntimeError("the BE22 network adapter is restricted to HC_ENVIRONMENT=test")
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_seconds)
        self._issuer = issuer
        self._worker_timeout = worker_timeout_seconds
        self._passwords: dict[str, str] = {}
        self._sessions: dict[str, str] = {}
        self._principals: dict[str, str] = {}
        self._bearers: dict[str, str] = {}
        self._resources: dict[str, str] = {}

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _authorization(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _request(
        self,
        method: str,
        path: str,
        *,
        expected: set[int],
        token: str | None = None,
        headers: dict[str, str] | None = None,
        json_body: object | None = None,
        content: bytes | None = None,
    ) -> SafeResponse:
        request_headers = dict(headers or {})
        if token is not None:
            request_headers.update(self._authorization(token))
        response = self._client.request(
            method,
            path,
            headers=request_headers,
            json=json_body,
            content=content,
        )
        request_id = response.headers.get("X-Request-ID", "")
        if response.status_code not in expected:
            code = "UNKNOWN"
            try:
                error_body = response.json()
                if isinstance(error_body, dict):
                    code = str(error_body.get("code", code))
            except ValueError:
                pass
            raise ApiStageError(
                f"{method} {path} returned {response.status_code} ({code}), request_id={request_id}"
            )
        body: Any = None
        if response.content:
            try:
                body = response.json()
            except ValueError:
                body = None
        return SafeResponse(response.status_code, body, request_id, response.headers.get("ETag"))

    def register_empty_accounts(self, scope: RunScope) -> StageResult:
        request_ids: list[str] = []
        resources: dict[str, str] = {}
        for identity, username in (
            ("admin", scope.admin_username),
            ("contractor", scope.contractor_username),
        ):
            password = secrets.token_urlsafe(24)
            registered = self._request(
                "POST",
                "/api/v1/auth/registrations",
                expected={201},
                json_body={"username": username, "password": password},
            )
            principal_id = str(registered.body["principal"]["principal_id"])
            logged_in = self._request(
                "POST",
                "/api/v1/auth/sessions",
                expected={201},
                json_body={"username": username, "password": password},
            )
            session_token = str(logged_in.body["access_token"])
            bootstrap = self._request(
                "GET",
                "/api/v1/auth/session/bootstrap",
                expected={200},
                token=session_token,
            )
            if bootstrap.body["available_scopes"] != []:
                raise ApiStageError(f"{identity} registration did not produce an empty account")
            self._passwords[identity] = password
            self._sessions[identity] = session_token
            self._principals[identity] = principal_id
            resources[f"{identity}_principal_id"] = principal_id
            request_ids.extend(
                item
                for item in (registered.request_id, logged_in.request_id, bootstrap.request_id)
                if item
            )
        self._bearers["access_admin"] = self._issuer.issue(
            subject=self._principals["admin"],
            scope=scope,
            capabilities=("project.access.manage",),
        )
        return StageResult(resource_ids=resources, request_ids=tuple(request_ids))

    def approve_membership_and_capabilities(self, scope: RunScope) -> StageResult:
        request_ids: list[str] = []
        resources: dict[str, str] = {}
        access_admin = self._bearers["access_admin"]
        for identity in ("admin", "contractor"):
            requested = self._request(
                "POST",
                f"/api/v1/organizations/{scope.organization_id}/projects/"
                f"{scope.project_id}/membership-requests",
                expected={201},
                token=self._sessions[identity],
                headers={"Idempotency-Key": f"{scope.run_id}-{identity}-membership"},
                json_body={"reason": "BE22 isolated real-API test"},
            )
            request_id = str(requested.body["request_id"])
            approved = self._request(
                "POST",
                f"/api/v1/organizations/{scope.organization_id}/projects/"
                f"{scope.project_id}/membership-requests/{request_id}:approve",
                expected={200},
                token=access_admin,
                headers={"Idempotency-Key": f"{scope.run_id}-{identity}-membership-approve"},
                json_body={"reason": "BE22 test profile approval"},
            )
            if approved.body["status"] != "APPROVED":
                raise ApiStageError("membership request was not approved")
            resources[f"{identity}_membership_request_id"] = request_id
            request_ids.extend(item for item in (requested.request_id, approved.request_id) if item)

        requested_capabilities = {
            "contractor": ["collection.upload", "annotation.write"],
            "admin": ["project.access.manage", "tag_schema.write", "annotation.review"],
        }
        for identity, capability_keys in requested_capabilities.items():
            capability = self._request(
                "POST",
                f"/api/v1/organizations/{scope.organization_id}/projects/"
                f"{scope.project_id}/capability-requests",
                expected={201},
                token=self._sessions[identity],
                headers={"Idempotency-Key": f"{scope.run_id}-{identity}-capabilities"},
                json_body={
                    "capability_keys": capability_keys,
                    "reason": "minimum approved capabilities for BE22 main chain",
                },
            )
            capability_id = str(capability.body["request_id"])
            approved_capability = self._request(
                "POST",
                f"/api/v1/organizations/{scope.organization_id}/projects/"
                f"{scope.project_id}/capability-requests/{capability_id}:approve",
                expected={200},
                token=access_admin,
                headers={"Idempotency-Key": f"{scope.run_id}-{identity}-capabilities-approve"},
                json_body={"reason": "BE22 test profile approval"},
            )
            if approved_capability.body["status"] != "APPROVED":
                raise ApiStageError("capability request was not approved")
            resources[f"{identity}_capability_request_id"] = capability_id
            request_ids.extend(
                item for item in (capability.request_id, approved_capability.request_id) if item
            )
        # Domain calls use opaque platform sessions. Their capabilities are recomputed
        # from AccessService facts on every request; the bootstrap JWT never reaches them.
        self._bearers["contractor"] = self._sessions["contractor"]
        self._bearers["admin"] = self._sessions["admin"]
        audit = self._request(
            "GET",
            f"/api/v1/projects/{scope.project_id}/access-audit-events",
            expected={200},
            token=self._bearers["admin"],
        )
        events = audit.body.get("items", []) if isinstance(audit.body, dict) else []
        audit_ids = tuple(str(item["event_id"]) for item in events if "event_id" in item)
        if audit.request_id:
            request_ids.append(audit.request_id)
        return StageResult(
            resource_ids=resources,
            request_ids=tuple(request_ids),
            audit_event_ids=audit_ids,
            detail="Domain calls use AccessService-approved project capabilities.",
        )

    def assert_cross_project_denied(self, scope: RunScope) -> StageResult:
        denied = self._request(
            "GET",
            f"/api/v1/projects/{scope.foreign_project_id}/collection-tasks",
            expected={403},
            token=self._bearers["contractor"],
        )
        return StageResult(request_ids=(denied.request_id,) if denied.request_id else ())

    def create_collection_task(self, scope: RunScope) -> StageResult:
        path = f"/api/v1/projects/{scope.project_id}/collection-tasks"
        body = {
            "name": f"BE22 isolated {scope.run_id}",
            "type": "COLLECTION",
            "scenario": "be22-real-api",
            "description": "Deterministic Wave 2 real-API package chain.",
            "target": {"package_count": 1},
            "quality_threshold": None,
        }
        headers = {"Idempotency-Key": f"{scope.run_id}-collection-task"}
        created = self._request(
            "POST",
            path,
            expected={201},
            token=self._bearers["contractor"],
            headers=headers,
            json_body=body,
        )
        replay = self._request(
            "POST",
            path,
            expected={201},
            token=self._bearers["contractor"],
            headers=headers,
            json_body=body,
        )
        if created.body != replay.body:
            raise ApiStageError("collection-task idempotency replay changed the resource")
        task_id = str(created.body["collection_task_id"])
        self._resources["collection_task_id"] = task_id
        self._resources["collection_task_etag"] = replay.etag or created.etag or ""
        return StageResult(
            resource_ids={"collection_task_id": task_id},
            request_ids=tuple(item for item in (created.request_id, replay.request_id) if item),
        )

    def upload_and_discover_manifest(self, scope: RunScope) -> StageResult:
        task_id = self._resources["collection_task_id"]
        token = self._bearers["contractor"]
        request_ids: list[str] = []
        fingerprints: dict[str, str] = {}
        manifests: dict[str, dict[str, Any]] = {}
        for sequence, name in enumerate(
            ("legal", "multi_camera", "missing_frame", "missing_topic"), 1
        ):
            package = scenario(name)
            manifest = render_manifest(
                package,
                scope,
                collection_task_id=task_id,
                sequence_no=sequence,
            )
            manifests[name] = manifest
            checked = self._request(
                "POST",
                f"/api/v1/projects/{scope.project_id}/regions/{scope.region_code}/upload-manifests:preflight",
                expected={200},
                token=token,
                content=json.dumps(manifest).encode(),
                headers={"Content-Type": "application/json"},
            )
            fingerprints[f"{name}_manifest_fingerprint"] = str(checked.body["manifest_fingerprint"])
            if checked.request_id:
                request_ids.append(checked.request_id)

        dataset_id = os.environ.get("HC_WAVE2_DATASET_ID", f"be22-{scope.run_id}-dataset")
        dataset_schema_snapshot_id = os.environ.get(
            "HC_WAVE2_DATASET_SCHEMA_SNAPSHOT_ID", "be22-dataset-schema-v1"
        )
        schema_id = f"{scope.run_id}-tag-schema"
        schema_body = {
            "schema_id": schema_id,
            "version": 1,
            "name": "BE22 three-level operation tags",
            "document": {
                "nodes": [
                    {
                        "tag_id": "operation-stage",
                        "code": "operation-stage",
                        "display_name": "Operation stage",
                    },
                    {
                        "tag_id": "grab-action",
                        "code": "grab-action",
                        "display_name": "Grab action",
                        "parent_tag_id": "operation-stage",
                    },
                    {
                        "tag_id": "grab-success",
                        "code": "grab-success",
                        "display_name": "Grab succeeded",
                        "parent_tag_id": "grab-action",
                    },
                ],
                "mutual_exclusions": [],
                "object_relations": [],
            },
            "compatible_targets": [
                {
                    "region_code": scope.region_code,
                    "dataset_id": dataset_id,
                    "dataset_schema_snapshot_id": dataset_schema_snapshot_id,
                    "task_kind": "TAGGING",
                }
            ],
        }
        created_schema = self._request(
            "POST",
            f"/api/v1/projects/{scope.project_id}/tag-schemas",
            expected={201},
            token=self._bearers["admin"],
            json_body=schema_body,
        )
        published_schema = self._request(
            "POST",
            f"/api/v1/projects/{scope.project_id}/tag-schemas/{schema_id}/versions/1/publish",
            expected={200},
            token=self._bearers["admin"],
        )
        request_ids.extend(
            item for item in (created_schema.request_id, published_schema.request_id) if item
        )
        quality_profile_id = f"{scope.run_id}-manifest-30hz"
        quality_profile = self._request(
            "POST",
            f"/api/v1/projects/{scope.project_id}/quality-profiles",
            expected={201},
            token=self._bearers["admin"],
            json_body={
                "profile_id": quality_profile_id,
                "profile_version": 1,
                "required_topics": manifests["legal"]["expected_topics"],
                "default_timing": {"target_frequency_hz": 30},
                # The checked-in MCAP fixture intentionally contains opaque CDR
                # bytes. This profile verifies transport/timing without claiming
                # that those bytes are production-decoded action observations.
                "action": {
                    "minimum_observation_count_risk": 0,
                    "minimum_observation_count_reject": 0,
                },
            },
        )
        if quality_profile.request_id:
            request_ids.append(quality_profile.request_id)
        self._resources.update(
            {
                "dataset_id": dataset_id,
                "dataset_schema_snapshot_id": dataset_schema_snapshot_id,
                "tag_schema_id": schema_id,
                "quality_profile_id": quality_profile_id,
            }
        )

        bad = render_manifest(
            scenario("bad_manifest"),
            scope,
            collection_task_id=task_id,
            sequence_no=5,
        )
        rejected = self._request(
            "POST",
            f"/api/v1/projects/{scope.project_id}/regions/{scope.region_code}/upload-manifests:preflight",
            expected={422},
            token=token,
            content=json.dumps(bad).encode(),
            headers={"Content-Type": "application/json"},
        )
        if rejected.request_id:
            request_ids.append(rejected.request_id)

        legal = scenario("legal")
        create_path = (
            f"/api/v1/projects/{scope.project_id}/regions/{scope.region_code}/upload-sessions"
        )
        create_body = {"manifest": manifests["legal"], "part_numbers": [1]}
        create_headers = {"Idempotency-Key": f"{scope.run_id}-legal-upload"}
        created = self._request(
            "POST",
            create_path,
            expected={201},
            token=token,
            headers=create_headers,
            json_body=create_body,
        )
        replay = self._request(
            "POST",
            create_path,
            expected={201},
            token=token,
            headers=create_headers,
            json_body=create_body,
        )
        if created.body["session"]["session_id"] != replay.body["session"]["session_id"]:
            raise ApiStageError("duplicate package created a second upload session")
        session_id = str(created.body["session"]["session_id"])
        signed_url = str(created.body["parts"][0]["url"])
        uploaded = httpx.put(signed_url, content=legal.payload, timeout=30)
        if uploaded.status_code not in {200, 201, 204}:
            raise ApiStageError(f"signed part upload returned {uploaded.status_code}")
        etag = uploaded.headers.get("ETag", "").strip('"')
        if not etag:
            raise ApiStageError("object store did not return an ETag for uploaded part")
        completed = self._request(
            "POST",
            f"{create_path}/{session_id}:complete",
            expected={200},
            token=token,
            json_body={"parts": [{"part_number": 1, "etag": etag}]},
        )
        committed = self._request(
            "POST",
            f"{create_path}/{session_id}:commit-manifest",
            expected={200},
            token=token,
            content=json.dumps(manifests["legal"]).encode(),
            headers={"Content-Type": "application/json"},
        )
        commit_replay = self._request(
            "POST",
            f"{create_path}/{session_id}:commit-manifest",
            expected={200},
            token=token,
            content=json.dumps(manifests["legal"]).encode(),
            headers={"Content-Type": "application/json"},
        )
        if committed.body != commit_replay.body:
            raise ApiStageError("Manifest commit replay changed the committed resource")
        workflow_locator = committed.body.get("workflow")
        if isinstance(workflow_locator, dict):
            self._resources["ingest_workflow_id"] = str(workflow_locator["workflow_id"])
            self._resources["ingest_outbox_event_id"] = str(workflow_locator["event_id"])
            self._resources["ingest_trigger_status"] = str(workflow_locator["status"])
        discovery = self._request(
            "GET",
            f"{create_path}/{session_id}/manifest",
            expected={200},
            token=token,
        )
        self._resources.update(
            {
                "upload_session_id": session_id,
                "rollout_id": str(manifests["legal"]["rollout_id"]),
                "data_package_id": str(manifests["legal"]["data_package_id"]),
            }
        )
        request_ids.extend(
            item
            for item in (
                created.request_id,
                replay.request_id,
                completed.request_id,
                committed.request_id,
                commit_replay.request_id,
                discovery.request_id,
            )
            if item
        )
        return StageResult(
            resource_ids={
                "upload_session_id": session_id,
                "rollout_id": self._resources["rollout_id"],
                "data_package_id": self._resources["data_package_id"],
                **{
                    key: self._resources[key]
                    for key in (
                        "ingest_workflow_id",
                        "ingest_outbox_event_id",
                        "ingest_trigger_status",
                    )
                    if key in self._resources
                },
                **fingerprints,
            },
            request_ids=tuple(request_ids),
        )

    def wait_for_worker_qc(self, scope: RunScope) -> StageResult:
        workflow_id = self._resources.get("ingest_workflow_id")
        if workflow_id is None:
            raise StageNotRunnable(
                "Manifest commit did not expose a persisted workflow locator; BE24 runtime "
                "wiring and generated OpenAPI aggregation are still required."
            )
        token = self._bearers["contractor"]
        deadline = time.monotonic() + self._worker_timeout
        job: dict[str, Any] | None = None
        request_ids: list[str] = []
        encoded_workflow_id = quote(workflow_id, safe="")
        while time.monotonic() < deadline:
            fetched = self._request(
                "GET",
                f"/api/v1/jobs/{encoded_workflow_id}",
                expected={200, 404},
                token=token,
            )
            if fetched.request_id:
                request_ids.append(fetched.request_id)
            if fetched.status_code == 404:
                time.sleep(1)
                continue
            job = fetched.body if isinstance(fetched.body, dict) else None
            if job is None:
                time.sleep(1)
                continue
            if job.get("status") in {
                "SUCCEEDED",
                "TECHNICAL_FAILED",
                "QUALITY_RISK",
                "QUALITY_REJECTED",
                "CANCELLED",
            }:
                break
            time.sleep(1)
        if job is None:
            raise StageNotRunnable(
                "The persisted ingest trigger did not become a Temporal job before the timeout; "
                "BE24 must wire the outbox dispatcher and persisted input resolver."
            )
        if job.get("status") != "SUCCEEDED":
            raise ApiStageError(
                f"Worker job {job.get('job_id')} ended as {job.get('status')} "
                f"at stage {job.get('stage')}"
            )
        self._resources["worker_job_id"] = str(job["job_id"])
        result = job.get("result") or {}
        dataset_version = result.get("dataset_version") or {}
        if isinstance(dataset_version, dict) and "version" in dataset_version:
            self._resources["dataset_version"] = str(dataset_version["version"])
            actual_dataset_id = str(dataset_version.get("dataset_id", ""))
            if actual_dataset_id != self._resources["dataset_id"]:
                raise ApiStageError("Worker dataset does not match the explicit Tag Schema target")
        derived = result.get("derived") or {}
        if isinstance(derived, dict) and "step_count" in derived:
            self._resources["base_step_count"] = str(derived["step_count"])
        annotation_task = result.get("annotation_task") or {}
        if isinstance(annotation_task, dict) and annotation_task.get("task_id"):
            self._resources["annotation_task_id"] = str(annotation_task["task_id"])
        return StageResult(
            resource_ids={
                "worker_job_id": self._resources["worker_job_id"],
                **(
                    {"annotation_task_id": self._resources["annotation_task_id"]}
                    if "annotation_task_id" in self._resources
                    else {}
                ),
            },
            request_ids=tuple(request_ids),
        )

    def annotate_multilevel_tags(self, scope: RunScope) -> StageResult:
        token = self._bearers["contractor"]
        automatic_task_id = self._resources.get("annotation_task_id")
        if automatic_task_id is None:
            raise StageNotRunnable(
                "The successful ingest workflow did not expose its automatic annotation task "
                "locator; the historical test-only DB provisioner is not a success-path fallback."
            )
        required = ("dataset_id", "dataset_version", "base_step_count", "tag_schema_id")
        missing = [name for name in required if name not in self._resources]
        if missing:
            raise ApiStageError(
                "Worker result did not expose annotation baseline fields: " + ", ".join(missing)
            )
        listed = self._request(
            "GET",
            f"/api/v1/projects/{scope.project_id}/annotation-tasks",
            expected={200},
            token=token,
            headers={"X-Region-Code": scope.region_code},
        )
        tasks = listed.body if isinstance(listed.body, list) else []
        task = next(
            (item for item in tasks if item.get("rollout_id") == self._resources["rollout_id"]),
            None,
        )
        if task is None:
            raise ApiStageError(
                f"automatic annotation task {automatic_task_id} is not visible through the API"
            )
        task_id = str(task["task_id"])
        if (
            task_id != automatic_task_id
            or task.get("creation_source") != "SYSTEM_LANCE"
            or task.get("region_code") != scope.region_code
            or task.get("tag_schema_id") != self._resources["tag_schema_id"]
            or int(task.get("dataset_version", 0)) != int(self._resources["dataset_version"])
            or int(task.get("base_step_count", 0)) != int(self._resources["base_step_count"])
        ):
            raise ApiStageError("automatic annotation task lineage does not match Worker facts")
        claimed = self._request(
            "POST",
            f"/api/v1/annotation-tasks/{task_id}/claim",
            expected={200},
            token=token,
            headers={
                "X-Project-ID": scope.project_id,
                "X-Region-Code": scope.region_code,
            },
        )
        saved = self._request(
            "POST",
            f"/api/v1/annotation-tasks/{task_id}/revisions",
            expected={201},
            token=token,
            headers={
                "X-Project-ID": scope.project_id,
                "X-Region-Code": scope.region_code,
                "If-Match": claimed.etag or "",
            },
            json_body={
                "expected_revision": 0,
                "client_mutation_id": f"{scope.run_id}-tag-save-1",
                "operations": [],
                "tags": [
                    {
                        "annotation_id": f"{scope.run_id}-grab-success",
                        "tag_id": "grab-success",
                        "path": ["operation-stage", "grab-action", "grab-success"],
                        "start_step": 0,
                        "end_step": 10,
                        "attributes": {},
                        "relations": [],
                    }
                ],
            },
        )
        submitted = self._request(
            "POST",
            f"/api/v1/annotation-tasks/{task_id}/submit",
            expected={201},
            token=token,
            headers={
                "X-Project-ID": scope.project_id,
                "X-Region-Code": scope.region_code,
                "If-Match": saved.etag or "",
                "Idempotency-Key": f"{scope.run_id}-tag-submit-1",
            },
            json_body={"expected_revision": 1},
        )
        self._resources.update(
            {
                "annotation_task_id": task_id,
                "annotation_revision": "1",
                "annotation_submission_id": str(submitted.body["submission_id"]),
                "annotation_etag": submitted.etag or "",
            }
        )
        return StageResult(
            resource_ids={
                "annotation_task_id": task_id,
                "annotation_revision": "1",
                "annotation_submission_id": self._resources["annotation_submission_id"],
            },
            request_ids=tuple(
                item
                for item in (
                    listed.request_id,
                    claimed.request_id,
                    saved.request_id,
                    submitted.request_id,
                )
                if item
            ),
        )

    def review_tags(self, scope: RunScope) -> StageResult:
        task_id = self._resources["annotation_task_id"]
        reviewed = self._request(
            "POST",
            f"/api/v1/annotation-tasks/{task_id}/reviews",
            expected={200},
            token=self._bearers["admin"],
            headers={
                "X-Project-ID": scope.project_id,
                "X-Region-Code": scope.region_code,
                "If-Match": self._resources["annotation_etag"],
            },
            json_body={
                "revision": int(self._resources["annotation_revision"]),
                "submission_id": self._resources["annotation_submission_id"],
                "decision": "APPROVE",
                "comment": "BE22 distinct test reviewer",
            },
        )
        if reviewed.body.get("status") != "APPROVED":
            raise ApiStageError("Tag revision was not approved")
        return StageResult(
            resource_ids={"approved_revision": self._resources["annotation_revision"]},
            request_ids=(reviewed.request_id,) if reviewed.request_id else (),
        )

    def close_collection_task(self, scope: RunScope) -> StageResult:
        task_id = self._resources["collection_task_id"]
        path = f"/api/v1/projects/{scope.project_id}/collection-tasks/{task_id}"
        detail = self._request("GET", path, expected={200}, token=self._bearers["contractor"])
        headers = {
            "If-Match": detail.etag or "",
            "Idempotency-Key": f"{scope.run_id}-close-task",
        }
        closed = self._request(
            "POST",
            f"{path}:close",
            expected={200},
            token=self._bearers["contractor"],
            headers=headers,
        )
        replay = self._request(
            "POST",
            f"{path}:close",
            expected={200},
            token=self._bearers["contractor"],
            headers=headers,
        )
        if closed.body != replay.body or closed.body.get("status") != "CLOSED":
            raise ApiStageError("collection-task close was not an idempotent CLOSED result")
        return StageResult(
            resource_ids={"collection_task_id": task_id, "status": "CLOSED"},
            request_ids=tuple(
                item for item in (detail.request_id, closed.request_id, replay.request_id) if item
            ),
        )
