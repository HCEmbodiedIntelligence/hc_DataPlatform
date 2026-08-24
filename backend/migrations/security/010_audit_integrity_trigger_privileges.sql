-- The append trigger must keep audit writers write-only.  The initial chain
-- migration deliberately created ordinary functions so its historical bytes
-- remain immutable; this forward migration gives only the trigger path the
-- privileges needed to read the just-written event and advance the chain.
--
-- Both functions are owned by the migration principal.  A fixed search path
-- includes PostgreSQL's default, database-owner-only pgcrypto schema (public)
-- because the immutable 008 migration installed ``digest`` there. Callers
-- receive no direct EXECUTE privilege: normal application roles can only reach
-- this code through the AFTER INSERT trigger on core.audit_events.

ALTER FUNCTION core.append_audit_integrity_entry(uuid)
    SECURITY DEFINER
    SET search_path = pg_catalog, core, public;

-- SQL-language functions plan their unqualified pgcrypto call with their own
-- function configuration, rather than inheriting the PL/pgSQL caller's path.
ALTER FUNCTION core.audit_integrity_event_hash(
    text, uuid, text, text, text, text, text, text, text, text, text, jsonb, timestamptz
)
    SET search_path = pg_catalog, core, public;

ALTER FUNCTION core.append_audit_integrity_entry_trigger()
    SECURITY DEFINER
    SET search_path = pg_catalog, core, public;

REVOKE ALL ON FUNCTION core.append_audit_integrity_entry(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION core.audit_integrity_event_hash(
    text, uuid, text, text, text, text, text, text, text, text, text, jsonb, timestamptz
) FROM PUBLIC;
REVOKE ALL ON FUNCTION core.append_audit_integrity_entry_trigger() FROM PUBLIC;

-- Read-only P19 verification recomputes a supplied digest in SQL.  This pure
-- immutable helper neither reads a relation nor advances a chain, so audit
-- readers may execute it without gaining write-chain access.
GRANT EXECUTE ON FUNCTION core.audit_integrity_event_hash(
    text, uuid, text, text, text, text, text, text, text, text, text, jsonb, timestamptz
) TO PUBLIC;
