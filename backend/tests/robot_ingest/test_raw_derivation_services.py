"""Real PG/MinIO registration recovery from explicit synthetic recording input.

Version labels exercise receipt selection, not historical binary/migration or
robot hardware acceptance. The original fixture is copied before unit declaration.
"""

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from hc_data_platform.continuous_recordings.registration import PostgresRecordingRegistration
from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.ingest.ports import crc64_ecma
from hc_data_platform.robot_ingest import raw_recording
from hc_data_platform.robot_ingest.models import (
    CompleteAssetCommand,
    CompletedPart,
    CreateRobotIngestIdentity,
    IssueCredentialCommand,
    RobotIngestAssetManifest,
    RobotIngestUploadManifest,
)
from hc_data_platform.robot_ingest.recording_bridge import (
    RecordingOutboxHandler,
    recording_upload_id,
    result,
)
from hc_data_platform.robot_ingest.recording_contract import OpenArmRecordingComplete
from hc_data_platform.robot_ingest.repository import PostgresRobotIngestRepository
from hc_data_platform.robot_ingest.service import RobotIngestService
from hc_data_platform.security.auth import AuthContext
from tests.continuous_recordings.test_openarm_raw import declared_synthetic_fixture

pytest_plugins = ["tests.episode_store.test_commit_services"]


def forbidden(*args, **kwargs):
    raise AssertionError("completed Raw derivation must not be encoded again")


@pytest.fixture
def uploaded_raw(services, tmp_path):
    repository, storage = services
    root = tmp_path / "original"
    declared_synthetic_fixture(root)
    marker = json.loads((root / "recording-complete.json").read_bytes())
    # New fixture-only attribution uses the recorder's explicit binding file;
    # original MCAP/session bytes in the copied recording stay unchanged.
    task = "synthetic-raw-pin-" + uuid4().hex
    binding = json.dumps({"collection_task_id": task, "synthetic": True}).encode()
    (root / "capture-binding.json").write_bytes(binding)
    marker["collection_task_id"] = task
    marker["recording_upload"]["collection_task_id"] = task
    marker["recording_upload"]["assets"].append(
        {
            "path": "capture-binding.json",
            "role": "AUXILIARY",
            "media_type": "application/json",
            "size": len(binding),
            "sha256": hashlib.sha256(binding).hexdigest(),
            "crc64": crc64_ecma(binding),
            "part_count": 1,
        }
    )
    marker["content_sha256"] = hashlib.sha256(
        json.dumps(
            marker["recording_upload"]["assets"], sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    (root / "recording-complete.json").write_text(json.dumps(marker))
    command = OpenArmRecordingComplete.model_validate(marker).recording_upload
    org, project, region = "raw-pin-" + uuid4().hex, "raw-pin-project", "raw-pin-region"
    scope = RequestContext(
        organization_id=org,
        project_id=project,
        region_code=region,
        subject_id="synthetic-raw-registration-test",
    )
    token = bind_request_context(scope)
    try:
        with repository.transaction() as cursor:
            cursor.execute(
                "INSERT INTO registry.organization_projects VALUES (%s,%s,%s)",
                (org, project, "Synthetic Raw pinning"),
            )
            cursor.execute(
                """INSERT INTO robotics.robot_assets
                (organization_id,robot_id,display_name,serial_no,lifecycle_status,connectivity_state,etag,topology_revision)
                VALUES (%s,%s,'Synthetic Raw','synthetic','ACTIVE','ONLINE','v1','v1')""",
                (org, command.robot_id),
            )
            cursor.execute(
                """INSERT INTO collection_tasks.collection_tasks
                (organization_id,project_id,collection_task_id,dataset_id,task_code,name,task_type,scenario,
                 status,create_fingerprint,upload_region_code)
                VALUES (%s,%s,%s,'dataset_raw_pinning',%s,
                        'Synthetic Raw','ROBOT_CAPTURE','software',
                        'ACTIVE',%s,%s)""",
                (
                    org,
                    project,
                    command.collection_task_id,
                    str(uuid4().int % 100_000_000).zfill(8),
                    "1" * 64,
                    region,
                ),
            )
        actor = AuthContext(
            subject_id=scope.subject_id,
            organization_ids=frozenset({org}),
            project_ids=frozenset({project}),
            region_codes=frozenset({region}),
            organization_scope_triples=frozenset({(org, project, region)}),
            scope_pairs=frozenset({(project, region)}),
            organization_scoped_capabilities=frozenset(
                {(org, project, "ingest_source.read"), (org, project, "ingest_source.manage")}
            ),
        )
        ingest = RobotIngestService(
            PostgresRobotIngestRepository.from_dsn(
                os.environ["HC_G1_TEST_POSTGRES_DSN"], repository.connections
            ),
            storage,
            credential_hmac_key="isolated-raw-version-test-hmac-only",
        )
        identity = ingest.create_identity(
            auth=actor,
            organization_id=org,
            project_id=project,
            command=CreateRobotIngestIdentity(
                robot_id=command.robot_id, allowed_formats=("CAPTURE_BUNDLE",)
            ),
        ).data
        credential = ingest.issue_credential(
            auth=actor,
            organization_id=org,
            project_id=project,
            ingest_identity_id=identity.ingest_identity_id,
            command=IssueCredentialCommand(),
        ).credential
        declared = {item.path: item for item in command.assets}
        files = [*declared, "recording-complete.json"]
        assets = []
        for i, name in enumerate(files):
            content = (root / name).read_bytes()
            item = declared.get(name)
            assets.append(
                RobotIngestAssetManifest(
                    asset_id=f"asset-{i}",
                    path=name,
                    role=item.role.value if item else "AUXILIARY",
                    media_type=item.media_type if item else "application/json",
                    size_bytes=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                    crc64=crc64_ecma(content),
                    camera_id=item.camera_id if item else None,
                )
            )
        upload = ingest.create_upload(
            token=credential.token,
            manifest=RobotIngestUploadManifest(
                client_upload_id=str(uuid4()),
                collection_task_id=command.collection_task_id,
                robot_id=command.robot_id,
                capture_mode="CONTINUOUS",
                source_format="CAPTURE_BUNDLE",
                source_format_version="2",
                capture_started_at=command.capture_started_at,
                capture_ended_at=command.capture_ended_at,
                assets=tuple(assets),
                format_metadata={
                    "recording_id": command.recording_id,
                    "openarm_recording": {"version": 2, "marker_path": "recording-complete.json"},
                },
            ),
        ).data
        for asset in upload.assets:
            path = root / asset.path
            with path.open("rb") as stream:
                part = storage.upload_part_stream(
                    asset.object_key, asset.multipart_upload_id, 1, stream, path.stat().st_size
                )
            ingest.complete_asset(
                token=credential.token,
                upload_id=upload.upload_id,
                asset_id=asset.asset_id,
                command=CompleteAssetCommand(parts=(CompletedPart(part_number=1, etag=part.etag),)),
            )
        upload = ingest.commit_upload(token=credential.token, upload_id=upload.upload_id).data
        event = DomainEventEnvelope(
            event_id=str(uuid4()),
            event_type=RecordingOutboxHandler.EVENT_TYPE,
            aggregate_type="raw_source",
            aggregate_id=upload.raw_source_id,
            organization_id=org,
            project_id=project,
            region_code=region,
            payload={"upload_id": upload.upload_id},
        )
        yield repository, storage, upload, marker, command, event
    finally:
        reset_request_context(token)


@pytest.mark.integration
def test_registered_derivation_survives_newer_receipt_concurrent_retry_and_missing_receipt(
    uploaded_raw, monkeypatch
):
    repository, storage, upload, marker, command, event = uploaded_raw
    # Seed a pre-capture-plan derivation receipt explicitly. A newly committed
    # upload without a plan now carries CAPTURE_PLAN_REQUIRED; it must not be
    # mistaken for the historical no-snapshot fixture exercised by this test.
    # The DB upload is left unchanged: replay below must use the checked receipt.
    historical = upload.model_copy(update={"processing_config_snapshot": None})
    originals = {asset.path: asset for asset in upload.assets}
    # A real prepared/encoded fixture is saved under an older recipe label. This
    # tests cache/DB binding, and deliberately does not claim an old binary run.
    current = raw_recording.VERSION
    monkeypatch.setattr(raw_recording, "VERSION", "openarm-raw-platform/3")
    old, old_assets = raw_recording.materialize(storage, historical, marker, command, originals)
    monkeypatch.setattr(raw_recording, "VERSION", current)
    old_key = raw_recording.receipt_prefix(upload, "openarm-raw-platform/3") + "/receipt.json"
    receipt = json.loads(b"".join(storage.read_chunks(old_key)))
    for failure in ("source", "identity", "foreign-object", "missing-original"):
        corrupted = json.loads(json.dumps(receipt))
        if failure == "source":
            corrupted["source_sha256"] = "0" * 64
        elif failure == "identity":
            corrupted["command"]["robot_id"] = "another-robot"
        elif failure == "foreign-object":
            next(iter(corrupted["assets"].values()))["object_key"] = "another-tenant/same-hash"
        else:
            path = next(key for key in corrupted["assets"] if key.startswith("source/original/"))
            del corrupted["assets"][path]
            corrupted["command"]["assets"] = [
                item for item in corrupted["command"]["assets"] if item["path"] != path
            ]
        with pytest.raises((ValueError, PermissionError)):
            raw_recording.checked_receipt(
                corrupted, upload, marker, command, originals, "openarm-raw-platform/3"
            )
    handler = RecordingOutboxHandler(
        repository.connections, storage, registrations=PostgresRecordingRegistration()
    )
    with monkeypatch.context() as patch:
        patch.setattr(raw_recording, "prepare", forbidden)
        handler.adopt(event)
    assert result(repository.connections, upload)["status"] == "AWAITING_SLICE"
    # Simulate a newer orphaned derivation while the registered command pins /3.
    with monkeypatch.context() as patch:
        patch.setattr(raw_recording, "READABLE_VERSIONS", (current,))
        newer, _ = raw_recording.materialize(storage, historical, marker, command, originals)
    # Equal bytes/manifests in two recipes still have distinct frozen locators.
    assert newer.recording_id == old.recording_id
    with monkeypatch.context() as patch:
        patch.setattr(raw_recording, "prepare", forbidden)
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(handler.adopt, [event, event, event]))
        # Hide only this test's old receipt; do not delete any stored object.
        head = storage.head
        patch.setattr(storage, "head", lambda key: None if key == old_key else head(key))
        with pytest.raises(ValueError, match="pinned recording derivation receipt is missing"):
            handler.adopt(event)
    with repository.transaction() as cursor:
        cursor.execute(
            "SELECT upload_document FROM ingest.recording_uploads "
            "WHERE organization_id=%s AND upload_id=%s",
            (upload.target.organization_id, recording_upload_id(upload.upload_id)),
        )
        document = cursor.fetchone()[0]
        assert document["command"] == old.model_copy(
            update={"collection_job_id": upload.collection_job_id}
        ).model_dump(mode="json")
        cursor.execute(
            "SELECT asset_path,object_key FROM ingest.recording_upload_assets "
            "WHERE organization_id=%s AND upload_id=%s",
            (upload.target.organization_id, recording_upload_id(upload.upload_id)),
        )
        assert dict(cursor.fetchall()) == {
            path: asset.object_key for path, asset in old_assets.items()
        }
    with pytest.raises(RuntimeError, match="upload not found"):
        handler.adopt(event.model_copy(update={"organization_id": "another-organization"}))
    for asset in old_assets.values():
        raw_recording.verify(
            storage,
            asset.object_key,
            asset.expected_size_bytes,
            asset.expected_sha256,
            lambda: None,
        )
    if evidence_root := os.environ.get("HC_G1_WRITER_EVIDENCE_ROOT"):
        evidence = {
            "scope": (
                "synthetic raw input, real PostgreSQL/MinIO/service registration; "
                "no hardware or historical binary claim"
            ),
            "upload_id": upload.upload_id,
            "raw_source_id": upload.raw_source_id,
            "recording_upload_id": str(recording_upload_id(upload.upload_id)),
            "original_seal_sha256": marker["content_sha256"],
            "pinned_recipe": "openarm-raw-platform/3",
            "running_recipe": current,
            "asset_count": len(old_assets),
            "concurrent_retries": 3,
            "pinned_object_keys_and_bytes_preserved": True,
            "encoder_called_on_retry": False,
            "missing_pinned_receipt_refused_regeneration": True,
            "foreign_scope_and_corrupt_receipts_rejected": True,
            "status": "AWAITING_SLICE",
            "independent_episode_package": "NOT_YET_GENERATED",
        }
        (Path(evidence_root) / "raw-derivation-recovery.json").write_text(
            json.dumps(evidence, indent=2) + "\n"
        )
