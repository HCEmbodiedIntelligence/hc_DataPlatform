-- Active instance ownership is separate from immutable capture provenance.
CREATE TABLE robotics.robot_history_anchors (
 organization_id text NOT NULL, robot_id text NOT NULL, snapshot jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(organization_id,robot_id)
);
INSERT INTO robotics.robot_history_anchors(organization_id,robot_id,snapshot)
 SELECT organization_id,robot_id,to_jsonb(r) FROM robotics.robot_assets r;
SELECT core.apply_organization_rls('robotics.robot_history_anchors'::regclass);
CREATE FUNCTION robotics.remember_robot_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO robotics.robot_history_anchors(organization_id,robot_id,snapshot)
 VALUES(NEW.organization_id,NEW.robot_id,to_jsonb(NEW)) ON CONFLICT DO NOTHING;
 RETURN NEW;
END $$;
CREATE TRIGGER remember_robot_identity AFTER INSERT ON robotics.robot_assets
 FOR EACH ROW EXECUTE FUNCTION robotics.remember_robot_identity();

-- Historical records reference a durable identity; the managed instance can be
-- removed after its active associations have been resolved. No old row changes.
DO $migration$
DECLARE c record;
BEGIN
 FOR c IN SELECT conrelid::regclass AS relation,conname,
  pg_get_constraintdef(oid) AS definition FROM pg_constraint
  WHERE contype='f' AND confrelid='robotics.robot_assets'::regclass
  AND conrelid IN ('ingest.robot_ingest_identities'::regclass,
                  'ingest.robot_processing_schemes'::regclass,
                  'robotics.robot_asset_components'::regclass,
                  'calibrations.calibration_sets'::regclass)
 LOOP
  EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I',c.relation,c.conname);
  EXECUTE format('ALTER TABLE %s ADD CONSTRAINT %I %s',c.relation,c.conname,
    replace(c.definition,'REFERENCES robotics.robot_assets',
                         'REFERENCES robotics.robot_history_anchors'));
 END LOOP;
END $migration$;

CREATE TABLE robotics.robot_resolution_operations (
 organization_id text NOT NULL, operation_id text NOT NULL, robot_id text NOT NULL,
 target_robot_id text, action text NOT NULL, fingerprint text NOT NULL,
 actor_id text NOT NULL, document jsonb NOT NULL DEFAULT '{}',
 result jsonb, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(organization_id,operation_id)
);
SELECT core.apply_organization_rls('robotics.robot_resolution_operations'::regclass);

CREATE TABLE robotics.robot_reference_links (
 link_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 organization_id text NOT NULL, kind text NOT NULL, project_id text NOT NULL DEFAULT '',
 region_code text NOT NULL DEFAULT '', reference_id text NOT NULL,
 origin_robot_id text NOT NULL, robot_id text,
 operation_id text NOT NULL, created_by text NOT NULL,
 active boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now(), ended_at timestamptz,
 FOREIGN KEY(organization_id,origin_robot_id)
  REFERENCES robotics.robot_history_anchors(organization_id,robot_id),
 FOREIGN KEY(organization_id,robot_id)
  REFERENCES robotics.robot_history_anchors(organization_id,robot_id),
 FOREIGN KEY(organization_id,operation_id)
  REFERENCES robotics.robot_resolution_operations(organization_id,operation_id),
 CHECK(active = (ended_at IS NULL))
);
CREATE UNIQUE INDEX robot_reference_links_active
 ON robotics.robot_reference_links(organization_id,kind,project_id,region_code,reference_id,origin_robot_id)
 WHERE active;
SELECT core.apply_organization_rls('robotics.robot_reference_links'::regclass);

CREATE FUNCTION robotics.robot_reference_owner(
 org text, reference_kind text, project text, region text, reference text, origin text
) RETURNS text LANGUAGE sql STABLE AS $$
 SELECT CASE WHEN EXISTS(SELECT 1 FROM robotics.robot_reference_links l
  WHERE l.organization_id=org AND l.kind=reference_kind AND l.project_id=COALESCE(project,'')
   AND l.region_code=COALESCE(region,'') AND l.reference_id=reference
   AND l.origin_robot_id=origin AND l.active)
 THEN (SELECT l.robot_id FROM robotics.robot_reference_links l
  WHERE l.organization_id=org AND l.kind=reference_kind AND l.project_id=COALESCE(project,'')
   AND l.region_code=COALESCE(region,'') AND l.reference_id=reference
   AND l.origin_robot_id=origin AND l.active)
 ELSE origin END
$$;
