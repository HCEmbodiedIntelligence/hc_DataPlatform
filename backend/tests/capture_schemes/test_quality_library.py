import ast
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hc_data_platform.capture_schemes.components import CaptureServices
from hc_data_platform.capture_schemes.quality_library import ThresholdRule
from hc_data_platform.continuous_recordings import capture_quality, processing
from hc_data_platform.continuous_recordings.quality_checks import RECORDING_CHECKS
from hc_data_platform.quality.models import QualityCode
from hc_data_platform.verification.models import VerificationCode

from .test_application_dependencies import Samples, application, identity
from .test_transactions import environment as environment


def test_recording_catalog_covers_emitted_checks():
    codes = set()
    for module in (capture_quality, processing):
        tree = ast.parse(Path(module.__file__).read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"add", "append"}
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value.isupper()
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"findings", "rejects", "warnings"}
            ):
                codes.add(node.args[0].value)
    # Optional-axis warnings are appended directly to the summary returned by aggregate.
    assert codes | {"CAPTURE_OPTIONAL_AXES_UNAVAILABLE"} == set(RECORDING_CHECKS)


def test_library_persists_across_requests_and_is_scoped(environment):
    dsn, scope, _ = environment
    org = scope[0]
    services = CaptureServices(lambda: psycopg.connect(dsn), Samples("qc"), lambda _: True)
    path = f"/api/v1/organizations/{org}/quality-rules"
    with TestClient(application(identity(scope, "robot.manage", "robot.read"), services)) as client:
        catalog = client.get(path).json()
        assert {r["code"] for r in catalog["engine_rules"]} == set(QualityCode)
        assert set(catalog["verification_checks"]) == set(VerificationCode)
        assert catalog["custom_rules"] == []
        for check in catalog["engine_rules"]:
            if check["group"]:
                ThresholdRule(name="defaults", code=check["code"], parameters=check["defaults"])
        params = {"minimum_frequency_ratio_risk": 0.95, "minimum_frequency_ratio_reject": 0.8}
        rule = {"name": "高频采集", "code": "QC_FREQUENCY_LOW", "parameters": params}
        assert client.post(path, json=rule).status_code == 201
        assert client.post(path, json=rule).status_code == 201
        different = dict(rule, parameters=dict(params, minimum_frequency_ratio_risk=0.96))
        assert client.post(path, json=different).status_code == 409
        invalid = dict(rule, parameters=dict(params, minimum_frequency_ratio_risk=0.5))
        assert client.post(path, json=invalid).status_code == 422
    with TestClient(application(identity(scope, "robot.read"), services)) as reader:
        assert reader.get(path).json()["custom_rules"] == [rule]
        assert reader.post(path, json=rule).status_code == 403
        assert reader.get(path.replace(org, org + "-other")).status_code == 403
    other = (org + "-other", *scope[1:])
    with TestClient(application(identity(other, "robot.read"), services)) as reader:
        assert reader.get(path.replace(org, other[0])).json()["custom_rules"] == []


def test_fixed_check_cannot_be_saved_as_custom_threshold():
    with pytest.raises(ValidationError):
        ThresholdRule(name="边界检查", code="QC_TRAILING_IDLE", parameters={})
