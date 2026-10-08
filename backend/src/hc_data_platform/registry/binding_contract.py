"""Published model facts used by an instance-binding transaction."""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class PublishedModelIdentity:
    organization_id: str
    model_id: str
    version_id: str


class PublishedModelForBinding(Protocol):
    def __call__(
        self, cursor: Any, organization_id: str, version_id: str
    ) -> PublishedModelIdentity: ...
