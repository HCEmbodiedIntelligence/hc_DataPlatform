"""Freeze once per immutable source; instance rebinding never rewrites data provenance."""

from collections.abc import Callable
from typing import Any, TypeVar

from hc_data_platform.core.context import current_request_context
from hc_data_platform.episode_store.mcap_plan import McapEpisodePlan
from hc_data_platform.episode_store.models import EpisodeIdentity
from hc_data_platform.episode_store.native_plan import NativeEpisodePlan
from hc_data_platform.registry.models import RobotModelAsset, RobotModelJointMapping
from hc_data_platform.robotics.scene_models import FrozenRobotScene, scene_hash

Plan = TypeVar("Plan", McapEpisodePlan, NativeEpisodePlan)
ModelInputs = tuple[str, str, tuple[RobotModelAsset, ...], str, tuple[RobotModelJointMapping, ...]]


class RobotSceneSnapshots:
    def __init__(
        self,
        connections: Callable[[], Any],
        *,
        binding_query: Callable[[Any, str, str], tuple[str, str] | None],
        model_query: Callable[[Any, str, str], ModelInputs],
    ) -> None:
        self.connections = connections
        self.binding_query, self.model_query = binding_query, model_query

    def freeze_package(self, plan: Plan) -> Plan:
        source = plan.input
        identity: EpisodeIdentity = source.identity
        context = current_request_context()
        if identity.scope != (context.organization_id, context.project_id, context.region_code):
            raise ValueError("robot scene source scope differs from processing context")
        calibration = plan.metadata.get("calibration", {"status": "unknown"})
        key = scene_hash(
            {
                "identity": identity.model_dump(mode="json"),
                "source_sha256": source.source_sha256,
                "robot_id": source.robot_id,
            }
        )
        with self.connections() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("robot-scene/1:" + key,)
            )
            cursor.execute(
                """SELECT document,content_sha256 FROM robotics.data_scene_snapshots
                WHERE organization_id=%s AND source_key=%s""",
                (identity.organization_id, key),
            )
            row = cursor.fetchone()
            if row is not None:
                scene = FrozenRobotScene.model_validate(row[0])
                if scene.content_sha256 != row[1] or scene.calibration != calibration:
                    raise ValueError("immutable source scene or calibration differs")
            else:
                binding = self.binding_query(cursor, identity.organization_id, source.robot_id)
                model: dict[str, Any] = {}
                if binding is not None:
                    model_id, assets_hash, assets, mapping_hash, mappings = self.model_query(
                        cursor, identity.organization_id, binding[1]
                    )
                    model = dict(
                        binding_id=binding[0],
                        model_id=model_id,
                        model_version_id=binding[1],
                        asset_manifest_sha256=assets_hash,
                        assets=assets,
                        joint_mapping_sha256=mapping_hash,
                        joint_mappings=mappings,
                    )
                scene = FrozenRobotScene(
                    robot_id=source.robot_id,
                    status="FROZEN" if binding else "NO_MODEL_BINDING",
                    **model,
                    calibration=calibration,
                    calibration_sha256=scene_hash(calibration),
                )
                cursor.execute(
                    """INSERT INTO robotics.data_scene_snapshots
                    (organization_id,project_id,region_code,source_key,model_version_id,document,content_sha256)
                    VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                    (
                        *identity.scope,
                        key,
                        scene.model_version_id,
                        scene.model_dump_json(),
                        scene.content_sha256,
                    ),
                )
        # The plan's existing configuration hash binds the complete scene; no new
        # default field changes historical PackageInput IDs or Temporal payloads.
        updated = plan.model_copy(
            update={"metadata": {**plan.metadata, "robot_scene": scene.model_dump(mode="json")}}
        )
        frozen_input = source.model_copy(
            update={"configuration_sha256": updated.configuration_sha256}
        )
        return type(plan).model_validate({**updated.model_dump(mode="json"), "input": frozen_input})
