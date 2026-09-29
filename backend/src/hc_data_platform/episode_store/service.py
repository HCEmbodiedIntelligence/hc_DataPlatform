"""Production composition shared by HTTP and processing consumers."""

from pathlib import Path
from tempfile import TemporaryDirectory

from .bundle_assets import BundleAssetsLoader
from .catalog import EpisodeCatalogReader, PostgresLegacyEntries
from .catalog_models import EpisodeDerivedReady
from .collections import EpisodeCollections
from .commit import EpisodePackageCommitter
from .files import PackageFiles, VerifiedFileCache
from .postgres import PostgresEpisodeStore, assert_scope
from .reader import ParquetStepReader, PhysicalDepthReader
from .schemas import EpisodeSchemas
from .writer import write_episode


class EpisodePackageService:
    def __init__(
        self,
        connections,
        storage,
        directory: Path,
        *,
        cache_bytes=2 * 1024**3,
        max_object_bytes=32 * 1024**3,
        legacy_catalog=None,
        media_repository=None,
    ):
        self.repository = PostgresEpisodeStore(connections)
        self.directory = directory
        self.cache = VerifiedFileCache(
            directory / "files", capacity_bytes=cache_bytes, max_object_bytes=max_object_bytes
        )
        self.files = PackageFiles(self.repository, storage, self.cache)
        self.committer = EpisodePackageCommitter(self.repository, storage)
        self.steps = ParquetStepReader(self.files)
        self.depth = PhysicalDepthReader(self.files)
        self.schemas = EpisodeSchemas(self.repository)
        self.bundle_assets = (
            BundleAssetsLoader(
                storage,
                media_repository,
                directory / "prepared-media",
                max_object_bytes=max_object_bytes,
            )
            if media_repository is not None
            else None
        )
        self.collections = EpisodeCollections(
            self.repository,
            storage,
            legacy=legacy_catalog,
            legacy_entries=PostgresLegacyEntries(self.repository, legacy_catalog)
            if legacy_catalog is not None
            else None,
        )
        self.catalog = (
            EpisodeCatalogReader(
                legacy_catalog, self.collections, self.files, self.steps, schemas=self.schemas
            )
            if legacy_catalog is not None
            else None
        )

    def close(self):
        self.cache.close()

    def write_from_aligned_bundle(self, source, steps, *, schema, metadata, **bundle):
        """Internal processing path; media may precede the visible Episode READY state."""
        if self.bundle_assets is None:
            raise RuntimeError("prepared media repository is not configured")
        with self.bundle_assets.load(source, **bundle) as assets:
            return self.write_and_publish(source, steps, assets, schema=schema, metadata=metadata)

    def write_and_publish(self, source, steps, assets, *, schema, metadata):
        """Processing hand-off; returns a real package and collection data reference.

        A failed collection publication leaves a recoverable complete package,
        but does not advance the dataset head or claim a product projection.
        """
        assert_scope(source.identity)
        if (
            schema.project_id != source.identity.project_id
            or schema.dataset_id != source.identity.dataset_id
            or schema.schema_snapshot_id != source.schema_snapshot_id
        ):
            raise ValueError("processing schema differs from frozen Episode input")
        self.schemas.register_schema(schema)
        staging = self.directory / "preparing"
        staging.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="attempt-", dir=staging) as temporary:
            root = Path(temporary) / "episode"
            write_episode(root, source, steps, assets, fps=schema.frequency_hz, metadata=metadata)
            receipt = self.committer.commit(root, source)
        collection = self.collections.publish(receipt.data, schema)
        ready = EpisodeDerivedReady(
            data=receipt.data,
            collection_version_id=collection.collection_version_id,
            project_id=source.identity.project_id,
            dataset_id=source.identity.dataset_id,
            rollout_id=source.identity.rollout_id,
            source_sha256=source.source_sha256,
            converter_version=source.processor_version,
            dataset_version=collection.version,
            step_count=receipt.data.frame_count,
            content_hash=collection.content_hash,
        )
        return collection, ready
