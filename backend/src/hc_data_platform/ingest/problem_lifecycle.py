"""Current problems follow data lifecycle; historical receipts remain immutable."""


def is_discarded(*statuses: str | None) -> bool:
    return "DISCARDED" in statuses


def active_problem_sql(alias: str) -> str:
    """Predicate for an internal SQL alias with scope and rollout_id columns."""
    if not alias.isidentifier():
        raise ValueError("expected a SQL relation alias")
    return f"""
    NOT EXISTS (
        SELECT 1 FROM ingest.raw_source_episodes removed
        WHERE (removed.organization_id,removed.project_id,removed.region_code,removed.episode_id)
          = ({alias}.organization_id,{alias}.project_id,{alias}.region_code,{alias}.rollout_id)
          AND removed.status='DISCARDED'
    ) AND NOT EXISTS (
        SELECT 1 FROM ingest.episode_resolutions removed
        WHERE (removed.organization_id,removed.project_id,removed.region_code,removed.episode_id)
          = ({alias}.organization_id,{alias}.project_id,{alias}.region_code,{alias}.rollout_id)
          AND removed.status='DISCARDED'
    ) AND NOT EXISTS (
        SELECT 1 FROM ingest.recording_episode_processing removed
        WHERE (removed.organization_id,removed.project_id,removed.region_code)
          = ({alias}.organization_id,{alias}.project_id,{alias}.region_code)
          AND removed.recording_id || ':' || removed.episode_id = {alias}.rollout_id
          AND removed.status='DISCARDED'
    ) AND NOT EXISTS (
        SELECT 1 FROM ingest.rollouts duplicate
        WHERE (duplicate.organization_id,duplicate.project_id,duplicate.region_code,
               duplicate.rollout_id)
          = ({alias}.organization_id,{alias}.project_id,{alias}.region_code,{alias}.rollout_id)
          AND duplicate.duplicate_of_rollout_id IS NOT NULL
    )
    """.strip()
