"""Production composition root shared by the API and Temporal Worker processes."""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlparse

from hc_data_platform.alignment.arrow_writer import ArrowFragmentWriter
from hc_data_platform.alignment.engine import AlignmentEngine
from hc_data_platform.alignment.models import AlignedFragmentManifestV1, AlignedValueV1
from hc_data_platform.alignment.postgres import PostgresAlignmentRepository
from hc_data_platform.annotation.postgres import PostgresAnnotationRepository
from hc_data_platform.annotation.router import configure_annotation
from hc_data_platform.annotation.service import AnnotationService
from hc_data_platform.core.config import Settings, get_settings
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.ingest.adapters import S3ObjectStorage
from hc_data_platform.ingest.postgres import PostgresIngestPersistence
from hc_data_platform.ingest.router import configure_ingest_service
from hc_data_platform.ingest.service import UploadSessionService
from hc_data_platform.lance_catalog.adapters import (
    LanceAdapter,
    PostgresAdvisoryDatasetLock,
    PostgresCatalogAdapter,
)
from hc_data_platform.lance_catalog.models import (
    AlignedFragmentManifestV1 as CatalogFragmentManifestV1,
)
from hc_data_platform.lance_catalog.models import StepRecord
from hc_data_platform.lance_catalog.router import configure_lance_catalog
from hc_data_platform.lance_catalog.service import LanceCatalogService, compute_fragment_hash
from hc_data_platform.preview.adapters import (
    AnnotationExclusionAdapter,
    FFmpegHlsEncoder,
    FilePreviewCache,
    LanceStepReaderAdapter,
)
from hc_data_platform.preview.memory import HmacUrlSigner
from hc_data_platform.preview.router import configure_preview_service
from hc_data_platform.preview.service import PreviewService
from hc_data_platform.publishing.adapters import (
    ApprovedAnnotationSnapshotAdapter,
    CatalogSnapshotAdapter,
    StepReaderAdapter,
)
from hc_data_platform.publishing.exporters import LanceSnapshotExporter, LeRobotV3Exporter
from hc_data_platform.publishing.postgres import (
    PostgresAnnotationTaskLocator,
    PostgresCatalogRolloutState,
    PostgresPublishedManifestRepository,
)
from hc_data_platform.publishing.router import (
    configure_dataset_publisher,
    configure_export_coordinator,
)
from hc_data_platform.publishing.s3 import S3ArtifactSink
from hc_data_platform.publishing.service import DatasetPublisher, ExportCoordinator
from hc_data_platform.quality.engine import QualityEngine
from hc_data_platform.quality.postgres import PostgresQualityRepository
from hc_data_platform.quality.router import configure_quality_repository
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
from hc_data_platform.workflow.models import (
    AlignmentActivityInput,
    CatalogFragmentPayloadV1,
)


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
            payload = json.loads(bytes(encoded).decode("utf-8"))
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
    ingest: UploadSessionService
    annotation: AnnotationService
    catalog: LanceCatalogService
    verification_repository: PostgresVerificationRepository
    quality_repository: PostgresQualityRepository
    alignment_repository: PostgresAlignmentRepository
    preview: PreviewService
    publisher: DatasetPublisher
    exporter: ExportCoordinator
    activities: ActivityDependencies


def _s3(settings: Settings) -> tuple[Any, S3ObjectStorage]:
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "s3",
        endpoint_url=settings.object_store_endpoint,
        aws_access_key_id=settings.object_store_access_key,
        aws_secret_access_key=settings.object_store_secret_key,
        region_name=settings.object_store_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    return client, S3ObjectStorage(client, settings.object_store_bucket)


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


def build_runtime(settings: Settings | None = None) -> RuntimeComponents:
    resolved = settings or get_settings()
    connection_factory = psycopg_connection_factory(resolved.postgres_dsn)
    s3_client, object_storage = _s3(resolved)

    ingest = UploadSessionService(
        object_storage,
        persistence=PostgresIngestPersistence(connection_factory),
    )
    annotation = AnnotationService(PostgresAnnotationRepository(connection_factory))
    verification_repository = PostgresVerificationRepository(connection_factory)
    quality_repository = PostgresQualityRepository(connection_factory)
    alignment_repository = PostgresAlignmentRepository(connection_factory)

    catalog_repository = PostgresCatalogAdapter(connection_factory)
    lance_root = resolved.lance_root_uri or f"s3://{resolved.object_store_bucket}/lance"
    lance_storage = LanceAdapter(
        lance_root,
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

    preview = PreviewService(
        step_reader=LanceStepReaderAdapter(catalog),
        exclusions=AnnotationExclusionAdapter(annotation),
        encoder=FFmpegHlsEncoder(Path(resolved.preview_cache_root) / "media"),
        cache=FilePreviewCache(Path(resolved.preview_cache_root) / "metadata"),
        signer=HmacUrlSigner(resolved.cursor_secret.encode()),
    )

    artifact_sink = S3ArtifactSink(
        s3_client, resolved.object_store_bucket, prefix=resolved.artifact_prefix
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
    activities = ActivityDependencies(
        verifier=McapVerifier(
            ChunkedObjectStorageReader(object_storage),
            decoder=_decoder(resolved),
        ),
        quality=QualityEngine(),
        alignment=AlignmentEngine(),
        fragment_writers=ArrowFragmentWriterFactory(Path(resolved.alignment_staging_root)),
        catalog_fragments=ArrowCatalogFragmentAdapter(catalog_repository),
        catalog=catalog,
        preview=preview,
        publisher=publisher,
        exporter=exporter,
        catalog_reconciler=catalog,
        publication_reconciler=PublicationReconciler(publisher),
        verification_reports=verification_repository,
        quality_reports=quality_repository,
        alignment_manifests=alignment_repository,
    )
    return RuntimeComponents(
        ingest=ingest,
        annotation=annotation,
        catalog=catalog,
        verification_repository=verification_repository,
        quality_repository=quality_repository,
        alignment_repository=alignment_repository,
        preview=preview,
        publisher=publisher,
        exporter=exporter,
        activities=activities,
    )


def configure_api(runtime: RuntimeComponents) -> None:
    configure_ingest_service(runtime.ingest)
    configure_annotation(runtime.annotation)
    configure_verification_repository(runtime.verification_repository)
    configure_quality_repository(runtime.quality_repository)
    from hc_data_platform.alignment.router import configure_alignment_repository

    configure_alignment_repository(runtime.alignment_repository)
    configure_lance_catalog(runtime.catalog)
    configure_preview_service(runtime.preview)
    configure_dataset_publisher(runtime.publisher)
    configure_export_coordinator(runtime.exporter)


def activity_dependencies() -> ActivityDependencies:
    """Deployment-owned factory referenced by HC_WORKFLOW_ACTIVITY_FACTORY."""

    return build_runtime().activities
