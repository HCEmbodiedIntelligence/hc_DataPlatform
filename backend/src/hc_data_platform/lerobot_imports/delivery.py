"""Native upload delivery states from owned publication and annotation read ports."""

from collections import Counter
from typing import TYPE_CHECKING

from hc_data_platform.annotation.delivery_queries import AnnotationDeliveries
from hc_data_platform.ingest.raw_sources import RawSourceRepositoryPort
from hc_data_platform.robot_ingest.processing_errors import ProcessingErrors
from hc_data_platform.workflow.result_queries import TerminalWorkflowResults

if TYPE_CHECKING:
    from .processing import NativeImportProgress


class NativeDeliveryProjection:
    def __init__(
        self,
        sources: RawSourceRepositoryPort,
        results: TerminalWorkflowResults,
        annotations: AnnotationDeliveries,
        errors: ProcessingErrors | None = None,
    ) -> None:
        self.sources = sources
        self.results = results
        self.annotations = annotations
        self.errors = errors

    def enrich(
        self, scope: tuple[str, str, str], progress: "NativeImportProgress"
    ) -> "NativeImportProgress":
        episodes = self.sources.list_episodes(
            organization_id=scope[0],
            project_id=scope[1],
            region_code=scope[2],
            raw_source_id=progress.import_id,
        )
        readable = [
            episode
            for episode in episodes
            if episode.status.value == "READY"
            and episode.dataset_version is not None
            and (episode.data_ref is not None or episode.lance_version is not None)
        ]
        deliveries = self.annotations.for_episodes(
            scope=scope,
            dataset_id=progress.dataset_id,
            episode_ids=tuple(episode.episode_id for episode in readable),
        )
        counts: Counter[str] = Counter()
        for episode in readable:
            status = "UNKNOWN"
            stored = self.results.latest_terminal(
                organization_id=scope[0],
                project_id=scope[1],
                resource_id=episode.episode_id,
                job_type="ContinuousRecordingEpisodeWorkflow"
                if progress.source_format == "MCAP"
                else "IngestRolloutWorkflow",
            )
            if stored is not None and stored.status == "SUCCEEDED":
                derived = stored.result.get("derived") or {}
                # A receipt for another region or data version is not this Episode's evidence.
                if episode.data_ref is not None and derived.get(
                    "data"
                ) == episode.data_ref.model_dump(mode="json"):
                    status = stored.result.get("annotation_status", "UNKNOWN")
            matching = [
                item.status
                for item in deliveries
                if (item.rollout_id, item.dataset_version, item.lance_version, item.data_ref)
                == (
                    episode.episode_id,
                    episode.dataset_version,
                    episode.lance_version,
                    episode.data_ref,
                )
            ]
            if matching:
                status = "APPROVED" if set(matching) == {"APPROVED"} else "CREATED"
            counts[status] += 1
        failure = (
            self.errors.for_source(scope=scope, raw_source_id=progress.import_id)
            if self.errors is not None and progress.last_error_code
            else None
        )
        return progress.model_copy(
            update={
                "readable_episodes": len(readable),
                "annotation_counts": dict(counts),
                "last_error_detail": failure.detail
                if failure is not None and failure.code == progress.last_error_code
                else None,
            }
        )
