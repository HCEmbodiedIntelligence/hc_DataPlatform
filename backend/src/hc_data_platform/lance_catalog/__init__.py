"""Lance dataset catalog contracts, real adapters, and in-memory fakes."""

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
