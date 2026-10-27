from types import SimpleNamespace as N
from unittest.mock import Mock

import pytest

from hc_data_platform.core.context import RequestContext, bind_request_context, reset_request_context
from hc_data_platform.integrations.annotation_logical import AnnotationLogicalService
from hc_data_platform.logical_data.models import LogicalRobotScene
from hc_data_platform.logical_data.service import LogicalDataService


@pytest.mark.parametrize("model_read", [True, False])
def test_combined_snapshot_reuses_the_exact_authorized_revision(model_read):
    scope = dict(organization_id="org", project_id="project", region_code="region")
    task = N(task_id="task", **scope, data_ref=None, dataset_id="dataset", base_lance_version=1,
             rollout_id="rollout", base_step_count=12)
    revision = N(scope=N(**scope), dataset_id="dataset", version_id="version_lance_1",
                 revision_id="revision", episode_id="episode", content_sha256="a" * 64, streams=())
    annotations = N(read_task=Mock(return_value=task))
    revisions = N(version_bootstrap=Mock(return_value=N(data=N(snapshot_token="fixed-snapshot-token"))),
                  rollout_revision=Mock(return_value=N(data=revision)),
                  episode_revision=Mock(side_effect=AssertionError("duplicate revision read")))
    scenes = N(scene=Mock(side_effect=lambda revision, snapshot: LogicalRobotScene(snapshot_id=snapshot, provenance="MISSING_HISTORICAL")),
               authorize_assets=Mock(return_value=[]))
    logical = LogicalDataService(revisions, N(), scenes=scenes)
    service = AnnotationLogicalService(annotations, N(), revisions, logical)
    auth = N(has_organization_capability=Mock(return_value=model_read))
    context = bind_request_context(RequestContext(**scope))
    try:
        result = service.snapshot("task", **scope, auth=auth, include_model=True)
        assert result.task_id == "task" and result.snapshot.selection.revision_id == "revision"
        assert annotations.read_task.call_count == revisions.rollout_revision.call_count == 1
        revisions.episode_revision.assert_not_called()
        assert (result.robot is not None) == model_read
        if model_read:
            scenes.authorize_assets.assert_called_once_with(revision, result.robot, auth, "glb")
        else:
            scenes.scene.assert_not_called()
        revision.scope = N(**{**scope, "organization_id": "foreign"})
        with pytest.raises(ValueError, match="differs from authorized"):
            service.snapshot("task", **scope, auth=auth, include_model=True)
    finally:
        reset_request_context(context)
