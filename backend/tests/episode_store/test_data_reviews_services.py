"""Real isolated PostgreSQL guards/concurrency; fixtures are not human acceptance."""

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest

from hc_data_platform.annotation.models import ReviewDecision
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.episode_store.commit import EpisodePackageCommitter
from hc_data_platform.episode_store.review_models import (
    DataReviewLocation,
    DataReviewTagReference,
    SubmitEpisodeDataReview,
)
from hc_data_platform.episode_store.reviews import EpisodeDataReviews
from tests.episode_store.test_annotation_bindings_services import actor
from tests.episode_store.test_annotation_revision_files_services import files  # noqa: F401
from tests.episode_store.test_commit_services import scoped


def command(data, **updates):
    return SubmitEpisodeDataReview.model_validate(
        {
            "expected_content_hash": data.content_hash,
            "expected_sequence": 0,
            "decision": "RETURNED",
            "comment": "Capture does not meet the task coverage requirement.",
            **updates,
        }
    )


@pytest.fixture
def reviewed_target(services, package):
    repository, storage = services
    root, source = package
    with scoped(source):
        data = EpisodePackageCommitter(repository, storage).commit(root, source).data
        with repository.transaction() as cursor:
            cursor.execute(
                """INSERT INTO registry.organization_projects
                (organization_id,project_id,display_name) VALUES (%s,%s,'G1 review fixture')
                ON CONFLICT (organization_id,project_id) DO NOTHING""",
                (source.identity.organization_id, source.identity.project_id),
            )
    return EpisodeDataReviews(repository), source, data


def submit(reviews, source, data, cmd, key="first", actor_id="reviewer"):
    with scoped(source):
        return reviews.submit(
            source.identity, data.data_version_id, cmd, actor_id=actor_id, idempotency_key=key
        )


@pytest.mark.integration
def test_lost_response_concurrency_history_and_immutable_records(reviewed_target):
    reviews, source, data = reviewed_target
    cmd = command(
        data,
        locations=[{"start_step": 1, "end_step": 3, "comment": "Missing required task motion."}],
    )
    with scoped(source):
        empty = reviews.list(source.identity, data.data_version_id)
        assert empty.status == "NOT_REVIEWED" and empty.latest is None
    # A committed response can be lost. Every retry recovers the same durable record.
    lost = submit(reviews, source, data, cmd)
    with ThreadPoolExecutor(max_workers=4) as pool:
        retried = list(pool.map(lambda _: submit(reviews, source, data, cmd), range(4)))
    assert all(item == lost for item in retried)
    newer = submit(
        reviews,
        source,
        data,
        command(
            data, expected_sequence=1, decision="APPROVED", comment="Rechecked task requirements."
        ),
        "second",
    )
    assert newer.sequence == 2 and newer.previous_review_id == lost.review_id
    # Original retry remains stable after a later decision, and produces no extra audit.
    assert submit(reviews, source, data, cmd) == lost
    with scoped(source):
        page = reviews.list(source.identity, data.data_version_id, limit=1)
        assert page.status == "APPROVED" and page.latest == newer
        assert page.items == (newer,) and page.next_before == 2
        page = reviews.list(source.identity, data.data_version_id, limit=1, before=2)
        assert page.items == (lost,) and page.next_before is None
        assert page.latest == newer and page.current_sequence == 2
        with reviews.repository.transaction() as cursor:
            cursor.execute(
                "SELECT count(*) FROM core.audit_events WHERE organization_id=%s "
                "AND action='episode.data_review.recorded'",
                (source.identity.organization_id,),
            )
            assert cursor.fetchone()[0] == 2
        for sql in (
            "UPDATE episode_store.data_reviews SET actor_id='forged' WHERE review_id=%s",
            "DELETE FROM episode_store.data_reviews WHERE review_id=%s",
        ):
            with (
                pytest.raises(psycopg.errors.CheckViolation),
                reviews.repository.transaction() as cursor,
            ):
                cursor.execute(sql, (lost.review_id,))


@pytest.mark.integration
def test_concurrent_distinct_decisions_require_refresh(reviewed_target):
    reviews, source, data = reviewed_target

    def compete(index):
        try:
            return submit(reviews, source, data, command(data), str(index))
        except ProblemException as exc:
            return exc.problem.code

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(compete, range(4)))
    assert sum(not isinstance(item, str) for item in results) == 1
    assert results.count("EPISODE_DATA_REVIEW_SEQUENCE_CONFLICT") == 3


@pytest.mark.integration
def test_content_idempotency_range_and_scope_fences(reviewed_target):
    reviews, source, data = reviewed_target
    first = submit(reviews, source, data, command(data))
    for cmd, key, code in (
        (command(data, comment="changed"), "first", "IDEMPOTENCY_CONFLICT"),
        (command(data, expected_content_hash="0" * 64), "hash", "CONTENT_CONFLICT"),
        (command(data), "stale", "SEQUENCE_CONFLICT"),
        (
            command(
                data,
                expected_sequence=1,
                locations=[{"start_step": 0, "end_step": 4, "comment": "beyond frame count"}],
            ),
            "range",
            "LOCATION_INVALID",
        ),
    ):
        with pytest.raises(ProblemException) as failure:
            submit(reviews, source, data, cmd, key)
        assert failure.value.problem.code == "EPISODE_DATA_REVIEW_" + code
    for field in ("organization_id", "project_id", "region_code"):
        foreign = source.model_copy(
            update={"identity": source.identity.model_copy(update={field: "foreign"})}
        )
        with scoped(foreign):
            with pytest.raises(PermissionError):
                reviews.list(source.identity, data.data_version_id)
            with pytest.raises(KeyError):
                reviews.list(foreign.identity, data.data_version_id)
    with scoped(source):
        with pytest.raises(KeyError):
            reviews.list(source.identity, "dv_" + "0" * 64)
        assert reviews.list(source.identity, data.data_version_id).items == (first,)


@pytest.mark.integration
def test_database_rejects_forged_receipt_and_sequence(reviewed_target):
    reviews, source, data = reviewed_target
    first = submit(reviews, source, data, command(data))
    with scoped(source):
        for field in ("content_hash", "package_prefix", "frame_count", "sequence"):
            document = first.model_dump(mode="json")
            document.update(
                review_id="er_" + uuid4().hex, sequence=2, previous_review_id=first.review_id
            )
            if field == "sequence":
                document["sequence"] = 17
            else:
                document["data"][field] = 999 if field == "frame_count" else "forged"
            with (
                pytest.raises(psycopg.errors.CheckViolation),
                reviews.repository.transaction() as cursor,
            ):
                cursor.execute(
                    """INSERT INTO episode_store.data_reviews
                    (organization_id,project_id,region_code,dataset_id,episode_id,
                     data_version_id,review_id,sequence,actor_id,key_sha256,
                     request_sha256,document)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,2,'reviewer',%s,%s,%s::jsonb)""",
                    (
                        *source.identity.key,
                        data.data_version_id,
                        document["review_id"],
                        "a" * 64,
                        "b" * 64,
                        json.dumps(document),
                    ),
                )


@pytest.mark.integration
def test_tag_binding_and_three_reviews_remain_independent(target, files):  # noqa: F811
    packages, source, annotations, _, request = target
    _, task, _, _ = files
    reviews, data = packages.data_reviews, request.data_ref
    editor, reviewer = actor(source.identity), actor(source.identity, "annotation-reviewer")
    with scoped(source):
        before = annotations.get_history(task.task_id, editor)
        quality = packages.files.open(source.identity, data.data_version_id).document(
            "hc/quality.json"
        )
        ref = DataReviewTagReference(
            task_id=task.task_id,
            revision=1,
            revision_content_hash=before.revisions[1].content_hash,
            annotation_id="child",
        )
        for update in (
            {"revision_content_hash": "0" * 64},
            {"revision": 0},
            {"task_id": "another-task"},
            {"annotation_id": "absent"},
        ):
            location = DataReviewLocation(tag=ref.model_copy(update=update), comment="Check task.")
            with pytest.raises(ProblemException) as invalid:
                submit(reviews, source, data, command(data, locations=[location]), uuid4().hex)
            assert invalid.value.problem.code == "EPISODE_DATA_REVIEW_TAG_INVALID"
        returned = submit(
            reviews,
            source,
            data,
            command(
                data,
                locations=[
                    DataReviewLocation(tag=ref, comment="Capture differs from task requirements.")
                ],
            ),
        )
        assert annotations.get_history(task.task_id, editor) == before
        submitted = annotations.submit_for_review(
            task.task_id,
            editor,
            expected_revision=1,
            if_match=annotations.get_task(task.task_id).etag,
            idempotency_key="submit-for-review",
        )
        annotations.review(
            task.task_id,
            reviewer,
            ReviewDecision.APPROVE,
            revision=1,
            submission_id=submitted.submission_id,
            if_match=annotations.get_task(task.task_id).etag,
        )
        assert annotations.get_task(task.task_id).approved_revision == 1
        assert reviews.list(source.identity, data.data_version_id).latest == returned
        after_annotation_review = annotations.get_history(task.task_id, editor)
        approved = submit(
            reviews,
            source,
            data,
            command(data, expected_sequence=1, decision="APPROVED", comment="Capture rechecked."),
            "rechecked",
        )
        assert approved.sequence == 2
        assert annotations.get_history(task.task_id, editor) == after_annotation_review
        assert (
            packages.files.open(source.identity, data.data_version_id).document("hc/quality.json")
            == quality
        )
        assert packages.repository.get(source.identity, data.data_version_id).data == data
