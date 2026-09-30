"""Publication collaborators bound to the caller's atomic transaction.

Implementations must neither commit independently nor send external messages.
The repository commits only after all three phases succeed. SQL transaction
binding belongs to the adapter/composition root, never these domain ports.
"""

from typing import Protocol

from .models import PublishedDatasetManifestV1


class PublicationSideEffects(Protocol):
    def prepare_sources(self, manifest: PublishedDatasetManifestV1) -> None: ...

    def project_release(self, manifest: PublishedDatasetManifestV1) -> None: ...

    def notify_owner(self, manifest: PublishedDatasetManifestV1) -> None: ...


class PublicationOnly:
    """Explicit composition for a publication store without product projections."""

    def prepare_sources(self, manifest: PublishedDatasetManifestV1) -> None:
        pass

    def project_release(self, manifest: PublishedDatasetManifestV1) -> None:
        pass

    def notify_owner(self, manifest: PublishedDatasetManifestV1) -> None:
        pass


def publication_only(transaction: object) -> PublicationSideEffects:
    """Select the limited composition explicitly; production installs its own."""
    return PublicationOnly()
