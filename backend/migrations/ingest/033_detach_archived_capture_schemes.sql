-- Retire only current bindings. Pinned scheme and mapping versions remain immutable.
UPDATE ingest.capture_scheme_links AS link
SET active = false, is_default = false, revision = link.revision + 1, updated_at = now()
FROM ingest.capture_schemes AS scheme
WHERE (scheme.organization_id, scheme.scheme_id) = (link.organization_id, link.scheme_id)
  AND scheme.archived AND (link.active OR link.is_default);
