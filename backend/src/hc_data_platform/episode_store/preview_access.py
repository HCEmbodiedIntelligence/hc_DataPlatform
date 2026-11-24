"""Authorize playback against the selected assignment and immutable Episode version."""

from hc_data_platform.core.context import select_request_scope
from hc_data_platform.core.errors import problem
from hc_data_platform.security.http import authorize_scope
from hc_data_platform.security.scope import ScopeGuard


def authorize_episode_preview(
    packages,
    auth,
    organization,
    project,
    region,
    dataset_id,
    *,
    episode_id=None,
    version_id=None,
    rollout_id=None,
    dataset_version=None,
    lance_version=None,
):
    if auth.has_capability("episode.read", project, organization):
        authorize_scope(auth, project, "episode.read", region, organization)
        return
    ScopeGuard.require(auth, project, region, organization)
    select_request_scope(project, region, organization_id=organization)
    scoped = (organization, project, region, dataset_id)
    annotate, review, publish, collect = (
        auth.has_capability(role, project, organization)
        for role in ("role.annotator", "role.reviewer", "role.publisher", "role.collector")
    )
    native = episode_id is not None and version_id is not None
    legacy = rollout_id is not None and (dataset_version is not None or lance_version is not None)
    if packages is not None and (native or legacy) and (annotate or review or publish or collect):
        with packages.repository.transaction() as cursor:
            if annotate or review or publish:
                cursor.execute(
                    """
                    SELECT 1 FROM annotation.annotation_tasks
                    WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s
                      AND ((data_ref #>> '{identity,episode_id}'=%s AND data_ref->>'data_version_id'=%s)
                        OR (data_ref IS NULL AND rollout_id=%s AND (dataset_version=%s OR base_lance_version=%s)))
                      AND ((%s AND assignee_id=%s)
                        OR (%s AND status='SUBMITTED')
                        OR (%s AND status='APPROVED' AND approved_revision=current_revision))
                    LIMIT 1
                """,
                    (
                        *scoped,
                        episode_id,
                        version_id,
                        rollout_id,
                        dataset_version,
                        lance_version,
                        annotate,
                        auth.subject_id,
                        review,
                        publish,
                    ),
                )
                if cursor.fetchone():
                    return
            if collect:
                cursor.execute(
                    """
                    SELECT DISTINCT r.collection_task_id FROM ingest.raw_source_episodes e
                    JOIN ingest.raw_sources r USING (organization_id, project_id, region_code, raw_source_id)
                    WHERE r.organization_id=%s AND r.project_id=%s AND r.region_code=%s AND r.dataset_id=%s
                      AND r.collection_task_id IS NOT NULL
                      AND ((e.data_ref #>> '{identity,episode_id}'=%s AND e.data_ref->>'data_version_id'=%s)
                        OR (e.data_ref IS NULL AND e.episode_id=%s AND (e.dataset_version=%s OR e.lance_version=%s)))
                """,
                    (*scoped, episode_id, version_id, rollout_id, dataset_version, lance_version),
                )
                if any(
                    auth.has_capability("upload.manage/" + task, project, organization)
                    for (task,) in cursor.fetchall()
                ):
                    return
    raise problem(
        status=403,
        code="CAPABILITY_REQUIRED",
        title="Episode preview forbidden",
        detail="The selected Episode is outside the authorized preview scope.",
    )
