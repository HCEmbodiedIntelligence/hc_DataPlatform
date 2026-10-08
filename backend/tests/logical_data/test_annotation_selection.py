"""Missing, mixed and ambiguous fixed source bindings fail before sample access."""

import pytest

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.errors import ProblemException
from tests.dataset_registry.test_dataset_version_detail_api import (
    DATASET_ID,
    ORGANIZATION_ID,
    PROJECT_ID,
    REGION_CODE,
    REVIEWING_VERSION_ID,
    _auth,
    _scope,
    _service,
)


@pytest.mark.parametrize("failure", ["missing", "ambiguous", "mixed", "token"])
def test_fixed_rollout_resolution_rejects_non_unique_or_unbound_sources(failure):
    service = _service()
    token = bind_request_context(
        RequestContext(
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            subject_id="p07-reviewer",
        )
    )
    try:
        args = dict(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            dataset_id=DATASET_ID,
            version_id=REVIEWING_VERSION_ID,
            request_id="logical-contract",
        )
        bootstrap = service.version_bootstrap(**args).data
        repository = service._repository
        revisions = repository.find_rollout_revisions(
            scope=_scope(),
            dataset_id=DATASET_ID,
            version_id=REVIEWING_VERSION_ID,
            rollout_id="rollout_p07fixture",
        )
        assert len(revisions) == 1
        original = revisions[0]
        key = (
            *_scope().model_dump().values(),
            DATASET_ID,
            REVIEWING_VERSION_ID,
            original.revision_id,
        )
        if failure == "ambiguous":
            duplicate = original.model_copy(update={"revision_id": "revision_duplicate"})
            repository._episode_revisions[(*key[:-1], duplicate.revision_id)] = duplicate
        elif failure == "mixed":
            streams = list(original.streams)
            media = streams[0].aligned_media_binding
            assert media is not None
            streams[0] = streams[0].model_copy(
                update={
                    "aligned_media_binding": media.model_copy(
                        update={"rollout_id": "another-source"}
                    )
                }
            )
            repository._episode_revisions[key] = original.model_copy(
                update={"streams": tuple(streams)}
            )
        with pytest.raises(ProblemException) as denied:
            service.rollout_revision(
                **args,
                rollout_id="absent" if failure == "missing" else "rollout_p07fixture",
                snapshot_token="bad-snapshot-token"
                if failure == "token"
                else bootstrap.snapshot_token,
            )
        assert denied.value.problem.status in {400, 409}
        if failure != "token":
            assert denied.value.problem.code == "LOGICAL_SOURCE_BINDING_UNAVAILABLE"
    finally:
        reset_request_context(token)
