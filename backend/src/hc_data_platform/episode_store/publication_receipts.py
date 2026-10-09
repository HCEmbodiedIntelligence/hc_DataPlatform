"""Verify immutable package and collection receipts for interrupted ingest recovery."""

from .catalog_models import CollectionVersion
from .models import CommitReceipt, DataVersionRef


class EpisodePublicationReceipts:
    def __init__(self, repository, catalog):
        self.repository, self.catalog = repository, catalog

    def published_receipt(
        self, data: DataVersionRef, collection: CollectionVersion
    ) -> CommitReceipt:
        receipt = self.repository.get(data.identity, data.data_version_id)
        published = self.catalog.version_snapshot(
            data.identity.dataset_id,
            version=collection.version,
            project_id=data.identity.project_id,
        )
        if receipt.data != data or published != collection:
            raise ValueError("robot publication differs from durable receipt")
        return receipt
