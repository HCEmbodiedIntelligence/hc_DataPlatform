-- Whole recordings are checked before humans spend time authoring slices.
DO $migration$
DECLARE constraint_row record;
BEGIN
  FOR constraint_row IN
    SELECT c.conname FROM pg_constraint c
    JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attname='status'
    WHERE c.conrelid='ingest.continuous_recordings'::regclass AND c.contype='c'
      AND a.attnum=ANY(c.conkey)
  LOOP
    EXECUTE format('ALTER TABLE ingest.continuous_recordings DROP CONSTRAINT %I',
                   constraint_row.conname);
  END LOOP;
END
$migration$;
ALTER TABLE ingest.continuous_recordings ADD CONSTRAINT continuous_recordings_status_check
  CHECK (status IN ('PENDING_QC','QC_RUNNING','QC_FAILED','QC_ERROR','READY_FOR_SLICING','SLICED'));
ALTER TABLE ingest.continuous_recordings ADD CONSTRAINT continuous_recordings_finalized_check
  CHECK ((status<>'SLICED' AND finalized_revision IS NULL)
      OR (status='SLICED' AND finalized_revision=current_revision));

CREATE TABLE ingest.recording_quality_reports (
  organization_id text NOT NULL,
  project_id text NOT NULL,
  region_code text NOT NULL,
  recording_id text NOT NULL,
  report_id text NOT NULL,
  report_document jsonb NOT NULL,
  PRIMARY KEY (organization_id,project_id,region_code,recording_id,report_id),
  FOREIGN KEY (organization_id,project_id,region_code,recording_id)
    REFERENCES ingest.continuous_recordings(organization_id,project_id,region_code,recording_id)
);
SELECT core.apply_project_rls('ingest.recording_quality_reports'::regclass);

-- Preserve finalized Episodes. Unsliced v2 recordings, including saved drafts,
-- enter the same durable queue as new uploads; no Raw objects are rewritten.
WITH pending AS (
  SELECT organization_id,project_id,region_code,recording_id,
    'recording-quality:' || md5(jsonb_build_array(organization_id,project_id,region_code,
      recording_id,source_sha256)::text) AS workflow_id
  FROM ingest.continuous_recordings
  WHERE status='READY_FOR_SLICING' AND recording_upload_id IS NOT NULL
), updated AS (
  UPDATE ingest.continuous_recordings r SET status='PENDING_QC', updated_at=now(),
    recording_document=r.recording_document || jsonb_build_object(
      'status','PENDING_QC','quality_workflow_id',p.workflow_id,'updated_at',now())
  FROM pending p WHERE (r.organization_id,r.project_id,r.region_code,r.recording_id)=
    (p.organization_id,p.project_id,p.region_code,p.recording_id)
  RETURNING r.*,p.workflow_id
)
INSERT INTO core.outbox_events
  (event_id,organization_id,project_id,region_code,event_type,envelope,occurred_at,
   available_at,published_at,publish_attempts)
SELECT md5(workflow_id)::uuid, organization_id,project_id,region_code,
  'continuous-recording.quality.requested.v1',
  jsonb_build_object('event_id',md5(workflow_id)::uuid,
    'event_type','continuous-recording.quality.requested.v1','schema_version',1,
    'aggregate_type','continuous_recording','aggregate_id',recording_id,
    'organization_id',organization_id,'project_id',project_id,'region_code',region_code,
    'occurred_at',now(),'payload',jsonb_build_object(
      'organization_id',organization_id,'project_id',project_id,'region_code',region_code,
      'recording_id',recording_id,'source_sha256',source_sha256,'workflow_id',workflow_id)),
  now(),now(),NULL,0 FROM updated ON CONFLICT DO NOTHING;
