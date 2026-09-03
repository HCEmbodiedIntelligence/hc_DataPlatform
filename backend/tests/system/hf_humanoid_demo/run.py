"""Provision an isolated scope and run one pinned Hugging Face G1 episode through Worker."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from hc_data_platform.ingest.cli import import_offline_bundle
from tests.system.hf_humanoid_demo.convert import (
    ACTION_TOPIC,
    FPS,
    prepare_bundle,
)
from tests.system.wave2.fixture import RunScope
from tests.system.wave2.http_adapter import HttpMainChainAdapter, Wave2TestJwtIssuer


class HfHumanoidDemoAdapter(HttpMainChainAdapter):
    """Reuse the test-profile identity setup while uploading an external pinned bundle."""

    def configure_and_upload(self, scope: RunScope, output_dir: Path) -> dict[str, object]:
        task_id = self._resources["collection_task_id"]
        evidence = prepare_bundle(
            output_dir,
            project_id=scope.project_id,
            collection_task_id=task_id,
            run_id=scope.run_id,
            source_cache=(
                Path(cache_path) if (cache_path := os.environ.get("HC_HF_SOURCE_CACHE")) else None
            ),
        )
        manifest_path = Path(str(evidence["manifest_path"]))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        dataset_id = f"{scope.run_id}-dataset"
        snapshot_id = f"{scope.run_id}-schema-v1"
        schema_id = f"{scope.run_id}-tags"
        admin = self._bearers["admin"]

        created_schema = self._request(
            "POST",
            f"/api/v1/projects/{scope.project_id}/tag-schemas",
            expected={201},
            token=admin,
            json_body={
                "schema_id": schema_id,
                "version": 1,
                "name": "Hugging Face Unitree G1 demo lineage",
                "document": {
                    "nodes": [
                        {
                            "tag_id": "source-hugging-face",
                            "code": "source-hugging-face",
                            "display_name": "Source: Hugging Face",
                        },
                        {
                            "tag_id": "robot-unitree-g1",
                            "code": "robot-unitree-g1",
                            "display_name": "Robot: Unitree G1",
                            "parent_tag_id": "source-hugging-face",
                        },
                    ],
                    "mutual_exclusions": [],
                    "object_relations": [],
                },
                "compatible_targets": [
                    {
                        "region_code": scope.region_code,
                        "dataset_id": dataset_id,
                        "dataset_schema_snapshot_id": snapshot_id,
                        "task_kind": "TAGGING",
                    }
                ],
            },
        )
        published_schema = self._request(
            "POST",
            (f"/api/v1/projects/{scope.project_id}/tag-schemas/{schema_id}/versions/1/publish"),
            expected={200},
            token=admin,
        )
        profile_id = f"{scope.run_id}-30hz"
        created_profile = self._request(
            "POST",
            f"/api/v1/projects/{scope.project_id}/quality-profiles",
            expected={201},
            token=admin,
            json_body={
                "profile_id": profile_id,
                "profile_version": 1,
                "required_topics": manifest["expected_topics"],
                "default_timing": {"target_frequency_hz": FPS},
                "action": {
                    "topic": ACTION_TOPIC,
                    "minimum_observation_count_risk": 0,
                    "minimum_observation_count_reject": 0,
                },
            },
        )
        self._resources.update(
            {
                "dataset_id": dataset_id,
                "dataset_schema_snapshot_id": snapshot_id,
                "tag_schema_id": schema_id,
                "quality_profile_id": profile_id,
                "rollout_id": str(manifest["rollout_id"]),
                "data_package_id": str(manifest["data_package_id"]),
            }
        )

        committed = import_offline_bundle(
            Path(str(evidence["conversion"]["mcap_path"])),  # type: ignore[index]
            manifest_path,
            api_base_url=str(self._client.base_url),
            region_code=scope.region_code,
            access_token=self._bearers["contractor"],
            idempotency_key=f"{scope.run_id}-hf-upload",
        )
        workflow = committed.get("workflow")
        if not isinstance(workflow, dict) or not workflow.get("workflow_id"):
            raise RuntimeError("Manifest commit did not return a workflow locator")
        self._resources["ingest_workflow_id"] = str(workflow["workflow_id"])
        if workflow.get("event_id"):
            self._resources["ingest_outbox_event_id"] = str(workflow["event_id"])
        return {
            "dataset_id": dataset_id,
            "schema_snapshot_id": snapshot_id,
            "quality_profile_id": profile_id,
            "tag_schema_id": schema_id,
            "workflow": workflow,
            "source": evidence["dataset"],
            "bundle": evidence["conversion"],
            "request_ids": [
                value
                for value in (
                    created_schema.request_id,
                    published_schema.request_id,
                    created_profile.request_id,
                )
                if value
            ],
        }

    def fetch_completed_job(self) -> dict[str, Any]:
        workflow_id = self._resources["ingest_workflow_id"]
        response = self._request(
            "GET",
            f"/api/v1/jobs/{quote(workflow_id, safe='')}",
            expected={200},
            token=self._bearers["contractor"],
        )
        if not isinstance(response.body, dict):
            raise RuntimeError("Worker job response is not an object")
        return response.body


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--worker-timeout", type=float, default=180)
    args = parser.parse_args()
    if os.environ.get("HC_ENVIRONMENT") != "test":
        raise SystemExit("HC_ENVIRONMENT=test is required")

    scope = RunScope.create(args.run_id)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    artifact: dict[str, object] = {
        "status": "RUNNING",
        "run_id": scope.run_id,
        "project_id": scope.project_id,
        "region_code": scope.region_code,
        "stages": [],
    }
    adapter = HfHumanoidDemoAdapter(
        args.base_url,
        issuer=Wave2TestJwtIssuer.from_environment(),
        worker_timeout_seconds=args.worker_timeout,
    )
    try:
        identities = adapter.register_empty_accounts(scope)
        artifact["stages"].append(  # type: ignore[union-attr]
            {"name": "isolated identities", "status": "PASS", **identities.resource_ids}
        )
        grants = adapter.approve_membership_and_capabilities(scope)
        artifact["stages"].append(  # type: ignore[union-attr]
            {"name": "exact-scope grants", "status": "PASS", **grants.resource_ids}
        )
        collection = adapter.create_collection_task(scope)
        artifact["stages"].append(  # type: ignore[union-attr]
            {"name": "collection task", "status": "PASS", **collection.resource_ids}
        )
        upload = adapter.configure_and_upload(scope, args.output_dir)
        artifact["upload"] = upload
        artifact["stages"].append(  # type: ignore[union-attr]
            {
                "name": "Hugging Face parquet to MCAP to committed Raw/outbox",
                "status": "PASS",
                "workflow_id": upload["workflow"]["workflow_id"],  # type: ignore[index]
            }
        )
        worker = adapter.wait_for_worker_qc(scope)
        job = adapter.fetch_completed_job()
        artifact["job"] = job
        artifact["stages"].append(  # type: ignore[union-attr]
            {"name": "Outbox to Temporal to Lance", "status": "PASS", **worker.resource_ids}
        )
        artifact["status"] = "PASS"
    except Exception as exc:
        artifact["status"] = "FAIL"
        artifact["error"] = {"type": type(exc).__name__, "detail": str(exc)}
        raise
    finally:
        adapter.close()
        (args.output_dir / "run-evidence.json").write_text(
            json.dumps(artifact, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(artifact, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
