"""Combine independent data and annotation reads without rewriting processing receipts."""

from typing import TYPE_CHECKING, Protocol

from hc_data_platform.annotation.delivery_queries import AnnotationDeliveries

from .models import RobotIngestEpisodeReadResult, RobotIngestEpisodeResult, RobotIngestUpload

if TYPE_CHECKING:
    from .processing_store import ProcessingStore


def project_episode(
    item: RobotIngestEpisodeResult, annotation_status: str = "UNKNOWN"
) -> RobotIngestEpisodeReadResult:
    return RobotIngestEpisodeReadResult(
        **item.model_dump(),
        annotation_status=annotation_status,
        data_readable=(
            item.status == "READY"
            and item.quality_status == "PASS"
            and item.dataset_version is not None
            and (item.lance_version is not None or item.data_ref is not None)
        ),
    )


class EpisodeDeliveries(Protocol):
    def project(
        self, upload: RobotIngestUpload, items: tuple[RobotIngestEpisodeResult, ...]
    ) -> tuple[RobotIngestEpisodeReadResult, ...]: ...


class EpisodeDeliveryProjection:
    def __init__(self, processing: "ProcessingStore", annotations: AnnotationDeliveries) -> None:
        self.processing = processing
        self.annotations = annotations

    def project(
        self, upload: RobotIngestUpload, items: tuple[RobotIngestEpisodeResult, ...]
    ) -> tuple[RobotIngestEpisodeReadResult, ...]:
        _, document = self.processing.read(upload)
        receipts = {ep.episode_id: ep.receipt for ep in document.episodes}
        removed = {ep.episode_id for ep in document.episodes if ep.status == "DISCARDED"}
        target = upload.target
        deliveries = self.annotations.for_episodes(
            scope=(target.organization_id, target.project_id, target.region_code),
            dataset_id=target.dataset_id,
            episode_ids=tuple(item.episode_id for item in items if item.episode_id not in removed),
        )
        results = []
        for item in items:
            if item.episode_id in removed:
                continue
            receipt = receipts.get(item.episode_id)
            status = "UNKNOWN"
            if receipt is not None and (
                receipt.dataset_version,
                receipt.lance_version,
                receipt.data_ref,
            ) == (item.dataset_version, item.lance_version, item.data_ref):
                status = receipt.annotation_status
            matching = [
                entry.status
                for entry in deliveries
                if (entry.rollout_id, entry.dataset_version, entry.lance_version, entry.data_ref)
                == (item.episode_id, item.dataset_version, item.lance_version, item.data_ref)
            ]
            if matching:
                # A task created by an annotation-only retry supersedes its original failure.
                # Require all exact-version tasks to be approved before displaying APPROVED.
                status = "APPROVED" if set(matching) == {"APPROVED"} else "CREATED"
            results.append(project_episode(item, status))
        return tuple(results)
