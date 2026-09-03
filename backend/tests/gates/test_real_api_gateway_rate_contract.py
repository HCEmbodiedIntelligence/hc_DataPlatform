from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_real_api_rate_policy_is_opt_in_and_returns_a_safe_problem() -> None:
    base = (ROOT / "deploy/compose/gateway.conf").read_text(encoding="utf-8")
    zone = (ROOT / "deploy/compose/gateway.real-api-rate-limit.conf").read_text(encoding="utf-8")
    location = (ROOT / "deploy/compose/gateway.real-api-auth-rate-limit.inc").read_text(
        encoding="utf-8"
    )

    assert "real-api-location-*.inc" in base
    assert "limit_req_zone $binary_remote_addr zone=real_api_auth_sessions" in zone
    assert "location = /api/v1/auth/sessions" in location
    assert "limit_req zone=real_api_auth_sessions burst=8 nodelay" in location
    assert "limit_req_status 429" in location
    assert "application/problem+json" in location
    assert '"code":"RATE_LIMITED"' in location
    assert 'Cache-Control "no-store" always' in location

    assert not (ROOT / "deploy/compose/gateway.real-api-preview-media-rate-limit.inc").exists()
