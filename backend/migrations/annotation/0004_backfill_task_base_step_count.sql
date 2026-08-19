-- Forward-only extraction of the lineage backfill that was accidentally added
-- to the already-applied annotation/0002 migration. It is intentionally tolerant
-- of deployments where the Lance lineage module is not installed.
DO $$
BEGIN
    IF to_regclass('public.lance_rollout_lineage') IS NOT NULL THEN
        EXECUTE $backfill$
            UPDATE annotation.annotation_tasks task
            SET base_step_count = lineage.step_count
            FROM public.lance_rollout_lineage lineage
            WHERE task.base_step_count IS NULL
              AND lineage.step_count > 0
              AND lineage.project_id = task.project_id
              AND lineage.dataset_id = task.dataset_id
              AND lineage.rollout_id = task.rollout_id
        $backfill$;
    END IF;
END
$$;
