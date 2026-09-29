"""Storage-aware logical StepReaderPort over mixed, version-pinned collections."""

from hc_data_platform.lance_catalog.models import DatasetVersionRef

from .catalog_models import CollectionStepWindow, EpisodeLineage
from .collections import collection_scope
from .models import DataVersionRef, EpisodeIdentity, LegacyEpisodeRef, digest
from .postgres import PackageConflict


class PostgresLegacyEntries:
    """Resolve historical Episode bindings from persisted revisions, never split IDs."""

    def __init__(self, repository, legacy):
        self.repository, self.legacy = repository, legacy

    def __call__(self, version):
        scope = collection_scope(version.dataset_id, version.project_id)
        with self.repository.transaction() as cursor:
            cursor.execute(
                """SELECT episode_id,revision_document
                FROM dataset_registry.dataset_version_episode_revisions
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                  AND dataset_id=%s AND version_id=%s ORDER BY ordinal,episode_id""",
                (*scope, f"version_lance_{version.version}"),
            )
            records = cursor.fetchall()
            entries = []
            for episode_id, document in records:
                rollouts = {
                    binding["rollout_id"]
                    for stream in document["streams"]
                    for kind in ("data_binding", "aligned_media_binding")
                    if (binding := stream.get(kind)) is not None
                }
                if len(rollouts) != 1:
                    raise PackageConflict(
                        "historical Episode has no unambiguous persisted rollout binding"
                    )
                rollout = next(iter(rollouts))
                lineage = self.legacy.lineage(
                    version.dataset_id,
                    rollout,
                    version=version.version,
                    project_id=version.project_id,
                )
                cursor.execute(
                    """SELECT step_count FROM lance_rollout_lineage
                    WHERE organization_id=%s AND project_id=%s AND dataset_id=%s AND rollout_id=%s
                      AND version_added<=%s""",
                    (scope[0], scope[1], scope[3], rollout, version.version),
                )
                row = cursor.fetchone()
                if row is None:
                    raise PackageConflict("historical Episode lineage is missing")
                identity = EpisodeIdentity(
                    organization_id=scope[0],
                    project_id=scope[1],
                    region_code=scope[2],
                    dataset_id=scope[3],
                    episode_id=episode_id,
                    rollout_id=rollout,
                )
                entries.append(
                    LegacyEpisodeRef(
                        identity=identity,
                        data_version_id="legacy_"
                        + digest(
                            {
                                "identity": identity.model_dump(mode="json"),
                                "dataset_uri": version.dataset_uri,
                                "lance_version": version.lance_version,
                                "content_hash": lineage.fragment_content_hash,
                            }
                        ),
                        content_hash=lineage.fragment_content_hash,
                        schema_snapshot_id=lineage.schema_snapshot_id,
                        frame_count=row[0],
                        dataset_version=version.version,
                        dataset_uri=version.dataset_uri,
                        lance_version=version.lance_version,
                    )
                )
            if {e.identity.rollout_id for e in entries} != set(version.committed_rollouts):
                raise KeyError("historical collection is not fully bound in this region")
            return tuple(entries)


class EpisodeCatalogReader:
    """Shares the established logical read methods; exposes no new Lance writer."""

    def __init__(self, legacy, collections, files, steps):
        self.legacy, self.collections, self.files, self.steps = legacy, collections, files, steps

    def schema_for(self, project_id, dataset_id):
        collection_scope(dataset_id, project_id)
        return self.legacy.schema_for(project_id, dataset_id)

    def current_version(self, dataset_id, *, project_id=None):
        collection_scope(dataset_id, project_id)
        current = self.collections.current(dataset_id, project_id=project_id)
        if current is not None:
            return current
        legacy = self.legacy.current_version(dataset_id, project_id=project_id)
        if legacy is not None:
            try:
                self._authorize_legacy_snapshot(legacy)
            except KeyError:
                return None
        return legacy

    def list_versions(self, dataset_id, *, project_id=None):
        collection_scope(dataset_id, project_id)
        old = []
        for version in self.legacy.list_versions(dataset_id, project_id=project_id):
            try:
                self._authorize_legacy_snapshot(version)
            except KeyError:
                continue
            old.append(version)
        new = self.collections.list_versions(dataset_id, project_id=project_id)
        if {v.version for v in old} & {v.version for v in new}:
            raise PackageConflict("legacy and collection version numbers overlap")
        return tuple(sorted((*old, *new), key=lambda item: item.version))

    def version_snapshot(self, dataset_id, *, version=None, project_id=None):
        collection_scope(dataset_id, project_id)
        if version is None:
            selected = self.current_version(dataset_id, project_id=project_id)
            if selected is None:
                raise KeyError("dataset has no committed version")
            if isinstance(selected, DatasetVersionRef):
                self._authorize_legacy_snapshot(selected)
            return selected
        try:
            return self.collections.get(dataset_id, version=version, project_id=project_id)
        except KeyError:
            selected = self.legacy.version_snapshot(
                dataset_id, version=version, project_id=project_id
            )
            self._authorize_legacy_snapshot(selected)
            return selected

    def _authorize_legacy_snapshot(self, selected):
        if self.collections.legacy_entries is None:
            raise PermissionError("historical reads require explicit regional Episode bindings")
        return self.collections.legacy_entries(selected)

    @staticmethod
    def _entry(version, rollout):
        for entry in version.episodes:
            if entry.identity.rollout_id == rollout:
                return entry
        raise KeyError("rollout is absent from the selected collection version")

    def _legacy_lineage(self, entry):
        version = self.legacy.version_snapshot(
            entry.identity.dataset_id,
            project_id=entry.identity.project_id,
            version=entry.dataset_version,
        )
        lineage = self.legacy.lineage(
            entry.identity.dataset_id,
            entry.identity.rollout_id,
            project_id=entry.identity.project_id,
            version=entry.dataset_version,
        )
        if (
            version.dataset_uri != entry.dataset_uri
            or version.lance_version != entry.lance_version
            or lineage.fragment_content_hash != entry.content_hash
            or lineage.schema_snapshot_id != entry.schema_snapshot_id
        ):
            raise PackageConflict(
                "historical storage no longer matches the frozen collection reference"
            )
        return lineage

    def read_steps(
        self,
        dataset_id,
        rollout_id,
        start_step,
        end_step,
        *,
        version=None,
        project_id=None,
        columns=None,
    ):
        selected = self.version_snapshot(dataset_id, version=version, project_id=project_id)
        if isinstance(selected, DatasetVersionRef):
            return self.legacy.read_steps(
                dataset_id,
                rollout_id,
                start_step,
                end_step,
                project_id=project_id,
                version=selected.version,
                columns=columns,
            )
        entry = self._entry(selected, rollout_id)
        if not 0 <= start_step <= end_step <= entry.frame_count or end_step - start_step > 4096:
            raise ValueError("invalid or oversized collection step window")
        if isinstance(entry, DataVersionRef):
            rows = self.steps.read_steps(
                entry.identity, entry.data_version_id, start_step, end_step, columns=columns
            ).steps
        else:
            self._legacy_lineage(entry)
            rows = self.legacy.read_steps(
                dataset_id,
                rollout_id,
                start_step,
                end_step,
                project_id=project_id,
                version=entry.dataset_version,
                columns=columns,
            ).steps
        return CollectionStepWindow(
            project_id=entry.identity.project_id,
            dataset_id=dataset_id,
            dataset_version=selected.version,
            collection_version_id=selected.collection_version_id,
            rollout_id=rollout_id,
            data=entry,
            start_step=start_step,
            end_step=end_step,
            steps=rows,
        )

    def lineage(self, dataset_id, rollout_id, *, version=None, project_id=None):
        selected = self.version_snapshot(dataset_id, version=version, project_id=project_id)
        if isinstance(selected, DatasetVersionRef):
            return self.legacy.lineage(
                dataset_id, rollout_id, project_id=project_id, version=selected.version
            )
        entry = self._entry(selected, rollout_id)
        if isinstance(entry, DataVersionRef):
            package = self.files.open(entry.identity, entry.data_version_id)
            source = package.manifest.input
            sha, processor, uri = (
                source.source_sha256,
                source.processor_version,
                entry.package_prefix,
            )
        else:
            legacy = self._legacy_lineage(entry)
            sha, processor, uri = (
                legacy.source_sha256,
                legacy.converter_version,
                legacy.fragment_uri,
            )
        return EpisodeLineage(
            data=entry,
            project_id=entry.identity.project_id,
            dataset_id=dataset_id,
            dataset_version=selected.version,
            collection_version_id=selected.collection_version_id,
            rollout_id=rollout_id,
            source_sha256=sha,
            converter_version=processor,
            schema_snapshot_id=entry.schema_snapshot_id,
            schema_fingerprint=selected.schema_fingerprint,
            fragment_uri=uri,
            fragment_content_hash=entry.content_hash,
            dataset_uri=selected.manifest_key,
        )
