"""Migrate legacy robot-model files into PostgreSQL and optionally prune them."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass

from hc_data_platform.core.config import get_settings
from hc_data_platform.core.dbapi import normalize_postgres_dsn
from hc_data_platform.registry.filesystem_storage import FilesystemRobotModelStorage


@dataclass(frozen=True, slots=True)
class MigrationReport:
    discovered_rows: int
    migrated_rows: int
    pruned_objects: int


def migrate_legacy_asset_content(
    *,
    postgres_dsn: str,
    asset_root: str,
    prune_source: bool = False,
) -> MigrationReport:
    """Copy every legacy authoritative file into its asset row in one transaction."""

    import psycopg

    storage = FilesystemRobotModelStorage(
        asset_root,
        signing_secret="robot-model-content-migration-only",
    )
    migrated = 0
    discovered = 0
    prunable_keys: tuple[str, ...] = ()
    with psycopg.connect(normalize_postgres_dsn(postgres_dsn)) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT organization_id, asset_id, object_key, size_bytes, sha256
                  FROM registry.robot_model_assets
                 WHERE content IS NULL
                 ORDER BY organization_id, asset_id
                 FOR UPDATE
                """
            )
            rows = cursor.fetchall()
            discovered = len(rows)
            cached: dict[str, bytes] = {}
            for _organization_id, asset_id, object_key, size_bytes, expected_sha256 in rows:
                key = str(object_key)
                body = cached.get(key)
                if body is None:
                    body = b"".join(storage.read_chunks(key))
                    cached[key] = body
                if len(body) != int(size_bytes):
                    raise RuntimeError(f"legacy robot-model asset has a size mismatch: {key}")
                if hashlib.sha256(body).hexdigest() != str(expected_sha256):
                    raise RuntimeError(f"legacy robot-model asset has a checksum mismatch: {key}")
                cursor.execute(
                    """
                    UPDATE registry.robot_model_assets
                       SET content = %s
                     WHERE organization_id = %s AND asset_id = %s
                       AND content IS NULL
                    """,
                    (body, str(_organization_id), asset_id),
                )
                migrated += cursor.rowcount

            if prune_source:
                cursor.execute(
                    """
                    SELECT DISTINCT object_key
                      FROM registry.robot_model_assets AS asset
                     WHERE content IS NOT NULL
                       AND NOT EXISTS (
                           SELECT 1
                             FROM registry.robot_model_assets AS pending
                            WHERE pending.object_key = asset.object_key
                              AND pending.content IS NULL
                       )
                     ORDER BY object_key
                    """
                )
                prunable_keys = tuple(str(row[0]) for row in cursor.fetchall())
        connection.commit()

    pruned = 0
    if prune_source:
        for key in prunable_keys:
            if storage.head(key) is None:
                continue
            storage.delete_object(key)
            pruned += 1
    return MigrationReport(
        discovered_rows=discovered,
        migrated_rows=migrated,
        pruned_objects=pruned,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Move legacy robot-model file bytes into PostgreSQL."
    )
    parser.add_argument(
        "--prune-source",
        action="store_true",
        help="Delete verified legacy files after the database transaction commits.",
    )
    arguments = parser.parse_args()
    settings = get_settings()
    report = migrate_legacy_asset_content(
        postgres_dsn=settings.postgres_dsn,
        asset_root=settings.robot_model_asset_root,
        prune_source=arguments.prune_source,
    )
    print(json.dumps(asdict(report), sort_keys=True))


if __name__ == "__main__":
    main()
