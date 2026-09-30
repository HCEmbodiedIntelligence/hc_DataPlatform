from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from hc_data_platform.core.context import current_request_context

from .notification_writer import AccountNotificationCommand

if TYPE_CHECKING:
    from psycopg import Cursor


class PostgresAccountNotificationWriter:
    def __init__(self, cursor: Cursor[Any]) -> None:
        self._cursor = cursor

    def put_once(self, notification: AccountNotificationCommand) -> None:
        cursor = self._cursor
        context = current_request_context()
        cursor.execute(
            "SELECT set_config('app.subject_id', %s, true)", (str(notification.recipient_id),)
        )
        try:
            cursor.execute(
                """
                INSERT INTO access_control.account_notifications (
                    notification_id, recipient_id, kind, organization_id, project_id,
                    access_request_id, resource_type, resource_id, event_key, state
                ) VALUES (
                    %s::uuid, %s::uuid, %s, %s, %s, NULL, %s, %s, %s, 'UNREAD'
                )
                ON CONFLICT (recipient_id, event_key) DO NOTHING
                """,
                (
                    str(uuid4()),
                    str(notification.recipient_id),
                    notification.kind.value,
                    notification.organization_id,
                    notification.project_id,
                    notification.resource_type.value,
                    notification.resource_id,
                    notification.event_key,
                ),
            )
        except Exception:
            # An aborted transaction cannot restore settings; preserve the original
            # exception and let the enclosing unit of work roll everything back.
            raise
        else:
            cursor.execute(
                "SELECT set_config('app.subject_id', %s, true)", (context.subject_id or "",)
            )
