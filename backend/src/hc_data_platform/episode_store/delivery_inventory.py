"""Canonical frozen delivery inventory, independent of execution services."""

from .collection_delivery_models import CollectionDeliveryPlan
from .models import canonical


def inventory_document(plan):
    return canonical(
        {
            "format": "hc-episode-delivery/1",
            "job_id": plan.job_id,
            "plan": plan.model_dump(mode="json"),
            "is_reassembled_collection": isinstance(plan, CollectionDeliveryPlan),
            "model_depth_training_support": "NOT_VALIDATED",
        }
    )
