"""Read an existing verified conversion's preview without migrating historical data.

Only an exact frozen source revision row qualifies. No latest pointer, filename
guess, new encoder or migration command is used by a media authorization.
"""

from hc_data_platform.core.errors import problem

from .migration_conversion import MigrationEpisodePlan
from .models import CommitReceipt


class LegacyConversionPreviews:
    def __init__(self, repository, files, previews, revision_proof):
        self.repository, self.files, self.previews = repository, files, previews
        self.revision_proof = revision_proof

    def authorize(self, revision, stream, *, actor_id):
        try:
            return self._authorize(revision, stream, actor_id=actor_id)
        except ValueError as exc:
            raise problem(
                status=409,
                code="LEGACY_DEPTH_PREVIEW_INTEGRITY_FAILED",
                title="Historical depth preview differs",
                detail=str(exc),
            ) from exc

    def _authorize(self, revision, stream, *, actor_id):
        binding = stream.aligned_media_binding
        scope = (
            revision.scope.organization_id,
            revision.scope.project_id,
            revision.scope.region_code,
            revision.dataset_id,
        )
        with self.repository.transaction() as cursor:
            proof = self.revision_proof(cursor, revision)
            cursor.execute(
                """SELECT member FROM episode_store.migration_batches b,
                LATERAL jsonb_array_elements(b.plan->'members') member
                WHERE b.organization_id=%s AND b.project_id=%s AND b.region_code=%s
                  AND b.dataset_id=%s AND b.status IN ('PUBLISHED','ROLLED_BACK')
                  AND member->'plan'->'revision_row_sha256' ? %s
                  AND member->'plan'->'legacy'->'identity'->>'episode_id'=%s
                LIMIT 3""",
                (*scope, proof, revision.episode_id),
            )
            members = [row[0] for row in cursor.fetchall()]
        if not members:
            raise problem(
                status=425,
                code="LEGACY_DEPTH_PREVIEW_NOT_READY",
                title="Historical depth preview unavailable",
                detail="This fixed source has no verified published colorizer preview.",
            )
        if len(members) != 1:
            raise ValueError("historical depth preview mapping is ambiguous")
        plan = MigrationEpisodePlan.model_validate(members[0]["plan"])
        receipt = CommitReceipt.model_validate(members[0]["receipt"])
        ident = plan.legacy.identity
        if (
            (*ident.scope, ident.dataset_id) != scope
            or ident.episode_id != revision.episode_id
            or ident.rollout_id != binding.rollout_id
            or receipt.data.identity != ident
            or receipt.data.data_version_id != plan.source.data_version_id
            or not 0 <= binding.start_step < binding.end_step <= plan.legacy.frame_count
        ):
            raise ValueError("historical preview mapping differs from fixed source")
        package = self.files.open(ident, receipt.data.data_version_id)
        if package.receipt != receipt or package.manifest.input != plan.source:
            raise ValueError("historical preview package differs from conversion receipt")
        report = package.document("hc/migration.json")
        if (
            report["plan_sha256"] != plan.sha256
            or report["legacy"] != plan.legacy.model_dump(mode="json")
            or report["legacy_logical_sha256"] != plan.legacy.content_hash
            or report["converted_logical_sha256"] != plan.legacy.content_hash
        ):
            raise ValueError("historical preview lacks exact source equivalence")
        info = package.document("meta/info.json")
        feature = info["hc.feature_mapping"].get(stream.channel_path)
        if feature not in plan.required_depth_features:
            raise ValueError("historical stream is not declared depth in the verified conversion")
        grant = self.previews.authorize(
            ident, receipt.data.data_version_id, feature, actor_id=actor_id
        )
        if grant.kind != "depth-preview" or grant.colorizer is None:
            raise ValueError("historical depth source cannot be played as an original video")
        return grant
