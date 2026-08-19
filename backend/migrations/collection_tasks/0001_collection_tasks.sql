CREATE SCHEMA IF NOT EXISTS collection_tasks;

CREATE SEQUENCE IF NOT EXISTS collection_tasks.task_code_sequence
    AS bigint
    MINVALUE 1
    MAXVALUE 99999999
    START WITH 1
    NO CYCLE;

CREATE TABLE IF NOT EXISTS collection_tasks.collection_tasks (
    collection_task_id text NOT NULL,
    project_id text NOT NULL,
    task_code char(8) NOT NULL,
    name text NOT NULL,
    task_type text NOT NULL,
    scenario text NOT NULL,
    description text NOT NULL DEFAULT '',
    target_json jsonb,
    quality_threshold double precision,
    status text NOT NULL DEFAULT 'ACTIVE',
    version bigint NOT NULL DEFAULT 1,
    create_fingerprint char(64) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, collection_task_id),
    UNIQUE (task_code),
    CHECK (collection_task_id <> ''),
    CHECK (project_id <> ''),
    CHECK (task_code ~ '^[0-9]{8}$'),
    CHECK (name <> ''),
    CHECK (task_type <> ''),
    CHECK (scenario <> ''),
    CHECK (target_json IS NULL OR jsonb_typeof(target_json) = 'object'),
    CHECK (quality_threshold IS NULL OR quality_threshold BETWEEN 0 AND 1),
    CHECK (status IN ('ACTIVE', 'CLOSED')),
    CHECK (version > 0),
    CHECK (create_fingerprint ~ '^[a-f0-9]{64}$')
);

CREATE INDEX IF NOT EXISTS collection_tasks_project_status_created_idx
ON collection_tasks.collection_tasks (project_id, status, created_at DESC, collection_task_id DESC);

SELECT core.apply_project_rls('collection_tasks.collection_tasks'::regclass);

CREATE OR REPLACE FUNCTION collection_tasks.reject_new_rollout_for_closed_task()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    matched_status text;
BEGIN
    -- An existing rollout is an idempotent retry, not a new package association.
    IF EXISTS (
        SELECT 1
        FROM ingest.rollouts existing
        WHERE existing.project_id = NEW.project_id
          AND existing.rollout_id = NEW.rollout_id
    ) THEN
        RETURN NEW;
    END IF;

    SELECT task.status
      INTO matched_status
      FROM ingest.collection_jobs job
      JOIN collection_tasks.collection_tasks task
        ON task.project_id = job.project_id
       AND task.collection_task_id = job.task_id
     WHERE job.project_id = NEW.project_id
       AND job.collection_job_id = NEW.collection_job_id
       FOR SHARE OF task;

    -- Historical/unmanaged ingest identifiers stay compatible. Only a formal P20
    -- association is governed by the P20 close state.
    IF matched_status IS NULL THEN
        RETURN NEW;
    END IF;
    IF matched_status <> 'ACTIVE' THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'COLLECTION_TASK_CLOSED',
            DETAIL = 'A closed collection task cannot accept a new rollout.';
    END IF;
    RETURN NEW;
END
$function$;

DROP TRIGGER IF EXISTS collection_task_accepts_new_rollout ON ingest.rollouts;
CREATE TRIGGER collection_task_accepts_new_rollout
BEFORE INSERT ON ingest.rollouts
FOR EACH ROW
EXECUTE FUNCTION collection_tasks.reject_new_rollout_for_closed_task();
