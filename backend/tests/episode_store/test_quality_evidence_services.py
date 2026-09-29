"""Actual isolated HTTP + immutable package receipts, not a human quality decision."""

import json
import shutil
from pathlib import Path

import httpx
import psycopg
import pytest

from hc_data_platform.episode_store.commit import EpisodePackageCommitter
from hc_data_platform.episode_store.models import canonical
from hc_data_platform.episode_store.quality import EpisodeQualityEvidence
from hc_data_platform.episode_store.validation import validate_package
from hc_data_platform.quality import QualityEngine, QualityInputV1, QualityProfileV1
from hc_data_platform.quality.models import ActionObservation
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_http_services import register_and_grant


@pytest.mark.integration
def test_fixed_quality_http_legacy_missing_evidence_and_tenant_fences(services, package, tmp_path):
    repository, storage = services
    root, source = package
    ident = source.identity
    evaluation = QualityEngine().evaluate_with_evidence(
        QualityInputV1(
            rollout_id=ident.rollout_id,
            source_sha256=source.source_sha256,
            start_ns=0,
            end_ns=100_000_000,
            topic_timestamps_ns={"/state": (0, 33_333_333, 66_666_666)},
            actions=(ActionObservation(timestamp_ns=0, values=(0,)),),
        ),
        QualityProfileV1(profile_id="fixture", required_topics={"/state"}),
    )
    (root / "hc/quality.json").write_bytes(canonical(evaluation.report.model_dump(mode="json")))
    legacy = tmp_path / "legacy"
    shutil.copytree(root, legacy)
    (root / "hc/quality_rules.json").write_bytes(
        canonical(evaluation.evidence.model_dump(mode="json"))
    )
    with scoped(source):
        committer = EpisodePackageCommitter(repository, storage)
        data = committer.commit(root, source).data
        legacy_data = committer.commit(
            legacy, source.model_copy(update={"configuration_sha256": "c" * 64})
        ).data
        # Complete validation catches source substitution before any new commit.
        wrong = QualityEngine().evaluate_with_evidence(
            QualityInputV1(
                rollout_id="other",
                source_sha256=source.source_sha256,
                start_ns=0,
                end_ns=1,
                topic_timestamps_ns={},
            ),
            QualityProfileV1(profile_id="other", required_topics=set()),
        )
        original_rules = (root / "hc/quality_rules.json").read_bytes()
        (root / "hc/quality_rules.json").write_bytes(
            canonical(wrong.evidence.model_dump(mode="json"))
        )
        with pytest.raises(ValueError, match="differs"):
            validate_package(root, source)
        (root / "hc/quality_rules.json").write_bytes(original_rules)
        with httpx.Client(base_url="http://gateway:8080", timeout=30, trust_env=False) as client:
            _, headers = register_and_grant(
                client, repository, ident, capabilities=["dataset_version.read"]
            )
            evidence_repository = EpisodeQualityEvidence(repository)
            evidence_repository.put(evaluation)
            evidence_repository.put(evaluation)
            with repository.transaction() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM episode_store.qc_evidence WHERE organization_id=%s",
                    (ident.organization_id,),
                )
                assert cursor.fetchone()[0] == 1
            for sql in (
                "UPDATE episode_store.qc_evidence SET rollout_id='forged' WHERE organization_id=%s",
                "DELETE FROM episode_store.qc_evidence WHERE organization_id=%s",
            ):
                with (
                    pytest.raises(psycopg.errors.CheckViolation),
                    repository.transaction() as cursor,
                ):
                    cursor.execute(sql, (ident.organization_id,))
            base = (
                f"/api/v1/projects/{ident.project_id}/regions/{ident.region_code}"
                f"/datasets/{ident.dataset_id}/episodes/{ident.episode_id}/data-versions/"
            )
            path = base + data.data_version_id + "/quality"
            assert client.get(path).status_code == 401
            response = client.get(path, headers=headers)
            assert response.status_code == 200, response.text
            current = response.json()
            assert current["data"] == data.model_dump(mode="json")
            assert current["status"] == "PASS" and current["evidence_status"] == "AVAILABLE"
            assert current["evidence"]["content_sha256"] == evaluation.evidence.content_sha256
            assert any(r["status"] == "skip" for r in current["rules"])
            response = client.get(base + legacy_data.data_version_id + "/quality", headers=headers)
            assert response.status_code == 200, response.text
            old = response.json()
            assert old["status"] == "PASS" and old["evidence_status"] == "LEGACY_MISSING"
            assert old["evidence"] is None and all(r["status"] == "skip" for r in old["rules"])
            assert (
                client.get(path, headers={**headers, "X-Organization-Id": "other"}).status_code
                == 403
            )
            assert (
                client.get(
                    path.replace("/regions/r/", "/regions/other/"),
                    headers={**headers, "X-Region-Code": "other"},
                ).status_code
                == 404
            )
            evidence = {
                "fixture": "synthetic fixed-version QC through normal gateway session",
                "data": data.model_dump(mode="json"),
                "legacy_data": legacy_data.model_dump(mode="json"),
                "report_sha256": evaluation.report.content_sha256,
                "evidence_sha256": evaluation.evidence.content_sha256,
                "rules": len(current["rules"]),
                "legacy_no_false_pass": True,
                "immutable_database_receipt": True,
                "foreign_scope_denied": True,
                "real_recording": False,
                "human_acceptance": False,
            }
            Path("/app/.g1-artifacts/qc-rules-http.json").write_text(
                json.dumps(evidence, indent=2) + "\n"
            )
            assert client.post("/api/v1/auth/session:logout", headers=headers).status_code == 204
