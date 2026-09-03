-- A restore is an append-only data revision, distinct from ordinary edits and
-- legacy-cleaning imports.  Historic annotation rows remain immutable; only
-- the admissible provenance catalog is extended.

ALTER TABLE annotation.annotation_revisions
    DROP CONSTRAINT IF EXISTS annotation_revisions_origin_check,
    DROP CONSTRAINT IF EXISTS annotation_revision_origin_valid,
    DROP CONSTRAINT IF EXISTS annotation_revision_legacy_audit_pair;

ALTER TABLE annotation.annotation_revisions
    ADD CONSTRAINT annotation_revision_origin_valid
        CHECK (origin IN ('ANNOTATION', 'ANNOTATION_RESTORE', 'LEGACY_CLEANING')),
    ADD CONSTRAINT annotation_revision_legacy_audit_pair
        CHECK ((origin = 'LEGACY_CLEANING') = (legacy_audit IS NOT NULL));
