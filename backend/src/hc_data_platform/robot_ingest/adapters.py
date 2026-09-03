from __future__ import annotations

from typing import Any, Protocol

from pydantic import Field

from hc_data_platform.core.errors import problem

from .models import (
    CameraVerification,
    CaptureMode,
    QualityStatus,
    RobotIngestAsset,
    RobotIngestProcessingStatus,
    RobotIngestUploadManifest,
    StrictModel,
)


class AdapterVerification(StrictModel):
    verified_episode_count: int | None = Field(default=None, ge=0)
    verified_sample_count: int = Field(default=0, ge=0)
    camera_verification: tuple[CameraVerification, ...] = ()
    quality_status: QualityStatus = QualityStatus.PENDING
    processing_status: RobotIngestProcessingStatus = RobotIngestProcessingStatus.PENDING


class RobotFormatAdapter(Protocol):
    name: str

    def preflight_manifest(self, manifest: RobotIngestUploadManifest) -> None: ...

    def verify_assets(
        self,
        manifest: RobotIngestUploadManifest,
        assets: tuple[RobotIngestAsset, ...],
    ) -> AdapterVerification: ...

    def discover_episodes(self, manifest: RobotIngestUploadManifest) -> int | None: ...

    def extract_capture_metrics(
        self, manifest: RobotIngestUploadManifest
    ) -> tuple[CameraVerification, ...]: ...

    def normalize_raw_source(self, manifest: RobotIngestUploadManifest) -> dict[str, Any]: ...

    def start_processing(self, raw_source_id: str) -> dict[str, Any]: ...


class _BaseAdapter:
    name = "base"

    def preflight_manifest(self, manifest: RobotIngestUploadManifest) -> None:
        del manifest

    def verify_assets(
        self,
        manifest: RobotIngestUploadManifest,
        assets: tuple[RobotIngestAsset, ...],
    ) -> AdapterVerification:
        if len(assets) != len(manifest.assets) or any(
            asset.state.value != "COMPLETED" for asset in assets
        ):
            raise problem(
                status=409,
                code="ROBOT_INGEST_ASSETS_INCOMPLETE",
                title="Upload assets are incomplete",
                detail="Every declared Raw asset must complete integrity verification.",
            )
        # ``format_metadata`` is supplied by the robot and is therefore only a
        # declaration. Built-in adapters must not promote client hints to verified
        # Episode, sample, camera, or QC facts. The trusted processing worker writes
        # those through ``apply_processing_result`` after reading immutable Raw data.
        return AdapterVerification(
            verified_episode_count=None,
            verified_sample_count=0,
            camera_verification=(),
            quality_status="PENDING",
            processing_status=(
                "DISCOVERING_EPISODES"
                if manifest.capture_mode is CaptureMode.CONTINUOUS
                else "PENDING"
            ),
        )

    def discover_episodes(self, manifest: RobotIngestUploadManifest) -> int | None:
        del manifest
        return None

    def extract_capture_metrics(
        self, manifest: RobotIngestUploadManifest
    ) -> tuple[CameraVerification, ...]:
        del manifest
        return ()

    def extract_sample_count(self, manifest: RobotIngestUploadManifest) -> int:
        del manifest
        return 0

    def normalize_raw_source(self, manifest: RobotIngestUploadManifest) -> dict[str, Any]:
        return {
            "adapter_name": self.name,
            "source_format": manifest.source_format,
            "source_format_version": manifest.source_format_version,
            "capture_mode": manifest.capture_mode.value,
        }

    def start_processing(self, raw_source_id: str) -> dict[str, Any]:
        return {"raw_source_id": raw_source_id, "adapter_name": self.name, "status": "PENDING"}


class LeRobotAdapter(_BaseAdapter):
    name = "lerobot_v3"

    def preflight_manifest(self, manifest: RobotIngestUploadManifest) -> None:
        if manifest.capture_mode is not CaptureMode.PRESEGMENTED:
            raise problem(
                status=422,
                code="LEROBOT_CAPTURE_MODE_INVALID",
                title="LeRobot capture mode is invalid",
                detail="LeRobot uploads must use PRESEGMENTED capture mode.",
            )
        if manifest.declared_episode_count is None:
            raise problem(
                status=422,
                code="LEROBOT_EPISODE_COUNT_REQUIRED",
                title="LeRobot episode count is required",
                detail="Declare the number of Episodes contained in this LeRobot upload.",
            )

    def discover_episodes(self, manifest: RobotIngestUploadManifest) -> int | None:
        discovered = super().discover_episodes(manifest)
        # Discovery from immutable LeRobot metadata runs asynchronously in production.
        # Until that parser reports a count, the declaration remains unverified.
        return discovered


class ContinuousRecordingAdapter(_BaseAdapter):
    # Keep the durable Raw job adapter name compatible with the existing
    # continuous-recording worker registry.
    name = "capture_bundle"

    def preflight_manifest(self, manifest: RobotIngestUploadManifest) -> None:
        # CAPTURE_BUNDLE describes the recorder's native Raw container. Episode
        # organization remains an independent manifest fact: most bundles are
        # continuous, while recorders may also provide pre-segmented windows.
        del manifest

    def discover_episodes(self, manifest: RobotIngestUploadManifest) -> int | None:
        if manifest.capture_mode is CaptureMode.CONTINUOUS:
            return None
        return super().discover_episodes(manifest)


class McapAdapter(_BaseAdapter):
    name = "mcap"


class CustomFormatAdapter(_BaseAdapter):
    def __init__(self, adapter_name: str = "custom") -> None:
        if not adapter_name or len(adapter_name) > 128:
            raise ValueError("adapter_name must be a non-empty bounded string")
        self.name = adapter_name


class RobotFormatAdapterRegistry:
    """One registry for format differences; multipart transport remains format-neutral."""

    def __init__(self) -> None:
        self._adapters: dict[str, RobotFormatAdapter] = {}
        self.register("LEROBOT_V3", LeRobotAdapter())
        self.register("MCAP", McapAdapter())
        self.register("CAPTURE_BUNDLE", ContinuousRecordingAdapter())

    def register(self, source_format: str, adapter: RobotFormatAdapter) -> None:
        key = source_format.upper()
        if not key:
            raise ValueError("source_format must not be empty")
        self._adapters[key] = adapter

    def resolve(self, manifest: RobotIngestUploadManifest) -> RobotFormatAdapter:
        adapter_name = manifest.format_metadata.get("adapter_name")
        adapter = self._adapters.get(manifest.source_format.upper())
        if adapter is None:
            raise problem(
                status=422,
                code="ROBOT_INGEST_ADAPTER_NOT_CONFIGURED",
                title="Source format adapter is not configured",
                detail="The requested source format has no approved Adapter.",
            )
        if isinstance(adapter_name, str) and adapter_name and adapter_name != adapter.name:
            raise problem(
                status=422,
                code="ROBOT_INGEST_ADAPTER_NOT_CONFIGURED",
                title="Source format adapter is not configured",
                detail="The requested source format has no approved Adapter.",
            )
        return adapter
