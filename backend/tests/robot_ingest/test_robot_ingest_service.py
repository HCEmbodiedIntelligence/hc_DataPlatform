from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.ingest.ports import InMemoryObjectStorage, crc64_ecma
from hc_data_platform.robot_ingest.adapters import AdapterVerification, CustomFormatAdapter
from hc_data_platform.robot_ingest.models import (
    AuthorizePartsCommand,
    CameraVerification,
    CaptureMode,
    CompleteAssetCommand,
    CompletedPart,
    CreateRobotIngestIdentity,
    FpsRational,
    IdentityState,
    IssueCredentialCommand,
    QualityStatus,
    RobotIngestAsset,
    RobotIngestAssetManifest,
    RobotIngestCameraManifest,
    RobotIngestEpisodeResult,
    RobotIngestProcessingStatus,
    RobotIngestUploadManifest,
    RobotIngestUploadPolicy,
    UpdateRobotIngestIdentity,
    UploadTarget,
)
from hc_data_platform.robot_ingest.repository import InMemoryRobotIngestRepository
from hc_data_platform.robot_ingest.service import RobotIngestService
from hc_data_platform.security.auth import AuthContext

NOW = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)
ORG = "robot-org"
PROJECT_A = "project-a"
PROJECT_B = "project-b"
REGION = "cn-east"
ROBOT = "robot-g1"
TASK_A = "task-a-global-id"
TASK_B = "task-b-global-id"


def _admin() -> AuthContext:
    capabilities = {
        (ORG, PROJECT_A, "ingest_source.read"),
        (ORG, PROJECT_A, "ingest_source.manage"),
        (ORG, PROJECT_B, "ingest_source.read"),
        (ORG, PROJECT_B, "ingest_source.manage"),
    }
    return AuthContext(
        subject_id="robot-admin",
        organization_ids=frozenset({ORG}),
        project_ids=frozenset({PROJECT_A, PROJECT_B}),
        region_codes=frozenset({REGION}),
        scope_pairs=frozenset({(PROJECT_A, REGION), (PROJECT_B, REGION)}),
        organization_scope_triples=frozenset({(ORG, PROJECT_A, REGION), (ORG, PROJECT_B, REGION)}),
        organization_scoped_capabilities=frozenset(capabilities),
    )


def _target(task_id: str, project_id: str, *, status: str = "ACTIVE") -> UploadTarget:
    return UploadTarget(
        collection_task_id=task_id,
        organization_id=ORG,
        project_id=project_id,
        dataset_id=f"dataset_{project_id.replace('-', '_')}",
        region_code=REGION,
        task_status=status,
    )


class Harness:
    def __init__(self, *, extra_targets: tuple[UploadTarget, ...] = ()) -> None:
        self.now = NOW
        self.repository = InMemoryRobotIngestRepository(
            targets=(
                _target(TASK_A, PROJECT_A),
                _target(TASK_B, PROJECT_B),
                *extra_targets,
            )
        )
        self.storage = InMemoryObjectStorage()
        self.service = RobotIngestService(
            self.repository,
            self.storage,
            credential_hmac_key="robot-ingest-test-hmac-key",
            clock=lambda: self.now,
        )
        identity = self.service.create_identity(
            auth=_admin(),
            organization_id=ORG,
            project_id=PROJECT_A,
            command=CreateRobotIngestIdentity(
                robot_id=ROBOT,
                display_name="G1 采集机器人",
                allowed_formats=("MCAP", "LEROBOT_V3", "CAPTURE_BUNDLE", "CUSTOM_V1"),
            ),
        ).data
        issued = self.service.issue_credential(
            auth=_admin(),
            organization_id=ORG,
            project_id=PROJECT_A,
            ingest_identity_id=identity.ingest_identity_id,
            command=IssueCredentialCommand(),
        )
        assert issued.credential is not None
        self.identity_id = identity.ingest_identity_id
        self.credential_id = issued.credential.credential_id
        self.token = issued.credential.token


def _asset(
    asset_id: str, path: str, body: bytes, *, camera_id: str | None = None
) -> RobotIngestAssetManifest:
    return RobotIngestAssetManifest(
        asset_id=asset_id,
        path=path,
        media_type="application/octet-stream",
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
        crc64=crc64_ecma(body),
        camera_id=camera_id,
    )


def _manifest(
    *,
    task_id: str = TASK_A,
    robot_id: str = ROBOT,
    source_format: str = "MCAP",
    capture_mode: CaptureMode = CaptureMode.PRESEGMENTED,
    body: bytes = b"robot-raw-data",
    client_upload_id: str | None = None,
    declared_episode_count: int | None = 1,
    format_metadata: dict[str, object] | None = None,
    assets: tuple[RobotIngestAssetManifest, ...] | None = None,
    cameras: tuple[RobotIngestCameraManifest, ...] = (),
    collection_job_id: str | None = None,
    organization_id: str | None = None,
    project_id: str | None = None,
) -> RobotIngestUploadManifest:
    return RobotIngestUploadManifest(
        client_upload_id=client_upload_id or str(uuid4()),
        collection_task_id=task_id,
        collection_job_id=collection_job_id,
        robot_id=robot_id,
        capture_mode=capture_mode,
        source_format=source_format,
        source_format_version="1",
        capture_started_at=NOW,
        capture_ended_at=NOW + timedelta(hours=2),
        declared_episode_count=declared_episode_count,
        assets=assets or (_asset("raw", "capture/raw.bin", body),),
        cameras=cameras,
        format_metadata=format_metadata or {},
        organization_id=organization_id,
        project_id=project_id,
    )


def _complete_first_asset(harness: Harness, manifest: RobotIngestUploadManifest) -> str:
    created = harness.service.create_upload(token=harness.token, manifest=manifest).data
    body = b"robot-raw-data"
    part = harness.storage.upload_part(
        created.assets[0].multipart_upload_id,
        1,
        body,
        key=created.assets[0].object_key,
    )
    harness.service.complete_asset(
        token=harness.token,
        upload_id=created.upload_id,
        asset_id=created.assets[0].asset_id,
        command=CompleteAssetCommand(parts=(CompletedPart(part_number=1, etag=part.etag),)),
    )
    return created.upload_id


def _assert_problem(code: str, action: object) -> None:
    assert callable(action)
    with pytest.raises(ProblemException) as caught:
        action()
    assert caught.value.problem.code == code


def test_robot_authentication_rejects_missing_revoked_expired_and_disabled_credentials() -> None:
    harness = Harness()
    manifest = _manifest()
    _assert_problem(
        "ROBOT_CREDENTIAL_INVALID",
        lambda: harness.service.create_upload(token="not-a-robot-token", manifest=manifest),
    )
    harness.service.revoke_credential(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=harness.identity_id,
        credential_id=harness.credential_id,
    )
    _assert_problem(
        "ROBOT_CREDENTIAL_REVOKED",
        lambda: harness.service.create_upload(token=harness.token, manifest=manifest),
    )

    expiring = harness.service.issue_credential(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=harness.identity_id,
        command=IssueCredentialCommand(expires_at=NOW + timedelta(minutes=1)),
    ).credential
    assert expiring is not None
    harness.now = NOW + timedelta(minutes=2)
    _assert_problem(
        "ROBOT_CREDENTIAL_EXPIRED",
        lambda: harness.service.create_upload(token=expiring.token, manifest=manifest),
    )
    listed = harness.service.list_credentials(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=harness.identity_id,
    )
    assert (
        next(item for item in listed if item.credential_id == expiring.credential_id).state.value
        == "EXPIRED"
    )
    harness.now = NOW
    active = harness.service.issue_credential(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=harness.identity_id,
        command=IssueCredentialCommand(),
    ).credential
    assert active is not None
    harness.service.set_identity_state(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=harness.identity_id,
        state=IdentityState.DISABLED,
    )
    _assert_problem(
        "ROBOT_IDENTITY_DISABLED",
        lambda: harness.service.create_upload(token=active.token, manifest=manifest),
    )


def test_authoritative_robot_and_task_scope_are_enforced() -> None:
    harness = Harness()
    _assert_problem(
        "ROBOT_IDENTITY_MISMATCH",
        lambda: harness.service.create_upload(
            token=harness.token, manifest=_manifest(robot_id="another-robot")
        ),
    )
    _assert_problem(
        "UPLOAD_SCOPE_MISMATCH",
        lambda: harness.service.create_upload(
            token=harness.token,
            manifest=_manifest(project_id=PROJECT_B),
        ),
    )
    created = harness.service.create_upload(
        token=harness.token,
        manifest=_manifest(organization_id=ORG, project_id=PROJECT_A),
    ).data
    assert created.target.organization_id == ORG
    assert created.target.project_id == PROJECT_A
    assert created.target.dataset_id == "dataset_project_a"


def test_same_robot_can_upload_to_tasks_in_different_projects_without_assignment() -> None:
    harness = Harness()
    upload_a = harness.service.create_upload(
        token=harness.token, manifest=_manifest(task_id=TASK_A)
    ).data
    upload_b = harness.service.create_upload(
        token=harness.token, manifest=_manifest(task_id=TASK_B)
    ).data
    assert upload_a.authenticated_robot_id == upload_b.authenticated_robot_id == ROBOT
    assert {upload_a.target.project_id, upload_b.target.project_id} == {PROJECT_A, PROJECT_B}
    assert upload_a.collection_job_id != upload_b.collection_job_id


def test_duplicate_task_identity_and_inactive_task_fail_closed() -> None:
    repository = InMemoryRobotIngestRepository(targets=(_target(TASK_A, PROJECT_A),))
    with pytest.raises(Exception, match="globally unique"):
        repository.add_target(_target(TASK_A, PROJECT_B))
    harness = Harness(extra_targets=(_target("closed-task", PROJECT_A, status="CLOSED"),))
    _assert_problem(
        "COLLECTION_TASK_INACTIVE",
        lambda: harness.service.create_upload(
            token=harness.token, manifest=_manifest(task_id="closed-task")
        ),
    )


def test_collection_job_must_match_task_and_authenticated_robot() -> None:
    harness = Harness()
    target = harness.repository.resolve_upload_target(TASK_A)
    assert target is not None
    harness.repository.resolve_collection_job(
        target=target,
        robot_id="different-robot",
        collection_job_id=None,
        generated_collection_job_id="preexisting-job",
        now=NOW,
    )
    _assert_problem(
        "COLLECTION_JOB_MISMATCH",
        lambda: harness.service.create_upload(
            token=harness.token,
            manifest=_manifest(collection_job_id="preexisting-job"),
        ),
    )


def test_lerobot_multi_episode_commit_and_idempotent_raw_registration() -> None:
    harness = Harness()
    manifest = _manifest(
        source_format="LEROBOT_V3",
        declared_episode_count=36,
        format_metadata={"verified_episode_count": 36},
    )
    upload_id = _complete_first_asset(harness, manifest)
    committed = harness.service.commit_upload(token=harness.token, upload_id=upload_id)
    assert committed.data.declared_episode_count == 36
    assert committed.data.verified_episode_count is None
    assert committed.data.raw_source_id is not None
    verified = harness.service.apply_processing_result(
        organization_id=ORG,
        upload_id=upload_id,
        ingest_identity_id=harness.identity_id,
        verified_episode_count=36,
    )
    assert verified.verified_episode_count == 36
    replay = harness.service.commit_upload(token=harness.token, upload_id=upload_id)
    assert replay.resumed is True
    assert replay.data.raw_source_id == committed.data.raw_source_id


def test_two_hour_continuous_raw_is_registered_before_derived_episodes() -> None:
    harness = Harness()
    manifest = _manifest(
        source_format="CAPTURE_BUNDLE",
        capture_mode=CaptureMode.CONTINUOUS,
        declared_episode_count=None,
    )
    upload_id = _complete_first_asset(harness, manifest)
    committed = harness.service.commit_upload(token=harness.token, upload_id=upload_id).data
    assert committed.raw_capture_count == 1
    assert committed.derived_episode_count == 0
    assert committed.processing_status.value == "DISCOVERING_EPISODES"
    processed = harness.service.apply_processing_result(
        organization_id=ORG,
        upload_id=upload_id,
        ingest_identity_id=harness.identity_id,
        episode_results=tuple(
            RobotIngestEpisodeResult(
                episode_id=f"episode_{index:02d}",
                source_episode_index=index,
                status="READY",
                frame_count=1_800,
                sample_count=60_000,
                dataset_version=1,
                lance_version=1,
                quality_status=QualityStatus.PASS,
                qc_report_id=f"qc_{index:02d}",
                created_at=NOW,
                updated_at=NOW,
            )
            for index in range(12)
        ),
    )
    assert processed.derived_episode_count == 12
    assert processed.verified_sample_count == 720_000
    assert processed.processing_status.value == "READY"
    assert processed.capture_ended_at - processed.capture_started_at == timedelta(hours=2)
    lineage = harness.service.list_upload_episode_results(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        region_code=REGION,
        upload_id=upload_id,
    )
    assert lineage.raw_source_id == committed.raw_source_id
    assert len(lineage.items) == 12

    with pytest.raises(ValueError, match="must agree with authoritative Episode results"):
        harness.service.apply_processing_result(
            organization_id=ORG,
            upload_id=upload_id,
            ingest_identity_id=harness.identity_id,
            processing_status=RobotIngestProcessingStatus.FAILED,
            episode_results=lineage.items,
        )
    assert lineage.items[0].qc_report_id == "qc_00"
    statistics = harness.service.statistics(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        region_code=REGION,
        robot_id=ROBOT,
    )
    assert statistics.qc_pass_count == 12
    assert statistics.frame_count == 21_600
    assert statistics.sample_count == 720_000


def test_multi_camera_declarations_and_verified_metrics_remain_separate() -> None:
    harness = Harness()
    body_a = b"camera-a"
    body_b = b"camera-b"
    cameras = tuple(
        RobotIngestCameraManifest(
            camera_id=camera_id,
            asset_id=asset_id,
            codec="h264",
            width=1920,
            height=1080,
            declared_fps=FpsRational(numerator=30_000, denominator=1001),
            declared_frame_count=100,
            declared_duration_ns=3_336_666_667,
            clock_domain="robot-monotonic",
            capture_started_at=NOW,
            capture_ended_at=NOW + timedelta(seconds=4),
        )
        for camera_id, asset_id in (("front", "video-front"), ("wrist", "video-wrist"))
    )
    manifest = _manifest(
        assets=(
            _asset("video-front", "video/front.mp4", body_a, camera_id="front"),
            _asset("video-wrist", "video/wrist.mp4", body_b, camera_id="wrist"),
        ),
        cameras=cameras,
    )
    created = harness.service.create_upload(token=harness.token, manifest=manifest).data
    for asset, body in zip(created.assets, (body_a, body_b), strict=True):
        part = harness.storage.upload_part(
            asset.multipart_upload_id,
            1,
            body,
            key=asset.object_key,
        )
        harness.service.complete_asset(
            token=harness.token,
            upload_id=created.upload_id,
            asset_id=asset.asset_id,
            command=CompleteAssetCommand(parts=(CompletedPart(part_number=1, etag=part.etag),)),
        )
    harness.service.commit_upload(token=harness.token, upload_id=created.upload_id)
    metrics = (
        CameraVerification(
            camera_id="front",
            verified_fps=FpsRational(numerator=30, denominator=1),
            verified_frame_count=120,
            verified_duration_ns=4_000_000_000,
            synchronization_status="PASS",
            continuity_status="PASS",
            validation_status="PASS",
        ),
        CameraVerification(
            camera_id="wrist",
            verified_fps=FpsRational(numerator=30, denominator=1),
            verified_frame_count=118,
            verified_duration_ns=4_000_000_000,
            synchronization_status="RISK",
            continuity_status="MISSING_FRAMES",
            validation_status="RISK",
            failure_code="FRAME_GAP",
        ),
    )
    with pytest.raises(ValueError, match="every declared camera exactly once"):
        harness.service.apply_processing_result(
            organization_id=ORG,
            upload_id=created.upload_id,
            ingest_identity_id=harness.identity_id,
            camera_verification=metrics[:1],
        )
    updated = harness.service.apply_processing_result(
        organization_id=ORG,
        upload_id=created.upload_id,
        ingest_identity_id=harness.identity_id,
        camera_verification=metrics,
    )
    assert updated.cameras[0].declared_frame_count == 100
    assert updated.camera_verification[0].verified_frame_count == 120
    assert updated.camera_verification[1].failure_code == "FRAME_GAP"
    statistics = harness.service.statistics(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        region_code=REGION,
        robot_id=ROBOT,
    )
    assert statistics.capture_duration_ns == 4_000_000_000


def test_processing_results_require_committed_raw_and_authoritative_identity() -> None:
    harness = Harness()
    created = harness.service.create_upload(token=harness.token, manifest=_manifest()).data
    with pytest.raises(ValueError, match="committed immutable Raw"):
        harness.service.apply_processing_result(
            organization_id=ORG,
            upload_id=created.upload_id,
            ingest_identity_id=harness.identity_id,
            verified_episode_count=1,
        )
    upload_id = _complete_first_asset(harness, _manifest())
    harness.service.commit_upload(token=harness.token, upload_id=upload_id)
    with pytest.raises(KeyError):
        harness.service.apply_processing_result(
            organization_id=ORG,
            upload_id=upload_id,
            ingest_identity_id="rii-different",
            verified_episode_count=1,
        )
    with pytest.raises(KeyError):
        harness.service.apply_processing_result(
            organization_id="different-organization",
            upload_id=upload_id,
            ingest_identity_id=harness.identity_id,
            verified_episode_count=1,
        )


def test_episode_count_drift_is_quality_risk_and_client_verified_hints_are_not_trusted() -> None:
    harness = Harness()
    upload_id = _complete_first_asset(
        harness,
        _manifest(
            declared_episode_count=3,
            format_metadata={"verified_episode_count": 3, "verified_sample_count": 999},
        ),
    )
    committed = harness.service.commit_upload(token=harness.token, upload_id=upload_id).data
    assert committed.verified_episode_count is None
    assert committed.verified_sample_count == 0
    episode_results = tuple(
        RobotIngestEpisodeResult(
            episode_id=f"episode-drift-{index}",
            source_episode_index=index,
            status="READY",
            frame_count=100,
            sample_count=100,
            dataset_version=1,
            lance_version=1,
            quality_status=QualityStatus.PASS,
            qc_report_id=f"qc-drift-{index}",
            created_at=NOW,
            updated_at=NOW,
        )
        for index in range(2)
    )
    processed = harness.service.apply_processing_result(
        organization_id=ORG,
        upload_id=upload_id,
        ingest_identity_id=harness.identity_id,
        episode_results=episode_results,
    )
    assert processed.verified_episode_count == 2
    assert processed.verified_frame_count == 200
    assert processed.verified_sample_count == 200
    assert processed.quality_status is QualityStatus.RISK
    with pytest.raises(ValueError, match="Episode identity set is immutable"):
        harness.service.apply_processing_result(
            organization_id=ORG,
            upload_id=upload_id,
            ingest_identity_id=harness.identity_id,
            episode_results=episode_results[:1],
        )
    unchanged = harness.repository.get_upload(ORG, upload_id, harness.identity_id)
    assert unchanged is not None
    assert unchanged.verified_episode_count == 2


def test_project_history_and_resolved_attempts_never_cross_project_scope() -> None:
    harness = Harness()
    upload_a = harness.service.create_upload(token=harness.token, manifest=_manifest()).data
    upload_b = harness.service.create_upload(
        token=harness.token,
        manifest=_manifest(task_id=TASK_B),
    ).data
    _assert_problem(
        "ROBOT_IDENTITY_MISMATCH",
        lambda: harness.service.create_upload(
            token=harness.token,
            manifest=_manifest(task_id=TASK_B, robot_id="wrong-robot"),
        ),
    )
    project_a = harness.service.list_robot_uploads(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        region_code=REGION,
        robot_id=ROBOT,
    )
    attempts_a = harness.service.list_attempts(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        region_code=REGION,
        robot_id=ROBOT,
    )
    assert [item.upload_id for item in project_a.items] == [upload_a.upload_id]
    assert upload_b.upload_id not in {item.upload_id for item in attempts_a.items}
    assert not any(
        item.failure_code == "ROBOT_IDENTITY_MISMATCH" and item.collection_task_id == TASK_B
        for item in attempts_a.items
    )


def test_policy_and_sensitive_manifest_rejections_are_auditable() -> None:
    harness = Harness()
    identity = harness.repository.get_identity(ORG, harness.identity_id)
    assert identity is not None
    harness.service.update_identity(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=harness.identity_id,
        command=UpdateRobotIngestIdentity(
            allowed_transports=identity.allowed_transports,
            allowed_formats=identity.allowed_formats,
            upload_policy=identity.upload_policy.model_copy(update={"max_assets": 1}),
        ),
    )
    body_a = b"policy-a"
    body_b = b"policy-b"
    _assert_problem(
        "ROBOT_INGEST_ASSET_LIMIT_EXCEEDED",
        lambda: harness.service.create_upload(
            token=harness.token,
            manifest=_manifest(
                assets=(
                    _asset("a", "a.bin", body_a),
                    _asset("b", "b.bin", body_b),
                )
            ),
        ),
    )
    attempts = harness.service.list_attempts(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        region_code=REGION,
        robot_id=ROBOT,
    )
    assert any(item.failure_code == "ROBOT_INGEST_ASSET_LIMIT_EXCEEDED" for item in attempts.items)
    with pytest.raises(ValueError, match="credential material"):
        _manifest(format_metadata={"nested": {"access_token": "must-not-persist"}})
    with pytest.raises(ValueError, match="credential material"):
        _manifest(
            format_metadata={
                "nested": [{"clientSecret": "must-not-persist"}, {"bearerToken": "hidden"}]
            }
        )
    with pytest.raises(ValueError, match="asset paths must be unique"):
        _manifest(
            assets=(
                _asset("one", "capture/shared.bin", b"one"),
                _asset("two", "capture/shared.bin", b"two"),
            )
        )


def test_resume_lists_uploaded_parts_skips_them_and_can_reauthorize_missing_parts() -> None:
    harness = Harness()
    client_upload_id = str(uuid4())
    manifest = _manifest(client_upload_id=client_upload_id)
    created = harness.service.create_upload(token=harness.token, manifest=manifest).data
    first_grant = harness.service.authorize_parts(
        token=harness.token,
        upload_id=created.upload_id,
        asset_id="raw",
        command=AuthorizePartsCommand(part_numbers=(1, 2)),
    )
    assert [item.part_number for item in first_grant.authorizations] == [1, 2]
    harness.storage.upload_part(created.assets[0].multipart_upload_id, 1, b"part-one")
    resumed = harness.service.create_upload(token=harness.token, manifest=manifest)
    assert resumed.resumed is True
    resumed_with_scope_hints = harness.service.create_upload(
        token=harness.token,
        manifest=manifest.model_copy(update={"organization_id": ORG, "project_id": PROJECT_A}),
    )
    assert resumed_with_scope_hints.data.upload_id == created.upload_id
    second_grant = harness.service.authorize_parts(
        token=harness.token,
        upload_id=created.upload_id,
        asset_id="raw",
        command=AuthorizePartsCommand(part_numbers=(1, 2)),
    )
    assert [item.part_number for item in second_grant.uploaded_parts] == [1]
    assert [item.part_number for item in second_grant.authorizations] == [2]
    harness.now = first_grant.authorizations[1].expires_at + timedelta(seconds=1)
    third_grant = harness.service.authorize_parts(
        token=harness.token,
        upload_id=created.upload_id,
        asset_id="raw",
        command=AuthorizePartsCommand(part_numbers=(2,)),
    )
    assert len(third_grant.authorizations) == 1
    assert third_grant.authorizations[0].expires_at > first_grant.authorizations[1].expires_at


def test_pause_resume_cancel_and_optional_checksum_policy_are_enforced() -> None:
    harness = Harness()
    manifest = _manifest()
    created = harness.service.create_upload(token=harness.token, manifest=manifest).data
    paused = harness.service.pause_upload(token=harness.token, upload_id=created.upload_id)
    assert paused.data.state.value == "PAUSED"
    _assert_problem(
        "ROBOT_INGEST_UPLOAD_STATE_INVALID",
        lambda: harness.service.authorize_parts(
            token=harness.token,
            upload_id=created.upload_id,
            asset_id="raw",
            command=AuthorizePartsCommand(part_numbers=(1,)),
        ),
    )
    resumed = harness.service.resume_upload(token=harness.token, upload_id=created.upload_id)
    assert resumed.data.state.value == "UPLOADING"
    cancelled = harness.service.cancel_upload(token=harness.token, upload_id=created.upload_id)
    assert cancelled.data.state.value == "CANCELLED"
    assert harness.service.cancel_upload(token=harness.token, upload_id=created.upload_id).resumed

    harness.service.update_identity(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=harness.identity_id,
        command=UpdateRobotIngestIdentity(
            allowed_transports=("HTTPS",),
            allowed_formats=("MCAP",),
            upload_policy=RobotIngestUploadPolicy(
                require_sha256=False,
                require_crc64=False,
            ),
        ),
    )
    # The current credential resolves the identity's live policy, so a policy update
    # does not require rotating the robot secret.
    body = b"robot-raw-data"
    declared = _asset("raw", "capture/policy.bin", body).model_copy(
        update={"sha256": "0" * 64, "crc64": (crc64_ecma(body) + 1) % 2**64}
    )
    policy_upload = harness.service.create_upload(
        token=harness.token,
        manifest=_manifest(assets=(declared,)),
    ).data
    part = harness.storage.upload_part(policy_upload.assets[0].multipart_upload_id, 1, body)
    completed = harness.service.complete_asset(
        token=harness.token,
        upload_id=policy_upload.upload_id,
        asset_id="raw",
        command=CompleteAssetCommand(parts=(CompletedPart(part_number=1, etag=part.etag),)),
    )
    assert completed.data.state.value == "READY_TO_COMMIT"


def test_upload_session_retention_expires_and_aborts_unfinished_multipart() -> None:
    harness = Harness()
    created = harness.service.create_upload(token=harness.token, manifest=_manifest()).data
    harness.now = created.expires_at
    _assert_problem(
        "UPLOAD_SESSION_EXPIRED",
        lambda: harness.service.get_upload(token=harness.token, upload_id=created.upload_id),
    )
    failed = harness.repository.get_upload(ORG, created.upload_id, harness.identity_id)
    assert failed is not None
    assert failed.state.value == "FAILED"
    assert failed.assets[0].state.value == "FAILED"
    attempts = harness.repository.list_attempts(organization_id=ORG, robot_id=ROBOT)
    assert any(item.failure_code == "UPLOAD_SESSION_EXPIRED" for item in attempts)


@pytest.mark.parametrize(
    ("field", "expected_code"),
    (
        ("size", "ASSET_SIZE_MISMATCH"),
        ("sha", "ASSET_SHA256_MISMATCH"),
        ("crc", "ASSET_CRC64_MISMATCH"),
    ),
)
def test_integrity_mismatches_reject_asset_commit(field: str, expected_code: str) -> None:
    harness = Harness()
    body = b"robot-raw-data"
    asset = _asset("raw", "capture/raw.bin", body)
    updates: dict[str, object] = {}
    if field == "size":
        updates["size_bytes"] = len(body) + 1
    elif field == "sha":
        updates["sha256"] = "0" * 64
    else:
        updates["crc64"] = (crc64_ecma(body) + 1) % 2**64
    manifest = _manifest(assets=(asset.model_copy(update=updates),))
    created = harness.service.create_upload(token=harness.token, manifest=manifest).data
    part = harness.storage.upload_part(created.assets[0].multipart_upload_id, 1, body)
    _assert_problem(
        expected_code,
        lambda: harness.service.complete_asset(
            token=harness.token,
            upload_id=created.upload_id,
            asset_id="raw",
            command=CompleteAssetCommand(parts=(CompletedPart(part_number=1, etag=part.etag),)),
        ),
    )


def test_custom_adapter_uses_common_transport_and_attempts_statistics_are_queryable() -> None:
    harness = Harness()
    unregistered = _manifest(
        source_format="CUSTOM_V1",
        format_metadata={"adapter_name": "customer_schema_v1"},
    )
    _assert_problem(
        "ROBOT_INGEST_ADAPTER_NOT_CONFIGURED",
        lambda: harness.service.create_upload(token=harness.token, manifest=unregistered),
    )
    harness.service.adapters.register("CUSTOM_V1", CustomFormatAdapter("customer_schema_v1"))
    upload_id = _complete_first_asset(
        harness,
        _manifest(
            source_format="CUSTOM_V1",
            format_metadata={
                "adapter_name": "customer_schema_v1",
                "verified_episode_count": 2,
                "verified_sample_count": 120,
            },
            declared_episode_count=2,
        ),
    )
    committed = harness.service.commit_upload(token=harness.token, upload_id=upload_id).data
    assert committed.verified_episode_count is None
    assert committed.verified_sample_count == 0
    harness.service.apply_processing_result(
        organization_id=ORG,
        upload_id=upload_id,
        ingest_identity_id=harness.identity_id,
        verified_episode_count=2,
        verified_frame_count=200,
        verified_sample_count=120,
        qc_pass_episode_count=1,
        qc_risk_episode_count=1,
        quality_status=QualityStatus.RISK,
    )
    _assert_problem(
        "ROBOT_IDENTITY_MISMATCH",
        lambda: harness.service.create_upload(
            token=harness.token, manifest=_manifest(robot_id="wrong-robot")
        ),
    )
    attempts = harness.service.list_attempts(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        region_code=REGION,
        robot_id=ROBOT,
    )
    assert any(item.failure_code == "ROBOT_IDENTITY_MISMATCH" for item in attempts.items)
    assert any(
        item.failure_code == "ROBOT_INGEST_ADAPTER_NOT_CONFIGURED" for item in attempts.items
    )
    statistics = harness.service.statistics(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        region_code=REGION,
        robot_id=ROBOT,
    )
    assert committed.raw_source_id is not None
    assert statistics.committed_raw_count == 1
    assert statistics.qc_pass_count == 1
    assert statistics.qc_risk_count == 1
    assert statistics.evaluated_episode_count == 2
    assert statistics.qualified_rate == 0.5
    assert statistics.frame_count == 200
    assert statistics.sample_count == 120
    assert statistics.technical_failure_count == 2
    filtered = harness.service.statistics(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        region_code=REGION,
        robot_id=ROBOT,
        source_format="MCAP",
        created_from=NOW,
        created_to=NOW + timedelta(days=1),
    )
    assert filtered.committed_raw_count == 0


def test_durable_raw_commit_survives_a_synchronous_processing_start_failure() -> None:
    class FailingStartAdapter(CustomFormatAdapter):
        def start_processing(self, raw_source_id: str) -> dict[str, object]:
            del raw_source_id
            raise RuntimeError("temporary worker dispatch failure")

    harness = Harness()
    harness.service.adapters.register("CUSTOM_V1", FailingStartAdapter("customer_schema_v1"))
    upload_id = _complete_first_asset(
        harness,
        _manifest(
            source_format="CUSTOM_V1",
            format_metadata={"adapter_name": "customer_schema_v1"},
        ),
    )
    committed = harness.service.commit_upload(token=harness.token, upload_id=upload_id).data
    assert committed.state.value == "COMMITTED"
    assert committed.raw_source_id is not None
    attempts = harness.repository.list_attempts(
        organization_id=ORG,
        project_id=PROJECT_A,
        region_code=REGION,
        robot_id=ROBOT,
    )
    assert any(
        item.failure_code == "ROBOT_INGEST_PROCESSING_START_FAILED"
        and item.raw_source_id == committed.raw_source_id
        for item in attempts
    )


@pytest.mark.parametrize(
    ("stage", "failure_code"),
    (
        ("preflight", "ROBOT_INGEST_ADAPTER_PREFLIGHT_FAILED"),
        ("verification", "ROBOT_INGEST_ADAPTER_VERIFICATION_FAILED"),
        ("normalization", "ROBOT_INGEST_ADAPTER_NORMALIZATION_FAILED"),
    ),
)
def test_unexpected_adapter_failures_are_safe_auditable_and_retryable(
    stage: str,
    failure_code: str,
) -> None:
    class UnexpectedFailureAdapter(CustomFormatAdapter):
        def preflight_manifest(self, manifest: RobotIngestUploadManifest) -> None:
            if stage == "preflight":
                raise RuntimeError("private adapter preflight detail")
            super().preflight_manifest(manifest)

        def verify_assets(
            self,
            manifest: RobotIngestUploadManifest,
            assets: tuple[RobotIngestAsset, ...],
        ) -> AdapterVerification:
            if stage == "verification":
                raise RuntimeError("private adapter verification detail")
            return super().verify_assets(manifest, assets)

        def normalize_raw_source(self, manifest: RobotIngestUploadManifest) -> dict[str, object]:
            if stage == "normalization":
                raise RuntimeError("private adapter normalization detail")
            return super().normalize_raw_source(manifest)

    harness = Harness()
    harness.service.adapters.register("CUSTOM_V1", UnexpectedFailureAdapter("customer_schema_v1"))
    manifest = _manifest(
        source_format="CUSTOM_V1",
        format_metadata={"adapter_name": "customer_schema_v1"},
    )
    if stage == "preflight":

        def action() -> object:
            return harness.service.create_upload(token=harness.token, manifest=manifest)
    else:
        upload_id = _complete_first_asset(harness, manifest)

        def action() -> object:
            return harness.service.commit_upload(token=harness.token, upload_id=upload_id)

    _assert_problem(failure_code, action)
    attempts = harness.repository.list_attempts(
        organization_id=ORG,
        project_id=PROJECT_A,
        region_code=REGION,
        robot_id=ROBOT,
    )
    assert any(item.failure_code == failure_code for item in attempts)
