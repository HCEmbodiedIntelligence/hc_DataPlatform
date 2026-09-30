"""Narrow transaction-bound inbox write contract, independent of its event source."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from .access_models import AccountNotificationKind, AccountNotificationResourceType


@dataclass(frozen=True)
class AccountNotificationCommand:
    recipient_id: UUID
    organization_id: str
    project_id: str
    kind: AccountNotificationKind
    resource_type: AccountNotificationResourceType
    resource_id: str
    event_key: str


class AccountNotificationWriter(Protocol):
    def put_once(self, notification: AccountNotificationCommand) -> None:
        """Store once by recipient/event identity in the enclosing transaction."""
        ...
