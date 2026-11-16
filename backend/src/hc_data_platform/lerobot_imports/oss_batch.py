"""Validated input for durable OSS imports."""

from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hc_data_platform.ingest.models import Identifier

BATCH_WORKFLOW = "OssBatchImportWorkflow"


class SubmitOssBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    source_uri: str = Field(min_length=1, max_length=2048)
    inventory_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_id: Identifier
    collection_task_id: Identifier
    robot_id: Identifier
    processing_mode: Literal["PROCESS"] = "PROCESS"
    processing_capability_revision: str | None = None
    recording_paths: list[Annotated[str, Field(min_length=1, max_length=1024)]] | None = Field(
        default=None, min_length=1
    )

    @field_validator("source_uri")
    @classmethod
    def unsigned_source(cls, uri: str) -> str:
        parts = urlsplit(uri)
        if parts.query or parts.fragment or parts.username or parts.password:
            raise ValueError("Use an unsigned OSS address.")
        return uri

    @field_validator("recording_paths")
    @classmethod
    def unique_recordings(cls, paths: list[str] | None) -> list[str] | None:
        if paths is not None:
            if len(set(paths)) != len(paths) or any(not p.lower().endswith(".mcap") for p in paths):
                raise ValueError("Select distinct MCAP recordings.")
            if sum(len(p.encode()) + 4 for p in paths) > 1024**2:
                raise ValueError("Recording selection exceeds the workflow message size.")
        return paths


class BatchInput(BaseModel):
    organization_id: str
    project_id: str
    region_code: str
    actor_id: str
    command: SubmitOssBatch
    offset: int = 0
    failed: int = 0
    first_failure: str | None = None


class BatchItem(BaseModel):
    batch_id: str | None = None
    organization_id: str
    project_id: str
    region_code: str
    actor_id: str
    command: dict


class BatchAccepted(BaseModel):
    batch_number: int | None = Field(default=None, ge=1)
    batch_id: str
    total: int


class OssBatchSummary(BatchAccepted):
    status: str
    source_name: str
    submitted: int
    failed: int
    first_failure: str | None = None


def record_accepted(batch: BatchInput, job) -> None:
    """Make newly queued work visible even before a worker starts polling."""
    import json
    from uuid import NAMESPACE_URL, uuid5
    from .configuration import connections

    command = batch.command
    summary = dict(
        region_code=batch.region_code,
        total=len(command.recording_paths or [None]),
        submitted=0,
        failed=0,
        source_name=command.source_uri.rstrip("/").rsplit("/", 1)[-1],
    )
    with connections()() as db, db.cursor() as cursor:
        cursor.execute(
            """INSERT INTO workflow.jobs
               (job_id, organization_id, workflow_id, workflow_run_id, project_id, resource_id,
                job_type, status, stage, result, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,'PENDING','pending',%s::jsonb,%s,%s)
               ON CONFLICT (workflow_id) DO NOTHING""",
            (
                uuid5(NAMESPACE_URL, f"hc-data-platform/workflow-job/{job.workflow_id}"),
                batch.organization_id,
                job.workflow_id,
                job.workflow_run_id,
                batch.project_id,
                str(command.request_id),
                BATCH_WORKFLOW,
                json.dumps(summary),
                job.created_at,
                job.created_at,
            ),
        )
