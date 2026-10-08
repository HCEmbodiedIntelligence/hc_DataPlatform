"""Read model provenance from the authorized immutable package, never current bindings."""

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import problem
from hc_data_platform.dataset_registry.models import DatasetPageEpisodeRevision
from hc_data_platform.episode_store.files import PackageFiles
from hc_data_platform.logical_data.models import LogicalRobotAssetGrant, LogicalRobotScene
from hc_data_platform.registry.service import RegistryService
from hc_data_platform.robotics.scene_models import FrozenRobotScene
from hc_data_platform.security.auth import AuthContext


class LogicalSceneReader:
    def __init__(self, files: PackageFiles, registry: RegistryService) -> None:
        self.files, self.registry = files, registry

    def scene(self, revision: DatasetPageEpisodeRevision, snapshot_id: str) -> LogicalRobotScene:
        try:
            return self._scene(revision, snapshot_id)
        except ValueError as exc:
            raise problem(
                status=409,
                code="FROZEN_ROBOT_SCENE_INVALID",
                title="Frozen robot scene is inconsistent",
                detail=str(exc),
            ) from exc

    def _scene(self, revision: DatasetPageEpisodeRevision, snapshot_id: str) -> LogicalRobotScene:
        bindings = [s.package_binding for s in revision.streams]
        if not bindings or all(b is None for b in bindings):
            return LogicalRobotScene(snapshot_id=snapshot_id, provenance="MISSING_HISTORICAL")
        first = bindings[0]
        if first is None or any(b is None or b.data != first.data for b in bindings):
            raise ValueError("logical revision has mixed package sources")
        data = first.data
        package = self.files.open(data.identity, data.data_version_id)
        if package.receipt.data != data:
            raise ValueError("robot scene package differs from fixed revision")
        if "hc/robot_scene.json" not in package.entries:
            return LogicalRobotScene(snapshot_id=snapshot_id, provenance="MISSING_HISTORICAL")
        scene = FrozenRobotScene.model_validate(package.document("hc/robot_scene.json"))
        if scene.calibration != package.document("hc/calibration.json"):
            raise ValueError("robot scene calibration differs from fixed package")
        return LogicalRobotScene(
            snapshot_id=snapshot_id,
            provenance="FROZEN",
            scene_sha256=scene.content_sha256,
            scene=scene,
        )

    def authorize_asset(
        self,
        revision: DatasetPageEpisodeRevision,
        view: LogicalRobotScene,
        asset_id: str,
        auth: AuthContext,
    ) -> LogicalRobotAssetGrant:
        scene = view.scene
        if scene is None or scene.model_version_id is None or view.scene_sha256 is None:
            raise KeyError("snapshot has no frozen model asset")
        asset = next((asset for asset in scene.assets if asset.asset_id == asset_id), None)
        if asset is None:
            raise KeyError("asset is not in frozen model manifest")
        grant = self.registry.authorize_published_asset_download(
            auth=auth,
            organization_id=revision.scope.organization_id,
            project_id=revision.scope.project_id,
            version_id=scene.model_version_id,
            asset_id=asset_id,
            request_id=current_request_context().request_id,
        )
        if grant.sha256 != asset.sha256 or grant.media_type != asset.media_type:
            raise problem(
                status=409,
                code="FROZEN_MODEL_ASSET_MISMATCH",
                title="Frozen model asset differs",
                detail="The authorized asset no longer matches the data snapshot.",
            )
        return LogicalRobotAssetGrant(
            snapshot_id=view.snapshot_id,
            scene_sha256=view.scene_sha256,
            asset=asset,
            authorization=grant,
        )
