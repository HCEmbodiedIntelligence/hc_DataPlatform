"""Tag Schema hashing and review validation shared by API and workflows."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from .models import (
    AnnotationOperation,
    AnnotationReviewCheck,
    AnnotationTag,
    ReviewCheckKind,
    TagAttributeDefinition,
    TagAttributeType,
    TagNodeDefinition,
    TagSchemaDocument,
    TagSchemaStatus,
    TagSchemaVersion,
)


class TagValidationIssue(ValueError):
    def __init__(self, kind: ReviewCheckKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


def stable_hash(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def schema_content_hash(document: TagSchemaDocument) -> str:
    return stable_hash(document)


def revision_content_hash(
    *,
    base_lance_version: int,
    tag_schema_id: str,
    tag_schema_version: int,
    tags: Sequence[AnnotationTag],
    operations: Sequence[AnnotationOperation],
) -> str:
    return stable_hash(
        {
            "base_lance_version": base_lance_version,
            "tag_schema_id": tag_schema_id,
            "tag_schema_version": tag_schema_version,
            "tags": [tag.model_dump(mode="json") for tag in tags],
            "operations": [operation.model_dump(mode="json") for operation in operations],
        }
    )


def default_flat_schema(project_id: str) -> TagSchemaVersion:
    """Built-in published schema for interval-only annotation tasks."""

    document = TagSchemaDocument(nodes=())
    return TagSchemaVersion(
        schema_id="default-flat",
        project_id=project_id,
        name="Default interval annotation",
        version=1,
        status=TagSchemaStatus.PUBLISHED,
        document=document,
        content_hash=schema_content_hash(document),
        created_by="system",
        created_at=_epoch(),
        published_by="system",
        published_at=_epoch(),
    )


def _epoch() -> datetime:
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def validate_tag_revision(
    *,
    schema: TagSchemaVersion,
    base_step_count: int | None,
    tags: Sequence[AnnotationTag],
    operations: Sequence[AnnotationOperation],
) -> tuple[AnnotationReviewCheck, ...]:
    """Validate all six review dimensions and return their auditable checklist."""

    if schema.status.value != TagSchemaStatus.PUBLISHED.value:
        raise TagValidationIssue(
            ReviewCheckKind.SCHEMA_VERSION,
            "the pinned Tag Schema version is not published",
        )
    if schema.content_hash != schema_content_hash(schema.document):
        raise TagValidationIssue(
            ReviewCheckKind.SCHEMA_VERSION,
            "the pinned Tag Schema content hash does not match its document",
        )

    node_by_id = {node.tag_id: node for node in schema.document.nodes}
    annotation_ids = [tag.annotation_id for tag in tags]
    if len(annotation_ids) != len(set(annotation_ids)):
        raise TagValidationIssue(
            ReviewCheckKind.HIERARCHY,
            "annotation_id must be unique within a revision",
        )
    tag_by_annotation_id = {tag.annotation_id: tag for tag in tags}
    for tag in tags:
        if tag.label is not None:
            parent = (
                tag_by_annotation_id.get(tag.parent_annotation_id)
                if tag.parent_annotation_id is not None
                else None
            )
            if tag.parent_annotation_id is not None and parent is None:
                raise TagValidationIssue(
                    ReviewCheckKind.HIERARCHY,
                    f"Tag {tag.annotation_id!r} references a missing parent interval",
                )
            expected_path = (*parent.path, tag.tag_id) if parent else (tag.tag_id,)
            if tag.path != expected_path:
                raise TagValidationIssue(
                    ReviewCheckKind.HIERARCHY,
                    f"Tag {tag.annotation_id!r} path does not match its interval ancestry",
                )
            continue
        try:
            expected_path = schema.document.path_for(tag.tag_id)
        except KeyError as exc:
            raise TagValidationIssue(
                ReviewCheckKind.HIERARCHY,
                f"Tag {tag.tag_id!r} does not exist in the pinned schema",
            ) from exc
        if tag.path != expected_path:
            raise TagValidationIssue(
                ReviewCheckKind.HIERARCHY,
                f"Tag {tag.annotation_id!r} path does not match its schema ancestry",
            )

    for tag in tags:
        if tag.label is None:
            continue
        seen: set[str] = set()
        current: AnnotationTag | None = tag
        while current is not None and current.label is not None:
            if current.annotation_id in seen:
                raise TagValidationIssue(
                    ReviewCheckKind.HIERARCHY,
                    "manual Tag interval hierarchy contains a cycle",
                )
            seen.add(current.annotation_id)
            current = (
                tag_by_annotation_id.get(current.parent_annotation_id)
                if current.parent_annotation_id is not None
                else None
            )

    ranged: list[tuple[str, int, int]] = [
        (tag.annotation_id, tag.start_step, tag.end_step) for tag in tags
    ] + [
        (operation.operation_id, operation.start_step, operation.end_step)
        for operation in operations
    ]
    if ranged and base_step_count is None:
        raise TagValidationIssue(
            ReviewCheckKind.BOUNDARY,
            "base_step_count is required before interval content can be reviewed",
        )
    if base_step_count is not None:
        for identifier, start, end in ranged:
            if start < 0 or start >= end or end > base_step_count:
                raise TagValidationIssue(
                    ReviewCheckKind.BOUNDARY,
                    f"{identifier!r} must stay inside [0,{base_step_count})",
                )

    for tag in tags:
        if tag.label is not None:
            continue
        definitions = _attributes_for_path(tag.path, node_by_id)
        unknown = set(tag.attributes).difference(definitions)
        if unknown:
            raise TagValidationIssue(
                ReviewCheckKind.REQUIRED_ATTRIBUTES,
                f"Tag {tag.annotation_id!r} contains unknown attributes: {sorted(unknown)}",
            )
        missing = {
            key for key, definition in definitions.items() if definition.required
        }.difference(tag.attributes)
        if missing:
            raise TagValidationIssue(
                ReviewCheckKind.REQUIRED_ATTRIBUTES,
                f"Tag {tag.annotation_id!r} is missing required attributes: {sorted(missing)}",
            )
        for key, value in tag.attributes.items():
            if not _matches_attribute(definitions[key], value):
                raise TagValidationIssue(
                    ReviewCheckKind.REQUIRED_ATTRIBUTES,
                    f"Tag {tag.annotation_id!r} attribute {key!r} has the wrong value type",
                )

    for exclusion_constraint in schema.document.mutual_exclusions:
        candidates = [tag for tag in tags if tag.tag_id in exclusion_constraint.tag_ids]
        for index, left in enumerate(candidates):
            for right in candidates[index + 1 :]:
                if left.tag_id == right.tag_id:
                    continue
                if _overlaps(left.start_step, left.end_step, right.start_step, right.end_step):
                    raise TagValidationIssue(
                        ReviewCheckKind.MUTUAL_EXCLUSION,
                        "mutually exclusive Tags overlap under "
                        f"{exclusion_constraint.constraint_id!r}",
                    )

    relation_by_type = {
        relation_constraint.relation_type: relation_constraint
        for relation_constraint in schema.document.object_relations
    }
    for tag in tags:
        if tag.label is not None:
            continue
        for relation in tag.relations:
            relation_constraint = relation_by_type.get(relation.relation_type)
            if relation_constraint is None or tag.tag_id not in relation_constraint.source_tag_ids:
                raise TagValidationIssue(
                    ReviewCheckKind.OBJECT_RELATIONS,
                    f"Tag {tag.annotation_id!r} uses an undeclared object relation",
                )
            if relation.target.object_type not in relation_constraint.target_object_types:
                raise TagValidationIssue(
                    ReviewCheckKind.OBJECT_RELATIONS,
                    f"Tag {tag.annotation_id!r} relation target type is not allowed",
                )
            if tag.subject is None:
                raise TagValidationIssue(
                    ReviewCheckKind.OBJECT_RELATIONS,
                    f"Tag {tag.annotation_id!r} requires a source object",
                )
        for required_relation in schema.document.object_relations:
            has_required_relation = any(
                relation.relation_type == required_relation.relation_type
                for relation in tag.relations
            )
            if (
                required_relation.required
                and tag.tag_id in required_relation.source_tag_ids
                and not has_required_relation
            ):
                raise TagValidationIssue(
                    ReviewCheckKind.OBJECT_RELATIONS,
                    (
                        f"Tag {tag.annotation_id!r} is missing relation "
                        f"{required_relation.relation_type!r}"
                    ),
                )

    manual_count = sum(tag.label is not None for tag in tags)
    evidence = {
        ReviewCheckKind.HIERARCHY: (
            f"{len(tags)} Tag paths are valid; {manual_count} manually named intervals "
            "use independent time ranges"
        ),
        ReviewCheckKind.BOUNDARY: f"all intervals are valid inside {base_step_count} steps",
        ReviewCheckKind.REQUIRED_ATTRIBUTES: "required and typed attributes are satisfied",
        ReviewCheckKind.MUTUAL_EXCLUSION: "no mutually exclusive Tags overlap",
        ReviewCheckKind.OBJECT_RELATIONS: "object relation constraints are satisfied",
        ReviewCheckKind.SCHEMA_VERSION: (
            f"{schema.schema_id}@{schema.version} is published as {schema.content_hash}"
        ),
    }
    return tuple(
        AnnotationReviewCheck(kind=kind, evidence=evidence[kind]) for kind in ReviewCheckKind
    )


def _attributes_for_path(
    path: Sequence[str], node_by_id: dict[str, TagNodeDefinition]
) -> dict[str, TagAttributeDefinition]:
    result: dict[str, TagAttributeDefinition] = {}
    for tag_id in path:
        for attribute in node_by_id[tag_id].attributes:
            existing = result.get(attribute.key)
            if existing is not None and existing != attribute:
                raise TagValidationIssue(
                    ReviewCheckKind.SCHEMA_VERSION,
                    f"attribute {attribute.key!r} is ambiguously redefined along a Tag path",
                )
            result[attribute.key] = attribute
    return result


def _matches_attribute(definition: TagAttributeDefinition, value: object) -> bool:
    if definition.value_type == TagAttributeType.STRING:
        return type(value) is str
    if definition.value_type == TagAttributeType.INTEGER:
        return type(value) is int
    if definition.value_type == TagAttributeType.NUMBER:
        return type(value) in {int, float}
    if definition.value_type == TagAttributeType.BOOLEAN:
        return type(value) is bool
    return type(value) is str and value in definition.enum_values


def _overlaps(left_start: int, left_end: int, right_start: int, right_end: int) -> bool:
    return max(left_start, right_start) < min(left_end, right_end)
