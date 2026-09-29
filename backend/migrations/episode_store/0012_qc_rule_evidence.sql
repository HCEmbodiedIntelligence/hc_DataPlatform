-- Additional execution receipts; archived qc-report/v1 documents/hashes are untouched.
CREATE TABLE episode_store.qc_evidence (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    rollout_id text NOT NULL,
    source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    report_sha256 text NOT NULL CHECK (report_sha256 ~ '^[0-9a-f]{64}$'),
    evidence_sha256 text NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
    document jsonb NOT NULL CHECK (jsonb_typeof(document)='object'),
    PRIMARY KEY (organization_id,project_id,region_code,evidence_sha256),
    FOREIGN KEY (organization_id,project_id) REFERENCES registry.organization_projects
);
CREATE INDEX qc_evidence_report ON episode_store.qc_evidence
    (organization_id,project_id,region_code,rollout_id,report_sha256);
SELECT core.apply_project_rls('episode_store.qc_evidence'::regclass);
CREATE FUNCTION episode_store.protect_qc_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE evidence jsonb;
DECLARE report jsonb;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'QC evidence is immutable' USING ERRCODE='23514';
    END IF;
    IF NEW.organization_id IS DISTINCT FROM NULLIF(current_setting('app.organization_id',true),'')
       OR NEW.project_id IS DISTINCT FROM NULLIF(current_setting('app.project_id',true),'')
       OR NEW.region_code IS DISTINCT FROM NULLIF(current_setting('app.region_code',true),'') THEN
        RAISE EXCEPTION 'QC evidence scope mismatch' USING ERRCODE='42501';
    END IF;
    evidence := NEW.document->'evidence';
    report := NEW.document->'report';
    IF evidence->>'format' IS DISTINCT FROM 'hc-qc-rule-evidence/1'
       OR evidence->>'content_sha256' IS DISTINCT FROM NEW.evidence_sha256
       OR evidence->>'report_sha256' IS DISTINCT FROM NEW.report_sha256
       OR report->>'content_sha256' IS DISTINCT FROM NEW.report_sha256
       OR evidence->>'source_sha256' IS DISTINCT FROM NEW.source_sha256
       OR report->>'source_sha256' IS DISTINCT FROM NEW.source_sha256
       OR evidence->>'rollout_id' IS DISTINCT FROM NEW.rollout_id
       OR report->>'rollout_id' IS DISTINCT FROM NEW.rollout_id
       OR evidence->>'engine_version' IS DISTINCT FROM report->>'engine_version'
       OR evidence->'start_ns' IS DISTINCT FROM report->'start_ns'
       OR evidence->'end_ns' IS DISTINCT FROM report->'end_ns' THEN
        RAISE EXCEPTION 'QC evidence binding mismatch' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER qc_evidence_identity BEFORE INSERT OR UPDATE OR DELETE ON episode_store.qc_evidence
    FOR EACH ROW EXECUTE FUNCTION episode_store.protect_qc_evidence();
