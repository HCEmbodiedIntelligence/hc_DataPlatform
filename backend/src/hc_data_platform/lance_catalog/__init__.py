"""Compatibility exports. New callers use owned models/ports and adapter modules.

Resolving a contract does not initialize unrelated services, SDKs or workers.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .adapters import (
        InMemoryCatalogRepository,
        InMemoryDatasetWriterLock,
        LanceAdapter,
        PostgresAdvisoryDatasetLock,
        PostgresCatalogAdapter,
        build_dataset_uri,
    )
    from .models import (
        AlignedFragmentManifestV1,
        DatasetSchemaSnapshot,
        DatasetVersionRef,
        DerivedReadyV1,
        PendingReconciliation,
        RolloutLineage,
        StepRecord,
        StepWindow,
        StorageCommitReceipt,
    )
    from .ports import (
        CatalogRepositoryPort,
        DatasetWriterLockPort,
        FakeStepReader,
        LanceCatalogPort,
        LanceStoragePort,
        StepReaderPort,
    )
    from .schema import (
        LanceDependencyError,
        SchemaCompilationError,
        compile_arrow_schema,
        compute_schema_fingerprint,
    )
    from .service import (
        CatalogConflictError,
        CatalogIndexPendingError,
        DatasetReconciliationRequired,
        InMemoryLanceCatalog,
        LanceCatalogService,
        SchemaIncompatibleError,
        compute_fragment_hash,
    )

__all__ = [
    "AlignedFragmentManifestV1",
    "CatalogConflictError",
    "CatalogIndexPendingError",
    "CatalogRepositoryPort",
    "DatasetWriterLockPort",
    "DatasetReconciliationRequired",
    "DatasetSchemaSnapshot",
    "DatasetVersionRef",
    "DerivedReadyV1",
    "FakeStepReader",
    "InMemoryCatalogRepository",
    "InMemoryDatasetWriterLock",
    "InMemoryLanceCatalog",
    "LanceAdapter",
    "LanceCatalogPort",
    "LanceCatalogService",
    "LanceDependencyError",
    "LanceStoragePort",
    "PendingReconciliation",
    "PostgresAdvisoryDatasetLock",
    "PostgresCatalogAdapter",
    "RolloutLineage",
    "SchemaIncompatibleError",
    "SchemaCompilationError",
    "StepReaderPort",
    "StepRecord",
    "StepWindow",
    "StorageCommitReceipt",
    "build_dataset_uri",
    "compile_arrow_schema",
    "compute_fragment_hash",
    "compute_schema_fingerprint",
]

_EXPORTS = {
    "InMemoryCatalogRepository": (".adapters", "InMemoryCatalogRepository"),
    "InMemoryDatasetWriterLock": (".adapters", "InMemoryDatasetWriterLock"),
    "LanceAdapter": (".adapters", "LanceAdapter"),
    "PostgresAdvisoryDatasetLock": (".adapters", "PostgresAdvisoryDatasetLock"),
    "PostgresCatalogAdapter": (".adapters", "PostgresCatalogAdapter"),
    "build_dataset_uri": (".adapters", "build_dataset_uri"),
    "AlignedFragmentManifestV1": (".models", "AlignedFragmentManifestV1"),
    "DatasetSchemaSnapshot": (".models", "DatasetSchemaSnapshot"),
    "DatasetVersionRef": (".models", "DatasetVersionRef"),
    "DerivedReadyV1": (".models", "DerivedReadyV1"),
    "PendingReconciliation": (".models", "PendingReconciliation"),
    "RolloutLineage": (".models", "RolloutLineage"),
    "StepRecord": (".models", "StepRecord"),
    "StepWindow": (".models", "StepWindow"),
    "StorageCommitReceipt": (".models", "StorageCommitReceipt"),
    "CatalogRepositoryPort": (".ports", "CatalogRepositoryPort"),
    "DatasetWriterLockPort": (".ports", "DatasetWriterLockPort"),
    "FakeStepReader": (".ports", "FakeStepReader"),
    "LanceCatalogPort": (".ports", "LanceCatalogPort"),
    "LanceStoragePort": (".ports", "LanceStoragePort"),
    "StepReaderPort": (".ports", "StepReaderPort"),
    "LanceDependencyError": (".schema", "LanceDependencyError"),
    "SchemaCompilationError": (".schema_spec", "SchemaCompilationError"),
    "compile_arrow_schema": (".schema", "compile_arrow_schema"),
    "compute_schema_fingerprint": (".schema_spec", "compute_schema_fingerprint"),
    "CatalogConflictError": (".service", "CatalogConflictError"),
    "CatalogIndexPendingError": (".service", "CatalogIndexPendingError"),
    "DatasetReconciliationRequired": (".service", "DatasetReconciliationRequired"),
    "InMemoryLanceCatalog": (".service", "InMemoryLanceCatalog"),
    "LanceCatalogService": (".service", "LanceCatalogService"),
    "SchemaIncompatibleError": (".service", "SchemaIncompatibleError"),
    "compute_fragment_hash": (".service", "compute_fragment_hash"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module, attribute = target
    value = getattr(import_module(module, __name__), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
