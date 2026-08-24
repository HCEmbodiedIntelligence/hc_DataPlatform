"""Deterministic HTTP provider gateway for the opt-in real-browser acceptance stack.

This process is reachable only on the Compose network.  It exercises the same
HTTP connector, authorization header, idempotency key, Worker and durable job
state used by a deployed provider without making a billable external request.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

_MAX_BODY_BYTES = 1_048_576
_MODELS = frozenset({"deterministic-v1", "fail-once-v1", "slow-v1"})


class ProviderHandler(BaseHTTPRequestHandler):
    server_version = "hc-auto-annotation-acceptance/1"
    protocol_version = "HTTP/1.1"
    attempts: ClassVar[dict[str, int]] = {}
    attempts_lock: ClassVar[threading.Lock] = threading.Lock()

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        if self.path != "/healthz":
            self._json(HTTPStatus.NOT_FOUND, {"code": "NOT_FOUND"})
            return
        self._json(HTTPStatus.OK, {"status": "ok"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        if self.path != "/v1/annotate":
            self._json(HTTPStatus.NOT_FOUND, {"code": "NOT_FOUND"})
            return
        expected_key = os.environ.get("HC_AUTO_ANNOTATION_FIXTURE_API_KEY", "")
        if not expected_key or self.headers.get("Authorization") != f"Bearer {expected_key}":
            self._json(HTTPStatus.UNAUTHORIZED, {"code": "PROVIDER_AUTH_REQUIRED"})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if length < 0 or length > _MAX_BODY_BYTES:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"code": "INVALID_BODY_SIZE"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"code": "INVALID_JSON"})
            return
        if not isinstance(payload, dict):
            self._json(HTTPStatus.BAD_REQUEST, {"code": "INVALID_REQUEST"})
            return
        job_id = payload.get("job_id")
        model = payload.get("model")
        selection = payload.get("input_selection")
        if (
            not isinstance(job_id, str)
            or not job_id
            or self.headers.get("Idempotency-Key") != job_id
            or not isinstance(model, str)
            or model not in _MODELS
            or not isinstance(selection, dict)
        ):
            self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"code": "INVALID_REQUEST"})
            return
        start_step = selection.get("start_step")
        end_step = selection.get("end_step")
        if (
            not isinstance(start_step, int)
            or isinstance(start_step, bool)
            or not isinstance(end_step, int)
            or isinstance(end_step, bool)
            or start_step < 0
            or end_step <= start_step
        ):
            self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"code": "INVALID_RANGE"})
            return

        with self.attempts_lock:
            attempt = self.attempts.get(job_id, 0) + 1
            self.attempts[job_id] = attempt
        if model == "fail-once-v1" and attempt == 1:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"code": "PROVIDER_RETRY"})
            return
        if model == "slow-v1":
            time.sleep(3)

        selected_steps = end_step - start_step
        self._json(
            HTTPStatus.OK,
            {
                "tags": [],
                "operations": [],
                "usage": {
                    "input_units": selected_steps,
                    "output_units": 0,
                    "cost_micros": max(1, (selected_steps + 999) // 1000),
                },
            },
        )

    def log_message(self, format: str, *args: Any) -> None:
        del format, args

    def _json(self, status: HTTPStatus, body: dict[str, object]) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 8090), ProviderHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
