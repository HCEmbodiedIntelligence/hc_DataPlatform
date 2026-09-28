"""Production composition shared by HTTP and processing consumers."""

from pathlib import Path

from .commit import EpisodePackageCommitter
from .files import PackageFiles, VerifiedFileCache
from .postgres import PostgresEpisodeStore
from .reader import ParquetStepReader, PhysicalDepthReader


class EpisodePackageService:
    def __init__(
        self,
        connections,
        storage,
        directory: Path,
        *,
        cache_bytes=2 * 1024**3,
        max_object_bytes=32 * 1024**3,
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

    def close(self):
        self.cache.close()
