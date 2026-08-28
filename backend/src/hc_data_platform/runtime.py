"""Production composition root shared by the API and Temporal Worker processes."""

from __future__ import annotations

import importlib
import json
import os
import socket
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlparse

from pydantic import TypeAdapter

from hc_data_platform.alignment.arrow_writer import ArrowFragmentWriter
from hc_data_platform.alignment.canonical import denormalize_from_json
from hc_data_platform.alignment.engine import AlignmentEngine
from hc_data_platform.alignment.models import AlignedFragmentManifestV1, AlignedValueV1
from hc_data_platform.alignment.postgres import PostgresAlignmentRepository
from hc_data_platform.annotation.auto_jobs import (
    AutoAnnotationJobService,
    AutoAnnotationOutboxHandler,
    HttpAutoAnnotationProvider,
    PostgresAutoAnnotationJobRepository,
    PostgresAutoAnnotationSamplingRepository,
)
from hc_data_platform.annotation.automation import (
    AutomaticAnnotationTaskService,
    PostgresAutomaticAnnotationRepository,
)
from hc_data_platform.annotation.postgres import PostgresAnnotationRepository
from hc_data_platform.annotation.router import configure_annotation
from hc_data_platform.annotation.service import AnnotationService
from hc_data_platform.audit_projection.governance import (
    AuditExportOutboxHandler,
    AuditGovernanceService,
    PostgresAuditGovernanceRepository,
    S3AuditArtifactStore,
)
from hc_data_platform.audit_projection.repository import PostgresAuditProjectionRepository
from hc_data_platform.audit_projection.router import configure_audit_projection
from hc_data_platform.audit_projection.service import AuditProjectionService
from hc_data_platform.calibrations.repository import PostgresCalibrationRepository
from hc_data_platform.calibrations.router import configure_calibrations
from hc_data_platform.calibrations.service import CalibrationService
from hc_data_platform.cleaning_drafts.repository import PostgresCleaningDraftRepository
from hc_data_platform.cleaning_drafts.router import configure_cleaning_drafts
from hc_data_platform.cleaning_drafts.service import CleaningDraftService
from hc_data_platform.cleaning_workbench.models import (
    CommitAcceptedEnvelope,
    PreviewAcceptedEnvelope,
)
from hc_data_platform.cleaning_workbench.repository import PostgresCleaningWorkbenchRepository
from hc_data_platform.cleaning_workbench.router import configure_cleaning_workbench
from hc_data_platform.cleaning_workbench.service import CleaningWorkbenchService
from hc_data_platform.collection_tasks.models import CollectionTaskRecord
from hc_data_platform.collection_tasks.postgres import PostgresCollectionTaskRepository
from hc_data_platform.collection_tasks.router import configure_collection_tasks
from hc_data_platform.collection_tasks.service import CollectionTaskService
from hc_data_platform.core.config import Settings, get_settings
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.dashboard.postgres import PostgresDashboardRepository
from hc_data_platform.dashboard.router import configure_dashboard
from hc_data_platform.dashboard.service import DashboardService
from hc_data_platform.data_schemas.repository import PostgresDataSchemaRepository
from hc_data_platform.data_schemas.router import configure_data_schemas
from hc_data_platform.data_schemas.service import DataSchemaService
from hc_data_platform.data_sources.models import DataSourceMutationRecord
from hc_data_platform.data_sources.repository import PostgresDataSourceRepository
from hc_data_platform.data_sources.router import configure_data_sources
from hc_data_platform.data_sources.service import DataSourceService
from hc_data_platform.dataset_registry.ingest_projection import PostgresDatasetIngestProjector
from hc_data_platform.dataset_registry.models import (
    DatasetPageApproveReviewMutationRecord,
    DatasetPageAsyncJobMutationRecord,
    DatasetPageMutationRecord,
    DatasetPageReturnReviewMutationRecord,
)
from hc_data_platform.dataset_registry.repository import PostgresDatasetPageRepository
from hc_data_platform.dataset_registry.router import configure_dataset_page
from hc_data_platform.dataset_registry.service import DatasetPageService
from hc_data_platform.ingest.adapters import S3ObjectStorage
from hc_data_platform.ingest.device_facts import (
    DeviceCaptureFactService,
    PostgresDeviceCaptureFactRepository,
)
from hc_data_platform.ingest.manifest import ObjectStorageManifestParser
from hc_data_platform.ingest.postgres import PostgresIngestPersistence
from hc_data_platform.ingest.router import configure_device_capture_facts, configure_ingest_service
from hc_data_platform.ingest.service import UploadSessionService
from hc_data_platform.lance_catalog.adapters import (
    LanceAdapter,
    PostgresAdvisoryDatasetLock,
    PostgresCatalogAdapter,
)
from hc_data_platform.lance_catalog.audit import (
    LanceCatalogAuditRecorder,
    PostgresLanceCatalogAuditRecorder,
)
from hc_data_platform.lance_catalog.models import (
    AlignedFragmentManifestV1 as CatalogFragmentManifestV1,
)
from hc_data_platform.lance_catalog.models import StepRecord
from hc_data_platform.lance_catalog.router import (
    configure_lance_catalog,
    configure_lance_catalog_audit_recorder,
)
from hc_data_platform.lance_catalog.service import LanceCatalogService, compute_fragment_hash
from hc_data_platform.manual_cleaning.models import (
    ManualIssueDraftMutationRecord,
    ManualIssueMutationRecord,
)
from hc_data_platform.manual_cleaning.repository import PostgresManualIssueRepository
from hc_data_platform.manual_cleaning.router import configure_manual_issues
from hc_data_platform.manual_cleaning.service import ManualIssueService
from hc_data_platform.preview.adapters import (
    AnnotationExclusionAdapter,
    FFmpegHlsEncoder,
    LanceStepReaderAdapter,
    S3ImageRefResolver,
)
from hc_data_platform.preview.artifact_store import S3PreviewArtifactStore
from hc_data_platform.preview.audit import PostgresPreviewAuditRecorder, PreviewAuditRecorder
from hc_data_platform.preview.gc import PreviewGarbageCollector
from hc_data_platform.preview.memory import HmacUrlSigner
from hc_data_platform.preview.postgres import PostgresPreviewRepository
from hc_data_platform.preview.profiles import PreviewProfileCatalog
from hc_data_platform.preview.router import (
    configure_preview_audit_recorder,
    configure_preview_service,
)
from hc_data_platform.preview.service import (
    PreviewControlPlaneService,
    PreviewGenerationService,
)
from hc_data_platform.preview.temporal_queue import TemporalPreviewJobQueue
from hc_data_platform.publishing.adapters import (
    ApprovedAnnotationSnapshotAdapter,
    CatalogSnapshotAdapter,
    StepReaderAdapter,
)
from hc_data_platform.publishing.audit import PostgresExportAuditRecorder
from hc_data_platform.publishing.exporters import LanceSnapshotExporter, LeRobotV3Exporter
from hc_data_platform.publishing.postgres import (
    PostgresAnnotationTaskLocator,
    PostgresCatalogRolloutState,
    PostgresPublishedManifestRepository,
)
from hc_data_platform.publishing.router import (
    configure_dataset_publisher,
    configure_export_audit_recorder,
    configure_export_coordinator,
)
from hc_data_platform.publishing.s3 import S3ArtifactSink
from hc_data_platform.publishing.service import DatasetPublisher, ExportCoordinator
from hc_data_platform.quality.engine import QualityEngine
from hc_data_platform.quality.postgres import PostgresQualityRepository
from hc_data_platform.quality.router import configure_quality_repository
from hc_data_platform.registry.repository import PostgresRegistryRepository
from hc_data_platform.registry.router import configure_registry
from hc_data_platform.registry.service import RegistryService
from hc_data_platform.robotics.repository import PostgresRoboticsRepository
from hc_data_platform.robotics.router import configure_robotics
from hc_data_platform.robotics.service import RoboticsService
from hc_data_platform.security.abuse import PostgresAbuseProtection, policy_from_settings
from hc_data_platform.security.access_postgres import PostgresAccessRepository
from hc_data_platform.security.access_service import AccessService
from hc_data_platform.security.admin_accounts import AdminAccountService
from hc_data_platform.security.challenge import challenge_verifier_from_settings
from hc_data_platform.security.outbox import (
    OutboxDispatcher,
    PostgresOutboxDeliveryRepository,
)
from hc_data_platform.security.passwords import PasswordHasher, PasswordPolicy, ScryptParameters
from hc_data_platform.security.psycopg import PsycopgIdempotencyStore
from hc_data_platform.security.recovery import (
    AccountRecoveryService,
    recovery_delivery_from_settings,
)
from hc_data_platform.storage.dispatch import (
    RepositoryStorageExecutionInputResolver,
    RepositoryStorageScheduleEnqueuer,
    StorageLifecycleOutboxHandler,
    StorageLifecycleScheduleOutboxHandler,
    StorageScheduleEnqueuer,
)
from hc_data_platform.storage.executor import PostgresLifecycleBatchExecutor
from hc_data_platform.storage.inventory import (
    PostgresStorageInventoryCatalog,
    S3StorageInventoryProvider,
    StorageInventorySnapshotProducer,
)
from hc_data_platform.storage.inventory_worker import StorageInventoryRuntime
from hc_data_platform.storage.object_store import S3StorageObjectOperator
from hc_data_platform.storage.postgres import (
    PostgresStorageIdempotencyStore,
    PostgresStorageRepository,
    StorageTransactionConnectionFactory,
)
from hc_data_platform.storage.router import configure_storage_governance
from hc_data_platform.storage.service import StorageGovernanceService
from hc_data_platform.verification.engine import McapVerifier
from hc_data_platform.verification.ports import (
    ChunkedObjectStorageReader,
    CompositeDecoderProbe,
    DecoderProbe,
    McapRos2DecoderProbe,
    RegisteredDecoderProbe,
)
from hc_data_platform.verification.postgres import PostgresVerificationRepository
from hc_data_platform.verification.router import configure_verification_repository
from hc_data_platform.workflow.activities import ActivityDependencies
from hc_data_platform.workflow.ingest_dispatch import IngestOutboxHandler
from hc_data_platform.workflow.ingest_plan import PostgresIngestWorkflowInputResolver
from hc_data_platform.workflow.models import (
    AlignmentActivityInput,
    CatalogFragmentPayloadV1,
)
from hc_data_platform.workflow.postgres import PostgresWorkflowJobRepository
from hc_data_platform.workflow.projection_store import (
    S3ProjectionArtifactStore,
    S3ProjectionStagingSweeper,
)
from hc_data_platform.workflow.service import TemporalWorkflowLauncher
from hc_data_platform.workflow.worker import DEFAULT_TASK_QUEUE

_DATASET_PAGE_IDEMPOTENCY_RESPONSE: TypeAdapter[
    DatasetPageMutationRecord
    | DatasetPageApproveReviewMutationRecord
    | DatasetPageReturnReviewMutationRecord
    | DatasetPageAsyncJobMutationRecord
] = TypeAdapter(
    DatasetPageMutationRecord
    | DatasetPageApproveReviewMutationRecord
    | DatasetPageReturnReviewMutationRecord
    | DatasetPageAsyncJobMutationRecord
)

_MANUAL_ISSUE_IDEMPOTENCY_RESPONSE: TypeAdapter[
    ManualIssueMutationRecord | ManualIssueDraftMutationRecord
] = TypeAdapter(ManualIssueMutationRecord | ManualIssueDraftMutationRecord)

_CLEANING_WORKBENCH_IDEMPOTENCY_RESPONSE: TypeAdapter[
    PreviewAcceptedEnvelope | CommitAcceptedEnvelope
] = TypeAdapter(PreviewAcceptedEnvelope | CommitAcceptedEnvelope)


class ArrowFragmentWriterFactory:
    def __init__(self, root: Path) -> None:
        self._root = root

    def create(self, request: AlignmentActivityInput) -> ArrowFragmentWriter:
        del request
        return ArrowFragmentWriter(self._root)


class ArrowCatalogFragmentAdapter:
    """Reload a committed Arrow attempt and preserve every alignment provenance value."""

    def __init__(self, catalog_repository: PostgresCatalogAdapter) -> None:
        self._catalog_repository = catalog_repository

    def prepare(
        self, request: AlignmentActivityInput, manifest: AlignedFragmentManifestV1
    ) -> CatalogFragmentPayloadV1:
        snapshot = self._catalog_repository.schema_for(request.project_id, request.dataset_id)
        if snapshot is None or snapshot.schema_snapshot_id != request.schema_snapshot_id:
            raise KeyError((request.project_id, request.dataset_id, request.schema_snapshot_id))
        rows = self._read_rows(manifest.staging_uri)
        steps = tuple(
            StepRecord(
                rollout_id=str(row["rollout_id"]),
                step_index=int(row["step_index"]),
                timestamp_ns=int(row["timestamp_ns"]),
                modalities={name: value.value for name, value in row["modalities"].items()},
                source_timestamps_ns={
                    name: value.source_timestamps_ns for name, value in row["modalities"].items()
                },
                time_error_ns={
                    name: value.time_error_ns for name, value in row["modalities"].items()
                },
                valid={name: value.valid for name, value in row["modalities"].items()},
                repeated={name: value.repeated for name, value in row["modalities"].items()},
                sample_valid=bool(row["sample_valid"]),
            )
            for row in rows
        )
        catalog_manifest = CatalogFragmentManifestV1(
            project_id=request.project_id,
            dataset_id=request.dataset_id,
            schema_snapshot_id=request.schema_snapshot_id,
            schema_fingerprint=snapshot.fingerprint,
            frequency_hz=manifest.frequency_hz,
            rollout_id=manifest.rollout_id,
            source_sha256=manifest.source_sha256,
            converter_version=manifest.converter_version,
            attempt_id=manifest.attempt_id,
            fragment_uri=manifest.staging_uri,
            step_count=len(steps),
            content_hash=compute_fragment_hash(steps),
        )
        return CatalogFragmentPayloadV1(manifest=catalog_manifest, steps=steps)

    @staticmethod
    def _read_rows(uri: str) -> list[dict[str, Any]]:
        parsed = urlparse(uri)
        if parsed.scheme != "file":
            raise ValueError("the Arrow fragment adapter requires a committed file URI")
        import pyarrow as pa
        import pyarrow.ipc as ipc

        path = unquote(parsed.path)
        with pa.memory_map(path, "r") as source:
            raw_rows = ipc.open_file(source).read_all().to_pylist()
        rows: list[dict[str, Any]] = []
        for raw in raw_rows:
            encoded = raw["modalities_json"]
            payload = denormalize_from_json(json.loads(bytes(encoded).decode("utf-8")))
            if not isinstance(payload, dict):
                raise ValueError("aligned fragment modalities must be a JSON object")
            rows.append(
                {
                    **raw,
                    "modalities": {
                        name: AlignedValueV1.model_validate(value)
                        for name, value in payload.items()
                    },
                }
            )
        return rows


class PublicationReconciler:
    def __init__(self, publisher: DatasetPublisher) -> None:
        self._publisher = publisher

    def reconcile(self, request: Any) -> Any:
        return self._publisher.publish(request)


@dataclass(frozen=True)
class RuntimeComponents:
    access: AccessService
    admin_accounts: AdminAccountService
    recovery: AccountRecoveryService
    audit_projection: AuditProjectionService
    audit_governance: AuditGovernanceService
    dashboard: DashboardService
    ingest: UploadSessionService
    device_capture_facts: DeviceCaptureFactService
    collection_tasks: CollectionTaskService
    storage: StorageGovernanceService
    registry: RegistryService
    robotics: RoboticsService
    calibrations: CalibrationService
    data_schemas: DataSchemaService
    data_sources: DataSourceService
    dataset_page: DatasetPageService
    manual_issues: ManualIssueService
    cleaning_drafts: CleaningDraftService
    cleaning_workbench: CleaningWorkbenchService
    annotation: AnnotationService
    auto_annotation_jobs: AutoAnnotationJobService
    catalog: LanceCatalogService
    catalog_audit: LanceCatalogAuditRecorder
    verification_repository: PostgresVerificationRepository
    quality_repository: PostgresQualityRepository
    alignment_repository: PostgresAlignmentRepository
    preview: PreviewControlPlaneService
    preview_audit: PreviewAuditRecorder
    publisher: DatasetPublisher
    exporter: ExportCoordinator
    export_audit: PostgresExportAuditRecorder
    activities: ActivityDependencies


@dataclass(frozen=True)
class WorkerOutboxRuntime:
    dispatcher: OutboxDispatcher
    scopes: tuple[str, ...]
    poll_interval_seconds: float
    batch_size: int
    schedule_enqueuer: StorageScheduleEnqueuer


def _s3(settings: Settings) -> tuple[Any, Any, S3ObjectStorage]:
    import boto3
    from botocore.config import Config

    client_options = {
        "aws_access_key_id": settings.object_store_access_key,
        "aws_secret_access_key": settings.object_store_secret_key,
        "region_name": settings.object_store_region,
        "config": Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    }
    operation_client = boto3.client(
        "s3",
        endpoint_url=settings.object_store_endpoint,
        **client_options,
    )
    public_endpoint = settings.object_store_public_endpoint
    if public_endpoint is None:  # Defensive: Settings resolves local/test and rejects prod absence.
        raise RuntimeError("public object-store endpoint is not configured")
    presign_client = boto3.client(
        "s3",
        endpoint_url=public_endpoint,
        **client_options,
    )
    return (
        operation_client,
        presign_client,
        S3ObjectStorage(
            operation_client,
            settings.object_store_bucket,
            presign_client=presign_client,
        ),
    )


def _decoder(settings: Settings) -> DecoderProbe:
    if settings.mcap_decoder_factory:
        module_name, separator, attribute = settings.mcap_decoder_factory.partition(":")
        if not separator:
            raise RuntimeError("HC_MCAP_DECODER_FACTORY must use package.module:factory")
        factory = getattr(importlib.import_module(module_name), attribute)
        return cast(DecoderProbe, factory())

    def decode_json(_schema: bytes, message: bytes) -> object:
        return json.loads(message)

    return CompositeDecoderProbe(
        (
            RegisteredDecoderProbe({("json", "jsonschema"): decode_json}),
            McapRos2DecoderProbe(),
        )
    )


def build_runtime(
    settings: Settings | None = None, *, include_media: bool = False
) -> RuntimeComponents:
    resolved = settings or get_settings()
    connection_factory = psycopg_connection_factory(resolved.postgres_dsn)
    hmac_secret = resolved.auth_abuse_hmac_secret
    if resolved.auth_abuse_enabled and hmac_secret is None:
        raise RuntimeError("auth abuse HMAC secret is not configured")
    access_repository = PostgresAccessRepository.from_dsn(
        resolved.postgres_dsn,
        session_idle_ttl_seconds=resolved.session_idle_ttl_seconds,
        session_absolute_ttl_seconds=resolved.session_absolute_ttl_seconds,
        session_touch_interval_seconds=resolved.session_touch_interval_seconds,
        max_active_sessions=resolved.max_active_sessions,
    )
    password_hasher = PasswordHasher(
        ScryptParameters(
            n=resolved.password_scrypt_n,
            r=resolved.password_scrypt_r,
            p=resolved.password_scrypt_p,
        )
    )
    password_policy = PasswordPolicy(
        min_length=resolved.password_min_length,
        max_length=resolved.password_max_length,
    )
    abuse_protection = (
        PostgresAbuseProtection.from_dsn(
            resolved.postgres_dsn,
            hmac_secret=hmac_secret.get_secret_value(),
            policy=policy_from_settings(resolved),
        )
        if resolved.auth_abuse_enabled and hmac_secret is not None
        else None
    )
    challenge_verifier = challenge_verifier_from_settings(resolved)
    access = AccessService(
        access_repository,
        password_hasher=password_hasher,
        password_policy=password_policy,
        abuse_protection=abuse_protection,
        challenge_verifier=challenge_verifier,
    )
    admin_accounts = AdminAccountService(
        access_repository,
        password_hasher=password_hasher,
        password_policy=password_policy,
    )
    recovery = AccountRecoveryService(
        access_repository,
        delivery=recovery_delivery_from_settings(resolved),
        password_hasher=password_hasher,
        password_policy=password_policy,
        abuse_protection=abuse_protection,
        challenge_verifier=challenge_verifier,
        email_verification_ttl_seconds=resolved.auth_recovery_email_verification_ttl_seconds,
        password_recovery_ttl_seconds=resolved.auth_recovery_token_ttl_seconds,
    )
    audit_governance_repository = PostgresAuditGovernanceRepository(connection_factory)
    audit_projection = AuditProjectionService(
        PostgresAuditProjectionRepository(connection_factory),
        cursor_secret=resolved.cursor_secret,
        retention_policy_provider=audit_governance_repository.get_policy,
        legal_hold_provider=audit_governance_repository.list_holds,
    )
    dashboard = DashboardService(
        PostgresDashboardRepository(connection_factory),
        cursor_secret=resolved.cursor_secret,
    )
    s3_client, s3_presign_client, object_storage = _s3(resolved)
    audit_governance = AuditGovernanceService(
        audit_governance_repository,
        audit_projection,
        S3AuditArtifactStore(
            s3_client,
            resolved.object_store_bucket,
            prefix=f"{resolved.artifact_prefix}/audit-exports",
            presign_client=s3_presign_client,
        ),
    )

    ingest = UploadSessionService(
        object_storage,
        persistence=PostgresIngestPersistence(connection_factory),
        authorization_ttl_seconds=resolved.ingest_part_authorization_ttl_seconds,
        cursor_secret=resolved.cursor_secret,
    )
    collection_tasks = CollectionTaskService(
        PostgresCollectionTaskRepository(connection_factory),
        PsycopgIdempotencyStore(
            connection_factory,
            response_decoder=CollectionTaskRecord.model_validate,
        ),
        cursor_secret=resolved.cursor_secret,
    )
    storage_connection_factory = StorageTransactionConnectionFactory(connection_factory)
    storage_object_operator = S3StorageObjectOperator(
        s3_client,
        resolved.object_store_bucket,
        presign_client=s3_presign_client,
    )
    storage = StorageGovernanceService(
        PostgresStorageRepository(storage_connection_factory),
        cursor_secret=resolved.cursor_secret,
        idempotency=PostgresStorageIdempotencyStore(
            connection_factory,
            storage_connection_factory,
        ),
        object_operator=storage_object_operator,
    )
    registry = RegistryService(
        PostgresRegistryRepository(connection_factory),
        storage=object_storage,
        cursor_secret=resolved.cursor_secret,
    )
    robotics = RoboticsService(
        PostgresRoboticsRepository(connection_factory),
        cursor_secret=resolved.cursor_secret,
    )
    calibrations = CalibrationService(
        PostgresCalibrationRepository(connection_factory),
        cursor_secret=resolved.cursor_secret,
    )
    data_schemas = DataSchemaService(
        PostgresDataSchemaRepository(connection_factory),
        cursor_secret=resolved.cursor_secret,
    )
    data_sources = DataSourceService(
        PostgresDataSourceRepository(connection_factory),
        cursor_secret=resolved.cursor_secret,
        credential_key=resolved.data_source_credential_key.get_secret_value(),
        idempotency=PsycopgIdempotencyStore(
            connection_factory,
            response_decoder=DataSourceMutationRecord.model_validate,
        ),
    )
    dataset_page = DatasetPageService(
        PostgresDatasetPageRepository(connection_factory),
        cursor_secret=resolved.cursor_secret,
        idempotency=PsycopgIdempotencyStore(
            connection_factory,
            response_decoder=_DATASET_PAGE_IDEMPOTENCY_RESPONSE.validate_python,
        ),
    )
    manual_issues = ManualIssueService(
        PostgresManualIssueRepository(connection_factory),
        cursor_secret=resolved.cursor_secret,
        idempotency=PsycopgIdempotencyStore(
            connection_factory,
            response_decoder=_MANUAL_ISSUE_IDEMPOTENCY_RESPONSE.validate_python,
        ),
    )
    cleaning_drafts = CleaningDraftService(
        PostgresCleaningDraftRepository(connection_factory),
        cursor_secret=resolved.cursor_secret,
    )
    cleaning_workbench = CleaningWorkbenchService(
        PostgresCleaningWorkbenchRepository(connection_factory),
        idempotency=PsycopgIdempotencyStore(
            connection_factory,
            response_decoder=_CLEANING_WORKBENCH_IDEMPOTENCY_RESPONSE.validate_python,
        ),
    )
    annotation = AnnotationService(
        PostgresAnnotationRepository(connection_factory),
        cursor_secret=resolved.cursor_secret,
    )
    configured_auto_providers: tuple[HttpAutoAnnotationProvider, ...] = ()
    if resolved.auto_annotation_provider_endpoint is not None:
        api_key = resolved.auto_annotation_provider_api_key
        configured_auto_providers = (
            HttpAutoAnnotationProvider(
                name=resolved.auto_annotation_provider_name,
                endpoint=resolved.auto_annotation_provider_endpoint,
                models=resolved.auto_annotation_provider_models,
                api_key=None if api_key is None else api_key.get_secret_value(),
                timeout_seconds=resolved.auto_annotation_provider_timeout_seconds,
            ),
        )
    auto_annotation_jobs = AutoAnnotationJobService(
        annotation,
        PostgresAutoAnnotationJobRepository(connection_factory),
        configured_auto_providers,
        max_concurrent_jobs_per_project=resolved.auto_annotation_max_concurrent_jobs,
        max_jobs_per_hour=resolved.auto_annotation_max_jobs_per_hour,
        daily_cost_limit_micros=resolved.auto_annotation_daily_cost_limit_micros,
        sampling_repository=PostgresAutoAnnotationSamplingRepository(connection_factory),
        require_sampling_manifest=True,
    )
    automatic_annotation = AutomaticAnnotationTaskService(
        PostgresAutomaticAnnotationRepository(connection_factory)
    )
    verification_repository = PostgresVerificationRepository(connection_factory)
    quality_repository = PostgresQualityRepository(connection_factory)
    alignment_repository = PostgresAlignmentRepository(connection_factory)

    catalog_repository = PostgresCatalogAdapter(connection_factory)
    lance_root = resolved.lance_root_uri or f"s3://{resolved.object_store_bucket}/lance"
    lance_storage = LanceAdapter(
        lance_root,
        object_store_client=s3_client,
        storage_options={
            "aws_endpoint": resolved.object_store_endpoint,
            "aws_access_key_id": resolved.object_store_access_key,
            "aws_secret_access_key": resolved.object_store_secret_key,
            "aws_region": resolved.object_store_region,
            "allow_http": str(resolved.object_store_endpoint.startswith("http://")).lower(),
        },
    )
    catalog = LanceCatalogService(
        lance_storage,
        catalog_repository,
        PostgresAdvisoryDatasetLock(connection_factory),
    )

    preview_repository = PostgresPreviewRepository(connection_factory)
    preview_store = S3PreviewArtifactStore(
        s3_client,
        resolved.object_store_bucket,
        presign_client=s3_presign_client,
    )
    preview_profiles = PreviewProfileCatalog(resolved.preview_allowed_profiles)
    preview_queue = TemporalPreviewJobQueue(
        TemporalWorkflowLauncher(
            resolved.temporal_target,
            namespace=os.getenv("HC_TEMPORAL_NAMESPACE", "default"),
            task_queue=resolved.media_temporal_task_queue,
        )
    )
    preview = PreviewControlPlaneService(
        repository=preview_repository,
        store=preview_store,
        signer=HmacUrlSigner(resolved.cursor_secret.encode()),
        queue=preview_queue,
        profiles=preview_profiles,
        artifact_ttl=timedelta(days=resolved.preview_artifact_ttl_days),
        session_ttl=timedelta(minutes=resolved.preview_session_ttl_minutes),
    )
    preview_generation = (
        PreviewGenerationService(
            step_reader=LanceStepReaderAdapter(
                catalog,
                page_size=512,
                image_ref_resolver=S3ImageRefResolver(
                    s3_client,
                    resolved.object_store_bucket,
                ),
            ),
            exclusions=AnnotationExclusionAdapter(annotation),
            encoder=FFmpegHlsEncoder(
                Path(resolved.preview_cache_root) / "staging",
                profiles=preview_profiles,
                ffmpeg_threads=resolved.media_ffmpeg_threads,
            ),
            repository=preview_repository,
            store=preview_store,
            profiles=preview_profiles,
            artifact_ttl=timedelta(days=resolved.preview_artifact_ttl_days),
        )
        if include_media
        else None
    )
    preview_audit = PostgresPreviewAuditRecorder(connection_factory)
    catalog_audit = PostgresLanceCatalogAuditRecorder(connection_factory)

    artifact_sink = S3ArtifactSink(
        s3_client,
        resolved.object_store_bucket,
        prefix=resolved.artifact_prefix,
        presign_client=s3_presign_client,
    )
    publisher = DatasetPublisher(
        catalog=CatalogSnapshotAdapter(
            catalog=catalog,
            rollout_states=PostgresCatalogRolloutState(connection_factory),
        ),
        annotations=ApprovedAnnotationSnapshotAdapter(
            annotations=annotation,
            exclusions=annotation,
            task_locator=PostgresAnnotationTaskLocator(connection_factory),
        ),
        repository=PostgresPublishedManifestRepository(connection_factory),
        artifact_sink=artifact_sink,
    )
    exporter = ExportCoordinator(
        source=StepReaderAdapter(catalog),
        sink=artifact_sink,
        exporters=(LanceSnapshotExporter(), LeRobotV3Exporter()),
    )
    export_audit = PostgresExportAuditRecorder(connection_factory)
    decoder = _decoder(resolved)
    activities = ActivityDependencies(
        manifest_parser=ObjectStorageManifestParser(object_storage),
        verifier=McapVerifier(
            ChunkedObjectStorageReader(object_storage),
            decoder=decoder,
        ),
        quality=QualityEngine(),
        alignment=AlignmentEngine(),
        ingest_projection=PostgresIngestWorkflowInputResolver(
            connection_factory,
            ChunkedObjectStorageReader(object_storage),
            catalog,
            decoder=decoder,
            projection_store=S3ProjectionArtifactStore(
                s3_client,
                resolved.object_store_bucket,
                Path(resolved.alignment_staging_root) / "projections",
            ),
            projection_staging_root=(
                Path(resolved.alignment_staging_root) / "projections"
            ),
            projection_ttl=timedelta(hours=resolved.preview_staging_ttl_hours),
        ),
        dataset_ingest_projection=PostgresDatasetIngestProjector(connection_factory),
        fragment_writers=ArrowFragmentWriterFactory(Path(resolved.alignment_staging_root)),
        catalog_fragments=ArrowCatalogFragmentAdapter(catalog_repository),
        catalog=catalog,
        preview=preview_generation,
        publisher=publisher,
        exporter=exporter,
        catalog_reconciler=catalog,
        publication_reconciler=PublicationReconciler(publisher),
        verification_reports=verification_repository,
        quality_reports=quality_repository,
        alignment_manifests=alignment_repository,
        annotation_tasks=automatic_annotation,
        storage_lifecycle=PostgresLifecycleBatchExecutor(
            connection_factory,
            storage_object_operator,
        ),
        workflow_jobs=PostgresWorkflowJobRepository(connection_factory),
    )
    return RuntimeComponents(
        access=access,
        admin_accounts=admin_accounts,
        recovery=recovery,
        audit_projection=audit_projection,
        audit_governance=audit_governance,
        dashboard=dashboard,
        ingest=ingest,
        device_capture_facts=DeviceCaptureFactService(
            PostgresDeviceCaptureFactRepository(connection_factory)
        ),
        collection_tasks=collection_tasks,
        storage=storage,
        registry=registry,
        robotics=robotics,
        calibrations=calibrations,
        data_schemas=data_schemas,
        data_sources=data_sources,
        dataset_page=dataset_page,
        manual_issues=manual_issues,
        cleaning_drafts=cleaning_drafts,
        cleaning_workbench=cleaning_workbench,
        annotation=annotation,
        auto_annotation_jobs=auto_annotation_jobs,
        catalog=catalog,
        catalog_audit=catalog_audit,
        verification_repository=verification_repository,
        quality_repository=quality_repository,
        alignment_repository=alignment_repository,
        preview=preview,
        preview_audit=preview_audit,
        publisher=publisher,
        exporter=exporter,
        export_audit=export_audit,
        activities=activities,
    )


def configure_api(runtime: RuntimeComponents) -> None:
    configure_audit_projection(runtime.audit_projection, runtime.audit_governance)
    configure_dashboard(runtime.dashboard)
    configure_ingest_service(runtime.ingest)
    configure_device_capture_facts(runtime.device_capture_facts)
    configure_collection_tasks(runtime.collection_tasks)
    configure_storage_governance(runtime.storage)
    configure_registry(runtime.registry)
    configure_robotics(runtime.robotics)
    configure_calibrations(runtime.calibrations)
    configure_data_schemas(runtime.data_schemas)
    configure_data_sources(runtime.data_sources)
    configure_dataset_page(runtime.dataset_page)
    configure_manual_issues(runtime.manual_issues)
    configure_cleaning_drafts(runtime.cleaning_drafts)
    configure_cleaning_workbench(runtime.cleaning_workbench)
    configure_annotation(runtime.annotation, auto_jobs=runtime.auto_annotation_jobs)
    configure_verification_repository(runtime.verification_repository)
    configure_quality_repository(runtime.quality_repository)
    from hc_data_platform.alignment.router import configure_alignment_repository

    configure_alignment_repository(runtime.alignment_repository)
    configure_lance_catalog(runtime.catalog)
    configure_lance_catalog_audit_recorder(runtime.catalog_audit)
    configure_preview_service(runtime.preview)
    configure_preview_audit_recorder(runtime.preview_audit)
    configure_dataset_publisher(runtime.publisher)
    configure_export_coordinator(runtime.exporter)
    configure_export_audit_recorder(runtime.export_audit)


def activity_dependencies() -> ActivityDependencies:
    """Deployment-owned factory referenced by HC_WORKFLOW_ACTIVITY_FACTORY."""

    return build_runtime().activities


def media_activity_dependencies() -> ActivityDependencies:
    """Media-worker-only factory; the API and main worker never construct FFmpeg."""

    return build_runtime(include_media=True).activities


def build_preview_gc(settings: Settings | None = None) -> PreviewGarbageCollector:
    """Compose exact-manifest preview GC for the main worker's scoped loop."""

    resolved = settings or get_settings()
    connection_factory = psycopg_connection_factory(resolved.postgres_dsn)
    s3_client, _, _ = _s3(resolved)
    return PreviewGarbageCollector(
        PostgresPreviewRepository(connection_factory),
        S3PreviewArtifactStore(s3_client, resolved.object_store_bucket),
        project_quota_bytes=resolved.preview_project_quota_bytes,
        global_quota_bytes=resolved.preview_global_quota_bytes,
        high_watermark_percent=resolved.preview_high_watermark_percent,
        low_watermark_percent=resolved.preview_low_watermark_percent,
    )


def build_projection_staging_sweeper(
    settings: Settings | None = None,
) -> S3ProjectionStagingSweeper:
    resolved = settings or get_settings()
    s3_client, _, _ = _s3(resolved)
    return S3ProjectionStagingSweeper(
        s3_client,
        resolved.object_store_bucket,
        ttl=timedelta(hours=resolved.preview_staging_ttl_hours),
    )


def build_storage_inventory(
    settings: Settings | None = None,
    *,
    scopes: tuple[str, ...] | None = None,
) -> StorageInventoryRuntime:
    """Compose the project catalog, S3 evidence reader, and sealed snapshot service."""

    resolved = settings or get_settings()
    configured_scopes = resolved.storage_inventory_scopes if scopes is None else scopes
    if not configured_scopes:
        raise ValueError("at least one exact storage inventory scope is required")
    connection_factory = psycopg_connection_factory(resolved.postgres_dsn)
    s3_client, _, _ = _s3(resolved)
    service = StorageGovernanceService(
        PostgresStorageRepository(connection_factory),
        cursor_secret=resolved.cursor_secret,
    )
    return StorageInventoryRuntime(
        producer=StorageInventorySnapshotProducer(
            PostgresStorageInventoryCatalog(
                connection_factory,
                object_store_bucket=resolved.object_store_bucket,
                artifact_prefix=resolved.artifact_prefix,
            ),
            S3StorageInventoryProvider(s3_client, resolved.object_store_bucket),
            service,
            observation_interval_seconds=max(int(resolved.storage_inventory_interval_seconds), 1),
        ),
        scopes=configured_scopes,
        interval_seconds=resolved.storage_inventory_interval_seconds,
    )


def build_worker_outbox(
    settings: Settings | None = None,
    *,
    temporal_client: Any | None = None,
) -> WorkerOutboxRuntime | None:
    """Compose durable upload-event delivery for explicitly authorized scopes."""

    resolved = settings or get_settings()
    if not resolved.outbox_scopes:
        return None
    connection_factory = psycopg_connection_factory(resolved.postgres_dsn)
    s3_client, s3_presign_client, object_storage = _s3(resolved)
    catalog_repository = PostgresCatalogAdapter(connection_factory)
    lance_root = resolved.lance_root_uri or f"s3://{resolved.object_store_bucket}/lance"
    catalog = LanceCatalogService(
        LanceAdapter(
            lance_root,
            object_store_client=s3_client,
            storage_options={
                "aws_endpoint": resolved.object_store_endpoint,
                "aws_access_key_id": resolved.object_store_access_key,
                "aws_secret_access_key": resolved.object_store_secret_key,
                "aws_region": resolved.object_store_region,
                "allow_http": str(resolved.object_store_endpoint.startswith("http://")).lower(),
            },
        ),
        catalog_repository,
        PostgresAdvisoryDatasetLock(connection_factory),
    )
    launcher = TemporalWorkflowLauncher(
        resolved.temporal_target,
        namespace=os.getenv("HC_TEMPORAL_NAMESPACE", "default"),
        task_queue=os.getenv("HC_TEMPORAL_TASK_QUEUE", DEFAULT_TASK_QUEUE),
        client=temporal_client,
    )
    resolver = PostgresIngestWorkflowInputResolver(
        connection_factory,
        ChunkedObjectStorageReader(object_storage),
        catalog,
    )
    handler = IngestOutboxHandler(launcher, resolver)
    storage_repository = PostgresStorageRepository(connection_factory)
    storage_handler = StorageLifecycleOutboxHandler(
        launcher,
        RepositoryStorageExecutionInputResolver(storage_repository),
    )
    storage_transaction_factory = StorageTransactionConnectionFactory(connection_factory)
    storage_schedule_repository = PostgresStorageRepository(storage_transaction_factory)
    storage_schedule_service = StorageGovernanceService(
        storage_schedule_repository,
        cursor_secret=resolved.cursor_secret,
        idempotency=PostgresStorageIdempotencyStore(
            connection_factory,
            storage_transaction_factory,
        ),
    )
    schedule_handler = StorageLifecycleScheduleOutboxHandler(storage_schedule_service)
    schedule_enqueuer = RepositoryStorageScheduleEnqueuer(storage_repository)
    audit_repository = PostgresAuditGovernanceRepository(connection_factory)
    audit_projection = AuditProjectionService(
        PostgresAuditProjectionRepository(connection_factory),
        cursor_secret=resolved.cursor_secret,
        retention_policy_provider=audit_repository.get_policy,
        legal_hold_provider=audit_repository.list_holds,
    )
    audit_governance = AuditGovernanceService(
        audit_repository,
        audit_projection,
        S3AuditArtifactStore(
            s3_client,
            resolved.object_store_bucket,
            prefix=f"{resolved.artifact_prefix}/audit-exports",
            presign_client=s3_presign_client,
        ),
    )
    annotation = AnnotationService(PostgresAnnotationRepository(connection_factory))
    auto_providers: tuple[HttpAutoAnnotationProvider, ...] = ()
    if resolved.auto_annotation_provider_endpoint is not None:
        api_key = resolved.auto_annotation_provider_api_key
        auto_providers = (
            HttpAutoAnnotationProvider(
                name=resolved.auto_annotation_provider_name,
                endpoint=resolved.auto_annotation_provider_endpoint,
                models=resolved.auto_annotation_provider_models,
                api_key=None if api_key is None else api_key.get_secret_value(),
                timeout_seconds=resolved.auto_annotation_provider_timeout_seconds,
            ),
        )
    auto_annotation_jobs = AutoAnnotationJobService(
        annotation,
        PostgresAutoAnnotationJobRepository(connection_factory),
        auto_providers,
        max_concurrent_jobs_per_project=resolved.auto_annotation_max_concurrent_jobs,
        max_jobs_per_hour=resolved.auto_annotation_max_jobs_per_hour,
        daily_cost_limit_micros=resolved.auto_annotation_daily_cost_limit_micros,
        sampling_repository=PostgresAutoAnnotationSamplingRepository(connection_factory),
        require_sampling_manifest=True,
    )
    dispatcher = OutboxDispatcher(
        PostgresOutboxDeliveryRepository(connection_factory),
        {
            handler.EVENT_TYPE: handler,
            storage_handler.EVENT_TYPE: storage_handler,
            schedule_handler.EVENT_TYPE: schedule_handler,
            AuditExportOutboxHandler.EVENT_TYPE: AuditExportOutboxHandler(audit_governance),
            AutoAnnotationOutboxHandler.EVENT_TYPE: AutoAnnotationOutboxHandler(
                auto_annotation_jobs
            ),
        },
        worker_id=f"hc-outbox-{socket.gethostname()}",
    )
    return WorkerOutboxRuntime(
        dispatcher=dispatcher,
        scopes=resolved.outbox_scopes,
        poll_interval_seconds=resolved.outbox_poll_interval_seconds,
        batch_size=resolved.outbox_batch_size,
        schedule_enqueuer=schedule_enqueuer,
    )
