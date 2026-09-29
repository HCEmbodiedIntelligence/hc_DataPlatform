-- G1 additive: human capture review never mutates QC, annotations or package bytes.
CREATE TABLE episode_store.data_reviews (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    dataset_id text NOT NULL,
    episode_id text NOT NULL,
    data_version_id text NOT NULL,
    review_id text NOT NULL,
    sequence bigint NOT NULL CHECK (sequence > 0),
    actor_id text NOT NULL CHECK (length(actor_id) > 0),
    key_sha256 text NOT NULL CHECK (key_sha256 ~ '^[0-9a-f]{64}$'),
    request_sha256 text NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    document jsonb NOT NULL CHECK (jsonb_typeof(document) = 'object'),
    PRIMARY KEY (organization_id, project_id, region_code, data_version_id, sequence),
    UNIQUE (review_id),
    UNIQUE (organization_id, project_id, region_code, data_version_id, actor_id, key_sha256),
    FOREIGN KEY (organization_id, project_id, region_code, dataset_id, episode_id, data_version_id)
        REFERENCES episode_store.versions
        (organization_id, project_id, region_code, dataset_id, episode_id, data_version_id)
);
SELECT core.apply_project_rls('episode_store.data_reviews'::regclass);

CREATE FUNCTION episode_store.protect_data_review() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent episode_store.versions%ROWTYPE;
DECLARE previous episode_store.data_reviews%ROWTYPE;
DECLARE location jsonb;
DECLARE linked_tag jsonb;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'Episode data review is append-only' USING ERRCODE = '23514';
    END IF;
    IF NEW.organization_id IS DISTINCT FROM NULLIF(current_setting('app.organization_id',true),'')
       OR NEW.project_id IS DISTINCT FROM NULLIF(current_setting('app.project_id',true),'')
       OR NEW.region_code IS DISTINCT FROM NULLIF(current_setting('app.region_code',true),'') THEN
        RAISE EXCEPTION 'Episode data review scope mismatch' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO parent FROM episode_store.versions
        WHERE organization_id=NEW.organization_id AND project_id=NEW.project_id
          AND region_code=NEW.region_code AND dataset_id=NEW.dataset_id
          AND episode_id=NEW.episode_id AND data_version_id=NEW.data_version_id
          AND status='COMMITTED' FOR UPDATE;
    IF NOT FOUND OR parent.receipt_document->'data' IS DISTINCT FROM NEW.document->'data' THEN
        RAISE EXCEPTION 'Episode data review requires the exact committed receipt'
            USING ERRCODE = '23514';
    END IF;
    SELECT * INTO previous FROM episode_store.data_reviews
        WHERE organization_id=NEW.organization_id AND project_id=NEW.project_id
          AND region_code=NEW.region_code AND data_version_id=NEW.data_version_id
        ORDER BY sequence DESC LIMIT 1;
    IF NEW.sequence <> COALESCE(previous.sequence,0)+1
       OR NEW.document->>'previous_review_id' IS DISTINCT FROM previous.review_id
       OR NEW.document->>'review_id' IS DISTINCT FROM NEW.review_id
       OR (NEW.document->>'sequence')::bigint IS DISTINCT FROM NEW.sequence
       OR NEW.document->>'actor_id' IS DISTINCT FROM NEW.actor_id
       OR NEW.document->>'format' IS DISTINCT FROM 'hc-episode-data-review/1'
       OR COALESCE(NEW.document->>'decision','') NOT IN ('APPROVED','RETURNED')
       OR COALESCE(length(btrim(NEW.document->>'comment')),0) NOT BETWEEN 1 AND 4000
       OR (NEW.document->>'created_at')::timestamptz IS NULL
       OR jsonb_typeof(NEW.document->'locations') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'Episode data review identity/sequence/document mismatch'
            USING ERRCODE = '23514';
    END IF;
    IF jsonb_array_length(NEW.document->'locations') > 100 THEN
        RAISE EXCEPTION 'too many review locations' USING ERRCODE = '23514';
    END IF;
    FOR location IN SELECT value FROM jsonb_array_elements(NEW.document->'locations') LOOP
        IF (location->>'start_step' IS NULL) <> (location->>'end_step' IS NULL)
           OR (location->>'start_step')::bigint < 0
           OR (location->>'end_step')::bigint <= (location->>'start_step')::bigint
           OR (location->>'end_step')::bigint > (NEW.document->'data'->>'frame_count')::bigint
           OR COALESCE(length(btrim(location->>'comment')),0) NOT BETWEEN 1 AND 2000 THEN
            RAISE EXCEPTION 'invalid review location' USING ERRCODE = '23514';
        END IF;
        linked_tag := NULLIF(location->'tag', 'null'::jsonb);
        IF location->>'start_step' IS NULL AND linked_tag IS NULL THEN
            RAISE EXCEPTION 'missing review location' USING ERRCODE = '23514';
        END IF;
        IF linked_tag IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM annotation.annotation_revisions r
            JOIN annotation.annotation_tasks t ON t.task_id=r.task_id
            WHERE t.organization_id=NEW.organization_id AND t.project_id=NEW.project_id
              AND t.region_code=NEW.region_code AND t.data_ref=NEW.document->'data'
              AND r.data_ref=t.data_ref AND r.task_id=linked_tag->>'task_id'
              AND r.revision=(linked_tag->>'revision')::bigint
              AND r.content_hash=linked_tag->>'revision_content_hash'
              AND EXISTS (SELECT 1 FROM jsonb_array_elements(r.tags) tag
                  WHERE tag->>'annotation_id'=linked_tag->>'annotation_id')
        ) THEN
            RAISE EXCEPTION 'review tag differs from bound annotation revision'
                USING ERRCODE = '23514';
        END IF;
    END LOOP;
    RETURN NEW;
END $$;
CREATE TRIGGER data_review_identity BEFORE INSERT OR UPDATE OR DELETE ON episode_store.data_reviews
    FOR EACH ROW EXECUTE FUNCTION episode_store.protect_data_review();
