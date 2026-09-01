from __future__ import annotations

from random import Random

import pytest
from pydantic import ValidationError

from hc_data_platform.annotation import (
    AnnotationActor,
    AnnotationIdempotencyConflictError,
    AnnotationObjectRelation,
    AnnotationPermissionError,
    AnnotationPolicyUnconfirmedError,
    AnnotationTag,
    AnnotationValidationError,
    InMemoryAnnotationService,
    MutualExclusionConstraint,
    ObjectRelationConstraint,
    ReviewCheckKind,
    ReviewDecision,
    SelfReviewPolicy,
    TagAttributeDefinition,
    TagAttributeType,
    TagNodeDefinition,
    TagObjectRef,
    TagSchemaDocument,
    TagSchemaImmutableError,
    TagSchemaStatus,
)


def actor(actor_id: str, *capabilities: str) -> AnnotationActor:
    return AnnotationActor(
        actor_id=actor_id,
        capabilities=frozenset(capabilities),
        project_ids=frozenset({"project-a"}),
    )


PUBLISHER = actor("publisher", "data_schema.read", "data_schema.publish", "dataset_version.publish")
ANNOTATOR = actor(
    "alice",
    "data_schema.read",
    "annotation_task.claim",
    "annotation_task.read",
    "annotation.save",
    "annotation.submit",
)
REVIEWER = actor("bob", "annotation_task.read", "annotation.review")


def schema_document() -> TagSchemaDocument:
    return TagSchemaDocument(
        nodes=(
            TagNodeDefinition(
                tag_id="event",
                code="event",
                display_name="Event",
                attributes=(
                    TagAttributeDefinition(
                        key="severity",
                        display_name="Severity",
                        value_type=TagAttributeType.ENUM,
                        required=True,
                        enum_values=("low", "high"),
                    ),
                ),
            ),
            TagNodeDefinition(
                tag_id="collision",
                code="collision",
                display_name="Collision",
                parent_tag_id="event",
            ),
            TagNodeDefinition(
                tag_id="safe",
                code="safe",
                display_name="Safe",
                parent_tag_id="event",
            ),
        ),
        mutual_exclusions=(
            MutualExclusionConstraint(
                constraint_id="collision-vs-safe",
                tag_ids=("collision", "safe"),
            ),
        ),
        object_relations=(
            ObjectRelationConstraint(
                relation_type="involves",
                source_tag_ids=("collision",),
                target_object_types=("robot",),
                required=True,
            ),
        ),
    )


def service_with_published_schema(
    *, self_review_policy: SelfReviewPolicy = SelfReviewPolicy.UNCONFIRMED
) -> InMemoryAnnotationService:
    service = InMemoryAnnotationService(self_review_policy=self_review_policy)
    service.create_tag_schema_version(
        project_id="project-a",
        schema_id="quality",
        version=1,
        name="Quality",
        document=schema_document(),
        actor=PUBLISHER,
    )
    published = service.publish_tag_schema_version(
        project_id="project-a",
        schema_id="quality",
        version=1,
        actor=PUBLISHER,
    )
    assert published.status is TagSchemaStatus.PUBLISHED
    service.create_task(
        task_id="task-tags",
        project_id="project-a",
        dataset_id="dataset-a",
        dataset_version=8,
        base_lance_version=23,
        base_step_count=100,
        tag_schema_id="quality",
        tag_schema_version=1,
        rollout_id="rollout-a",
    )
    service.claim("task-tags", ANNOTATOR)
    return service


def tag(
    annotation_id: str,
    tag_id: str = "collision",
    *,
    path: tuple[str, ...] | None = None,
    start: int = 10,
    end: int = 20,
    attributes: dict[str, str] | None = None,
    relation: bool = True,
) -> AnnotationTag:
    relations = (
        (
            AnnotationObjectRelation(
                relation_type="involves",
                target=TagObjectRef(object_id="robot-7", object_type="robot"),
            ),
        )
        if relation and tag_id == "collision"
        else ()
    )
    return AnnotationTag(
        annotation_id=annotation_id,
        tag_id=tag_id,
        path=path or ("event", tag_id),
        start_step=start,
        end_step=end,
        attributes=attributes if attributes is not None else {"severity": "high"},
        subject=(
            TagObjectRef(object_id="episode-object", object_type="event") if relations else None
        ),
        relations=relations,
    )


def save_tags(service: InMemoryAnnotationService, *tags: AnnotationTag):
    task = service.get_task("task-tags")
    return service.save_draft(
        "task-tags",
        ANNOTATOR,
        (),
        tags=tags,
        expected_revision=task.current_revision,
        if_match=task.etag,
        client_mutation_id=f"save-{task.current_revision + 1}",
    )


def test_schema_cycle_is_rejected_and_depth_is_not_product_capped() -> None:
    with pytest.raises(ValidationError, match="cycle"):
        TagSchemaDocument(
            nodes=(
                TagNodeDefinition(tag_id="a", code="a", display_name="A", parent_tag_id="b"),
                TagNodeDefinition(tag_id="b", code="b", display_name="B", parent_tag_id="a"),
            )
        )

    # Generated depths exercise the OPEN-07 extension point without encoding a max depth.
    for depth in (1, 2, 8, 32, 128):
        document = TagSchemaDocument(
            nodes=tuple(
                TagNodeDefinition(
                    tag_id=f"n{index}",
                    code=f"n{index}",
                    display_name=f"Node {index}",
                    parent_tag_id=None if index == 0 else f"n{index - 1}",
                )
                for index in range(depth)
            )
        )
        assert document.path_for(f"n{depth - 1}") == tuple(f"n{index}" for index in range(depth))


def test_published_schema_version_is_immutable() -> None:
    service = service_with_published_schema()
    published = service.get_tag_schema_version(
        project_id="project-a", schema_id="quality", version=1, actor=PUBLISHER
    )
    assert (
        service.publish_tag_schema_version(
            project_id="project-a",
            schema_id="quality",
            version=1,
            actor=PUBLISHER,
        )
        == published
    )
    with pytest.raises(TagSchemaImmutableError):
        service.create_tag_schema_version(
            project_id="project-a",
            schema_id="quality",
            version=1,
            name="Changed",
            document=TagSchemaDocument(nodes=()),
            actor=PUBLISHER,
        )


@pytest.mark.parametrize(
    ("invalid_tag", "check"),
    (
        (tag("bad-path", path=("collision",)), ReviewCheckKind.HIERARCHY),
        (tag("past-end", end=101), ReviewCheckKind.BOUNDARY),
        (tag("missing-attribute", attributes={}), ReviewCheckKind.REQUIRED_ATTRIBUTES),
        (tag("missing-relation", relation=False), ReviewCheckKind.OBJECT_RELATIONS),
    ),
)
def test_tag_validation_negative_dimensions(
    invalid_tag: AnnotationTag, check: ReviewCheckKind
) -> None:
    service = service_with_published_schema()
    with pytest.raises(AnnotationValidationError) as captured:
        save_tags(service, invalid_tag)
    assert captured.value.problem.details == {"check": check.value}


def test_mutual_exclusion_uses_half_open_overlap_property() -> None:
    service = service_with_published_schema()
    rng = Random(20260817)
    for index in range(30):
        start = rng.randrange(0, 80)
        width = rng.randrange(1, 20)
        boundary = min(start + width, 99)
        # Adjacent [start,boundary) and [boundary,boundary+1) never overlap.
        save_tags(
            service,
            tag(f"left-{index}", start=start, end=boundary),
            tag(
                f"right-{index}",
                tag_id="safe",
                start=boundary,
                end=boundary + 1,
                relation=False,
            ),
        )

    service = service_with_published_schema()
    with pytest.raises(AnnotationValidationError) as captured:
        save_tags(
            service,
            tag("left", start=10, end=20),
            tag("right", tag_id="safe", start=19, end=30, relation=False),
        )
    assert captured.value.problem.details == {"check": ReviewCheckKind.MUTUAL_EXCLUSION.value}


def test_manual_interval_tags_can_nest_without_a_preset_schema_node() -> None:
    service = service_with_published_schema()
    parent = AnnotationTag(
        annotation_id="manual-basketball",
        tag_id="manual-node-basketball",
        label="打篮球",
        parent_annotation_id=None,
        path=("manual-node-basketball",),
        start_step=10,
        end_step=80,
    )
    child = AnnotationTag(
        annotation_id="manual-dribbling",
        tag_id="manual-node-dribbling",
        label="运球",
        parent_annotation_id=parent.annotation_id,
        path=(*parent.path, "manual-node-dribbling"),
        start_step=20,
        end_step=40,
    )

    revision = save_tags(service, parent, child)

    assert revision.tags == (parent, child)


def test_manual_child_tag_can_cross_its_parent_interval() -> None:
    service = service_with_published_schema()
    parent = AnnotationTag(
        annotation_id="manual-basketball",
        tag_id="manual-node-basketball",
        label="打篮球",
        path=("manual-node-basketball",),
        start_step=10,
        end_step=80,
    )
    outside_child = AnnotationTag(
        annotation_id="manual-dribbling",
        tag_id="manual-node-dribbling",
        label="运球",
        parent_annotation_id=parent.annotation_id,
        path=(*parent.path, "manual-node-dribbling"),
        start_step=5,
        end_step=40,
    )

    revision = save_tags(service, parent, outside_child)

    assert revision.tags == (parent, outside_child)


def test_submit_is_idempotent_and_contains_all_review_checks_and_fixed_versions() -> None:
    service = service_with_published_schema()
    revision = save_tags(service, tag("collision-1"))
    task = service.get_task("task-tags")
    first = service.submit_for_review(
        "task-tags",
        ANNOTATOR,
        expected_revision=revision.revision,
        if_match=task.etag,
        idempotency_key="submit-1",
    )
    replay = service.submit_for_review(
        "task-tags",
        ANNOTATOR,
        expected_revision=revision.revision,
        if_match=task.etag,
        idempotency_key="submit-1",
    )
    assert replay == first
    assert first.episode_version == 1
    assert {check.kind for check in first.checks} == set(ReviewCheckKind)
    assert first.base_lance_version == 23
    assert (first.tag_schema_id, first.tag_schema_version) == ("quality", 1)
    assert first.revision_content_hash == revision.content_hash

    with pytest.raises(AnnotationIdempotencyConflictError):
        service.submit_for_review(
            "task-tags",
            ANNOTATOR,
            expected_revision=revision.revision,
            if_match=service.get_task("task-tags").etag,
            idempotency_key="submit-1",
        )


def test_review_checks_exact_submission_and_open_08_policy_is_configurable() -> None:
    for policy, expected_error in (
        (SelfReviewPolicy.UNCONFIRMED, AnnotationPolicyUnconfirmedError),
        (SelfReviewPolicy.DENY, AnnotationPermissionError),
    ):
        service = InMemoryAnnotationService(self_review_policy=policy)
        dual_role = actor(
            "dual",
            "annotation_task.read",
            "annotation_task.claim",
            "annotation.save",
            "annotation.submit",
            "annotation.review",
        )
        service.create_task(
            task_id="self-review",
            project_id="project-a",
            dataset_id="dataset-a",
            dataset_version=1,
            rollout_id=f"rollout-{policy.value}",
        )
        service.claim("self-review", dual_role)
        submission = service.submit_for_review(
            "self-review",
            dual_role,
            expected_revision=0,
            if_match=service.get_task("self-review").etag,
            idempotency_key="submit-self",
        )
        with pytest.raises(expected_error):
            service.review(
                "self-review",
                dual_role,
                ReviewDecision.APPROVE,
                revision=0,
                submission_id=submission.submission_id,
                if_match=service.get_task("self-review").etag,
            )

    allowed = InMemoryAnnotationService(self_review_policy=SelfReviewPolicy.ALLOW)
    dual_role = actor(
        "dual",
        "annotation_task.read",
        "annotation_task.claim",
        "annotation.save",
        "annotation.submit",
        "annotation.review",
    )
    allowed.create_task(
        task_id="self-review",
        project_id="project-a",
        dataset_id="dataset-a",
        dataset_version=1,
        rollout_id="rollout-allow",
    )
    allowed.claim("self-review", dual_role)
    submission = allowed.submit_for_review(
        "self-review",
        dual_role,
        expected_revision=0,
        if_match=allowed.get_task("self-review").etag,
        idempotency_key="submit-self",
    )
    assert (
        allowed.review(
            "self-review",
            dual_role,
            ReviewDecision.APPROVE,
            revision=0,
            submission_id=submission.submission_id,
            if_match=allowed.get_task("self-review").etag,
        )
        is not None
    )
