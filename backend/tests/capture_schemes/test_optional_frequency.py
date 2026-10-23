"""Sample rates describe recordings and never become scheme requirements."""

# ruff: noqa: F811 -- pytest injects the imported environment fixture by name.

import copy

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hc_data_platform.capture_schemes import schemes
from hc_data_platform.capture_schemes.common import digest
from hc_data_platform.capture_schemes.components import CaptureServices
from hc_data_platform.capture_schemes.models import CaptureDefinition, SchemeDraft
from hc_data_platform.core.errors import ProblemException

from .processing_fixture import make_source
from .test_application_dependencies import application, identity
from .test_contracts import draft
from .test_mcap_processing import config, contract, joint_source
from .test_sample_validation import lerobot_document, stored
from .test_transactions import environment as environment


@pytest.mark.parametrize("omit", [False, True])
def test_draft_and_published_contract_accept_empty_rate(omit):
    document = draft().model_dump(mode="json", exclude={"expected_revision"})
    if omit:
        document["output"].pop("frequency_hz")
    else:
        document["output"]["frequency_hz"] = None
    assert (
        SchemeDraft.model_validate({**document, "expected_revision": 0}).output.frequency_hz is None
    )
    assert CaptureDefinition.model_validate(document).output.frequency_hz is None


@pytest.mark.parametrize("frequency", [0, -1, 1001, float("nan"), float("inf")])
def test_optional_does_not_accept_invalid_explicit_rates(frequency):
    with pytest.raises(ValidationError):
        draft(fps=frequency)


def test_http_empty_rate_save_reload_validate_publish_preserves_old_version(environment, tmp_path):
    dsn, scope, _ = environment
    root = tmp_path / "source"
    info = make_source(root, 0, count=3)
    validator, sample, _ = stored(root, "LEROBOT_V3", organization_id=scope[0])
    services = CaptureServices(lambda: psycopg.connect(dsn), validator, lambda org: org == scope[0])
    auth = identity(scope, "robot.read", "robot.manage", "upload.read")
    path = f"/api/v1/organizations/{scope[0]}/capture-schemes/optional-fps"
    document = {"name": "Sample frequency", **lerobot_document(info), "expected_revision": 0}
    with TestClient(application(auth, services)) as client:
        assert client.put(path, json=document).status_code == 200
        validation = {"sample": sample.model_dump(mode="json"), "expected_revision": 1}
        assert client.post(path + ":validate", json=validation).status_code == 200
        first = client.post(path + ":publish", json={"expected_revision": 1}).json()
        assert first["version"] == 1
        document["expected_revision"] = 1
        document["output"].pop("frequency_hz")
        saved = client.put(path, json=document)
        assert saved.status_code == 200, saved.text
        assert saved.json()["draft"]["output"]["frequency_hz"] is None
        assert client.get(path).json()["draft"]["output"]["frequency_hz"] is None
        premature = client.post(path + ":publish", json={"expected_revision": 2})
        assert premature.json()["code"] == "CAPTURE_SAMPLE_VALIDATION_REQUIRED"
        validation["expected_revision"] = 2
        checked = client.post(path + ":validate", json=validation)
        assert checked.status_code == 200, checked.text
        result = checked.json()
        assert result["revision"] == 2 and result["draft"]["output"]["frequency_hz"] is None
        evidence = result["validations"][0]
        assert evidence["status"] == "PASSED" and evidence["frame_count"] == 3
        assert evidence["revision"] == 2 and "inferred_from_revision" not in evidence
        assert evidence["draft_sha256"] == digest(result["draft"])
        published = client.post(path + ":publish", json={"expected_revision": 2})
        assert published.status_code == 200, published.text
        assert published.json()["output"]["frequency_hz"] is None
        assert published.json()["content_sha256"] == digest(result["draft"])
        assert client.get(path).json()["versions"][1] == first


def test_failed_sample_does_not_fill_rate_or_advance_revision(environment, tmp_path):
    dsn, scope, _ = environment
    root = tmp_path / "source"
    info = make_source(root, 0, count=3)
    validator, sample, storage = stored(root, "LEROBOT_V3", organization_id=scope[0])
    document = {"name": "Failed sample", **lerobot_document(info), "expected_revision": 0}
    document["output"]["frequency_hz"] = None
    storage.objects["source/meta/info.json"] += b"corrupted"
    with psycopg.connect(dsn) as db:
        saved = schemes.save(db, scope[0], "bad-sample", SchemeDraft(**document), "tester")
        evidence = validator.validate(scope[0], saved["draft"], sample)
        assert evidence["status"] == "FAILED"
        result = schemes.record_validation(db, scope[0], "bad-sample", 1, evidence, "tester")
        assert result["revision"] == 1 and result["draft"]["output"]["frequency_hz"] is None


def test_mcap_observed_rate_stays_in_evidence_without_changing_scheme(environment, tmp_path):
    dsn, scope, _ = environment
    joint_source(tmp_path / "source.mcap")
    validator, sample, _ = stored(tmp_path, "MCAP", organization_id=scope[0])
    document = {
        "name": "MCAP automatic frequency",
        "output": {**contract().model_dump(mode="json"), "frequency_hz": None},
        "adapters": [{"source_format": "MCAP", "configuration": {**config(), "fps": None}}],
        "expected_revision": 0,
    }
    with psycopg.connect(dsn) as db:
        saved = schemes.save(db, scope[0], "mcap-auto", SchemeDraft(**document), "tester")
        evidence = validator.validate(scope[0], saved["draft"], sample)
        assert evidence["status"] == "PASSED", evidence
        assert evidence["frequency_hz"] == 15 and evidence["frame_count"] == 167
        result = schemes.record_validation(db, scope[0], "mcap-auto", 1, evidence, "tester")
        assert result["draft"]["output"]["frequency_hz"] is None
        assert result["draft"]["adapters"][0]["configuration"]["fps"] is None
        published = schemes.publish(db, scope[0], "mcap-auto", 1, "tester")
        CaptureDefinition.model_validate({k: published[k] for k in result["draft"]})


def test_concurrent_edit_cannot_receive_an_old_samples_inferred_rate(environment, tmp_path):
    dsn, scope, _ = environment
    root = tmp_path / "source"
    info = make_source(root, 0, count=3)
    validator, sample, _ = stored(root, "LEROBOT_V3", organization_id=scope[0])
    document = {"name": "Concurrent edit", **lerobot_document(info), "expected_revision": 0}
    document["output"]["frequency_hz"] = None
    with psycopg.connect(dsn) as db:
        saved = schemes.save(db, scope[0], "concurrent", SchemeDraft(**document), "tester")
        evidence = validator.validate(scope[0], saved["draft"], sample)
        changed = copy.deepcopy(document)
        changed.update(name="Updated scheme", expected_revision=1)
        schemes.save(db, scope[0], "concurrent", SchemeDraft(**changed), "tester")
        with pytest.raises(ProblemException, match="CAPTURE_REVISION_CONFLICT"):
            schemes.record_validation(db, scope[0], "concurrent", 1, evidence, "tester")
        current = schemes.detail(db, scope[0], "concurrent")
        assert current["revision"] == 2 and current["draft"]["output"]["frequency_hz"] is None
