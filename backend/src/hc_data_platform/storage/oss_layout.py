"""Durable physical OSS aliases; immutable business manifests keep their identity."""

from __future__ import annotations

import hashlib
import re

from hc_data_platform.core.dbapi import normalize_postgres_dsn


def compact_alias(key: str) -> tuple[str, str] | None:
    parts = key.split("/")
    kind = parts[0]
    if kind in {"episodes", "collections"}:
        marker = "dv_" if kind == "episodes" else "cv_"
        for index, part in enumerate(parts):
            if re.fullmatch("id-" + marker + "[a-f0-9]{64}", part):
                return "/".join(parts[: index + 1]), kind + "/" + part[3:]
    if kind == "raw" and len(parts) >= 5 and re.fullmatch("[a-f0-9]{32}", parts[3]):
        return "/".join(parts[:4]), "raw/" + parts[3]
    if (
        kind in {"derived-mcap", "recording-previews"}
        and len(parts) >= 6
        and re.fullmatch("[a-f0-9]{64}", parts[4])
    ):
        return "/".join(parts[:5]), kind + "/" + parts[4]
    if kind == "episode-previews" and len(parts) >= 4 and re.fullmatch("dv_[a-f0-9]{64}", parts[2]):
        return key, kind + "/" + hashlib.sha256(key.encode()).hexdigest() + "." + parts[-1].rsplit(
            ".", 1
        )[-1]
    if (
        kind == "episode-deliveries"
        and len(parts) == 4
        and re.fullmatch("ed_[a-f0-9]{64}", parts[2])
        and re.fullmatch("[a-f0-9]{64}\\.zip", parts[3])
    ):
        return key, kind + "/" + parts[2] + ".zip"
    return None


def replace_prefix(key, aliases, *, reverse=False):
    for logical, physical in sorted(aliases.items(), key=lambda p: -len(p[1 if reverse else 0])):
        source, target = (physical, logical) if reverse else (logical, physical)
        if key == source or key.startswith(source + "/"):
            return target + key[len(source) :]
    return key


class PostgresOssPrefixAliases:
    def __init__(self, dsn: str, bucket: str, root_prefix: str):
        self.dsn = normalize_postgres_dsn(dsn)
        self.bucket, self.root_prefix = bucket, root_prefix.rstrip("/")
        self._known = {}

    def _connect(self):
        import psycopg

        return psycopg.connect(self.dsn)

    def all(self):
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT logical_prefix,physical_prefix
                FROM platform.object_store_prefix_aliases
                WHERE bucket=%s AND root_prefix=%s""",
                (self.bucket, self.root_prefix),
            )
            self._known = dict(cursor.fetchall())
            return self._known

    def register(self, logical, physical):
        # A distinct logical owner can never overwrite another owner's short prefix.
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO platform.object_store_prefix_aliases
                (bucket,root_prefix,logical_prefix,physical_prefix) VALUES (%s,%s,%s,%s)
                ON CONFLICT (bucket,root_prefix,logical_prefix) DO NOTHING""",
                (self.bucket, self.root_prefix, logical, physical),
            )
            cursor.execute(
                """SELECT physical_prefix FROM platform.object_store_prefix_aliases
                WHERE bucket=%s AND root_prefix=%s AND logical_prefix=%s""",
                (self.bucket, self.root_prefix, logical),
            )
            if cursor.fetchone()[0] != physical:
                raise ValueError("OSS prefix alias conflicts with existing location")

    def physical(self, key, *, create=False):
        result = replace_prefix(key, self._known)
        if result != key:
            return result
        aliases = self.all()
        result = replace_prefix(key, aliases)
        if result != key or not create:
            return result
        candidate = compact_alias(key)
        if candidate is None:
            return key
        self.register(*candidate)
        return replace_prefix(key, dict([candidate]))

    def logical(self, key, aliases=None):
        return replace_prefix(key, self.all() if aliases is None else aliases, reverse=True)
