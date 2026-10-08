"""Selection validation and exact historical idempotency request compatibility."""

import pytest
from pydantic import ValidationError

from hc_data_platform.episode_store.release_models import PublishCollectionRelease
from hc_data_platform.episode_store.releases import CollectionReleases


def test_historical_request_identity_does_not_gain_a_null_field():
    old = {
        "dataset_version": "release-1",
        "collection_version_id": "cv_" + "a" * 64,
        "annotation_tasks": {},
    }
    command = PublishCollectionRelease(**old, expected_content_hash="b" * 64)
    assert CollectionReleases.request_identity(command) == old
    assert CollectionReleases.request_identity(command.model_copy(update={"episode_ids": ()})) == {
        **old,
        "episode_ids": [],
    }


@pytest.mark.parametrize("selection", [("a", "a"), ("",)])
def test_invalid_selection_rejected(selection):
    with pytest.raises(ValidationError, match="unique nonempty"):
        PublishCollectionRelease(
            dataset_version="release-1",
            collection_version_id="cv_" + "a" * 64,
            episode_ids=selection,
        )


def test_selection_order_is_canonical_for_idempotent_retry():
    def command(ids):
        return PublishCollectionRelease(
            dataset_version="release-1",
            collection_version_id="cv_" + "a" * 64,
            episode_ids=ids,
        )

    assert CollectionReleases.request_identity(command(("b", "a"))) == (
        CollectionReleases.request_identity(command(("a", "b")))
    )
