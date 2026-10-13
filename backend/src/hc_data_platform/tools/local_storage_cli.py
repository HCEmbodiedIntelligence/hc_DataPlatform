"""Host-operator storage commands, using the same authorization and audit as the UI.

Run only through the local deployment's API container. Docker/DB administration
access is required; this is not a remote authentication or password reset endpoint.
"""

from __future__ import annotations

import argparse
import json
import secrets
from contextlib import contextmanager
from urllib.parse import quote

import httpx

from hc_data_platform.core.config import get_settings
from hc_data_platform.core.dbapi import normalize_postgres_dsn
from hc_data_platform.security.access_models import SessionIssued
from hc_data_platform.security.access_postgres import PostgresAccessRepository
from hc_data_platform.security.access_service import AccessService


@contextmanager
def local_operator_client(username: str | None = None):
    import psycopg

    settings = get_settings()
    if settings.environment != "local":
        raise RuntimeError("This host-operator helper is restricted to the local deployment.")
    dsn = normalize_postgres_dsn(settings.postgres_dsn)
    with psycopg.connect(dsn) as connection:
        rows = connection.execute(
            """SELECT a.principal_id::text, a.password_hash, a.credential_revision
               FROM access_control.accounts a
               WHERE a.status = 'ACTIVE' AND (%s::text IS NULL OR a.canonical_username = %s)
                 AND EXISTS (SELECT 1 FROM access_control.platform_capability_grants g
                     WHERE g.principal_id = a.principal_id AND g.active
                       AND g.capability_key = 'platform.admin')""",
            (username, username),
        ).fetchall()
    if len(rows) != 1:
        raise RuntimeError("Specify --username for exactly one active platform administrator.")
    principal, password_hash, revision = rows[0]
    repository = PostgresAccessRepository(lambda: psycopg.connect(dsn))
    token = AccessService.TOKEN_PREFIX + secrets.token_urlsafe(32)
    digest = AccessService.token_hash(token)
    request_id = "local-storage-" + secrets.token_hex(12)
    issued = repository.create_session(
        principal_id=principal,
        token_hash=digest,
        expected_password_hash=password_hash,
        expected_credential_revision=int(revision),
        request_id=request_id,
    )
    if not isinstance(issued, SessionIssued):
        raise RuntimeError(
            "Could not create a temporary operator session; existing sessions were preserved."
        )
    try:
        with httpx.Client(
            base_url="http://127.0.0.1:8000/api/v1",
            timeout=60,
            headers={"Authorization": "Bearer " + token, "X-Request-Id": request_id},
            trust_env=False,
        ) as client:
            yield client
    finally:
        repository.revoke_session(token_hash=digest, request_id=request_id + "-close")


def checked(response: httpx.Response) -> dict:
    if not response.is_success:
        try:
            error = response.json()
        except ValueError:
            raise RuntimeError(f"Platform returned HTTP {response.status_code}") from None
        raise RuntimeError(json.dumps(error, ensure_ascii=False))
    return response.json()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["objects", "trash", "restore"])
    parser.add_argument("object_id", nargs="?")
    parser.add_argument("--organization")
    parser.add_argument("--project")
    parser.add_argument("--username")
    parser.add_argument("--reason")
    args = parser.parse_args()
    if args.action != "objects" and (not args.object_id or not args.reason):
        parser.error("trash/restore require object_id and --reason (at least 3 characters)")
    with local_operator_client(args.username) as client:
        projects = checked(client.get("/platform/projects"))["items"]
        selected = [
            p
            for p in projects
            if (args.project is None or p["project_id"] == args.project)
            and (args.organization is None or p["organization_id"] == args.organization)
        ]
        if len(selected) != 1:
            raise RuntimeError("Use --organization and --project to select one project.")
        project = selected[0]
        client.headers.update(
            {"X-Organization-Id": project["organization_id"], "X-Project-Id": project["project_id"]}
        )
        base = "/projects/" + quote(project["project_id"], safe="") + "/storage/objects"
        if args.action == "objects":
            cursor = None
            count = 0
            while True:
                params = {"limit": "100", **({"cursor": cursor} if cursor else {})}
                page = checked(client.get(base, params=params))
                for item in page["items"]:
                    print(
                        json.dumps(
                            {
                                k: item[k]
                                for k in (
                                    "object_id",
                                    "display_key",
                                    "physical_bytes",
                                    "object_role",
                                    "status",
                                    "allowed_actions",
                                )
                            },
                            ensure_ascii=False,
                        )
                    )
                    count += 1
                if not page["page_info"]["has_next_page"]:
                    break
                cursor = page["page_info"]["end_cursor"]
            print(f"平台可管理对象：{count}。原始文件、受引用和保留期保护的对象不能直接删除。")
            return
        path = base + "/" + quote(args.object_id, safe="")
        current = checked(client.get(path))
        action = args.action.upper()
        if action not in current["allowed_actions"]:
            raise RuntimeError(
                "平台禁止此操作：对象正在使用、受保护，或状态不允许。未修改任何文件。"
            )
        result = checked(
            client.post(
                path + ":" + args.action,
                json={"reason": args.reason},
                headers={"If-Match": current["etag"], "Idempotency-Key": secrets.token_hex(16)},
            )
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.action == "trash":
            print("已移入平台回收站；回收站仍占空间，后续清理按平台生命周期策略执行。")


if __name__ == "__main__":
    main()
