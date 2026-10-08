"""Append-only version-bound human data review on the existing scoped connection."""

from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from hc_data_platform.core.errors import problem

from .models import CommitReceipt, canonical, digest
from .review_models import EpisodeDataReview, EpisodeDataReviewPage
from .scope import assert_scope


def failure(code, detail, status=409):
    return problem(status=status, code=code, title="Episode data review", detail=detail)


class EpisodeDataReviews:
    def __init__(self, repository):
        self.repository = repository

    @staticmethod
    def _version(cursor, identity, version_id, *, lock=False):
        assert_scope(identity)
        cursor.execute(
            """SELECT receipt_document FROM episode_store.versions
            WHERE organization_id=%s AND project_id=%s AND region_code=%s
              AND dataset_id=%s AND episode_id=%s AND data_version_id=%s
              AND status='COMMITTED'"""
            + (" FOR UPDATE" if lock else ""),
            (*identity.key, version_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise KeyError("committed Episode version not found")
        data = CommitReceipt.model_validate(row[0]).data
        if data.identity != identity:
            raise PermissionError("Episode review target differs from scoped identity")
        return data

    @staticmethod
    def _latest(cursor, data):
        cursor.execute(
            """SELECT document FROM episode_store.data_reviews
            WHERE organization_id=%s AND project_id=%s AND region_code=%s
              AND data_version_id=%s ORDER BY sequence DESC LIMIT 1""",
            (*data.identity.scope, data.data_version_id),
        )
        row = cursor.fetchone()
        return EpisodeDataReview.model_validate(row[0]) if row else None

    def list(self, identity, version_id, *, before=None, limit=25):
        if not 1 <= limit <= 100 or (before is not None and before < 1):
            raise ValueError("invalid review page")
        with self.repository.transaction() as cursor:
            data = self._version(cursor, identity, version_id)
            latest = self._latest(cursor, data)
            # Bound pages to one observed head even if another review commits
            # between these statements under PostgreSQL READ COMMITTED.
            sequence = latest.sequence if latest else 0
            cursor.execute(
                """SELECT document FROM episode_store.data_reviews
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                  AND data_version_id=%s AND sequence <= %s
                  AND (%s::bigint IS NULL OR sequence < %s)
                ORDER BY sequence DESC LIMIT %s""",
                (*identity.scope, version_id, sequence, before, before, limit + 1),
            )
            items = tuple(EpisodeDataReview.model_validate(row[0]) for row in cursor.fetchall())
            return EpisodeDataReviewPage(
                data=data,
                status=latest.decision if latest else "NOT_REVIEWED",
                current_sequence=sequence,
                latest=latest,
                items=items[:limit],
                next_before=items[limit - 1].sequence if len(items) > limit else None,
            )

    @staticmethod
    def _locations(cursor, data, locations):
        for location in locations:
            if location.end_step is not None and location.end_step > data.frame_count:
                raise failure(
                    "EPISODE_DATA_REVIEW_LOCATION_INVALID", "Range exceeds Episode frames.", 422
                )
            tag = location.tag
            if tag is None:
                continue
            cursor.execute(
                """SELECT r.tags FROM annotation.annotation_revisions r
                JOIN annotation.annotation_tasks t ON t.task_id=r.task_id
                WHERE t.organization_id=%s AND t.project_id=%s AND t.region_code=%s
                  AND r.task_id=%s AND r.revision=%s AND r.content_hash=%s
                  AND r.data_ref=%s::jsonb AND t.data_ref=r.data_ref""",
                (
                    *data.identity.scope,
                    tag.task_id,
                    tag.revision,
                    tag.revision_content_hash,
                    data.model_dump_json(),
                ),
            )
            row = cursor.fetchone()
            if row is None or not any(
                item.get("annotation_id") == tag.annotation_id for item in row[0]
            ):
                raise failure(
                    "EPISODE_DATA_REVIEW_TAG_INVALID",
                    "Tag is not present in the specified revision of this data version.",
                    422,
                )

    def submit(self, identity, version_id, command, *, actor_id, idempotency_key):
        if not actor_id or not 1 <= len(idempotency_key) <= 256 or not idempotency_key.strip():
            raise ValueError("actor and nonblank idempotency key are required")
        request_hash = digest(command.model_dump(mode="json"))
        key_hash = sha256(idempotency_key.encode()).hexdigest()
        with self.repository.transaction() as cursor:
            # Lock the persisted version, including when no first review exists.
            data = self._version(cursor, identity, version_id, lock=True)
            cursor.execute(
                """SELECT request_sha256, document FROM episode_store.data_reviews
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                  AND data_version_id=%s AND actor_id=%s AND key_sha256=%s""",
                (*identity.scope, version_id, actor_id, key_hash),
            )
            previous_request = cursor.fetchone()
            if previous_request:
                if previous_request[0] != request_hash:
                    raise failure(
                        "EPISODE_DATA_REVIEW_IDEMPOTENCY_CONFLICT",
                        "Use a new idempotency key for a different review command.",
                    )
                return EpisodeDataReview.model_validate(previous_request[1])
            if command.expected_content_hash != data.content_hash:
                raise failure(
                    "EPISODE_DATA_REVIEW_CONTENT_CONFLICT", "Review content hash differs."
                )
            latest = self._latest(cursor, data)
            current_sequence = latest.sequence if latest else 0
            if command.expected_sequence != current_sequence:
                raise failure(
                    "EPISODE_DATA_REVIEW_SEQUENCE_CONFLICT",
                    "Another review was recorded. Refresh before submitting a new decision.",
                )
            self._locations(cursor, data, command.locations)
            review = EpisodeDataReview(
                review_id="er_" + uuid4().hex,
                data=data,
                sequence=current_sequence + 1,
                previous_review_id=latest.review_id if latest else None,
                decision=command.decision,
                actor_id=actor_id,
                created_at=datetime.now(UTC),
                comment=command.comment,
                locations=command.locations,
            )
            cursor.execute(
                """INSERT INTO episode_store.data_reviews
                (organization_id,project_id,region_code,dataset_id,episode_id,data_version_id,
                 review_id,sequence,actor_id,key_sha256,request_sha256,document)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                (
                    *identity.key,
                    version_id,
                    review.review_id,
                    review.sequence,
                    actor_id,
                    key_hash,
                    request_hash,
                    review.model_dump_json(),
                ),
            )
            cursor.execute(
                """INSERT INTO core.audit_events
                (audit_id,organization_id,project_id,region_code,actor_id,action,
                 resource_type,resource_id,request_id,details,occurred_at)
                VALUES (%s,%s,%s,%s,%s,'episode.data_review.recorded','episode_data_review',%s,
                  NULLIF(current_setting('app.request_id',true),''),%s::jsonb,%s)""",
                (
                    uuid4(),
                    *identity.scope,
                    actor_id,
                    review.review_id,
                    canonical(
                        {
                            "data_version_id": version_id,
                            "content_hash": data.content_hash,
                            "review_sha256": digest(review.model_dump(mode="json")),
                            "sequence": review.sequence,
                            "decision": review.decision,
                        }
                    ).decode(),
                    review.created_at,
                ),
            )
            return review
