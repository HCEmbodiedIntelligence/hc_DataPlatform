"""Minimal in-cluster metrics target and Alertmanager webhook evidence receiver."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

_LOCK = threading.Lock()
_CANARY = 1
_DELIVERIES: list[dict[str, Any]] = []
_ALLOWED_LABELS = frozenset({"alertname", "component", "severity", "synthetic"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class DrillHandler(BaseHTTPRequestHandler):
    server_version = "hc-observability-drill/1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _json(self, status: HTTPStatus, payload: object) -> None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 1 or length > 1_048_576:
            raise ValueError("invalid body length")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise TypeError("body must be an object")
        return value

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._json(HTTPStatus.OK, {"status": "ready"})
            return
        if self.path == "/metrics":
            with _LOCK:
                value = _CANARY
            body = (
                "# HELP hc_observability_alert_canary Approved delivery drill switch.\n"
                "# TYPE hc_observability_alert_canary gauge\n"
                f"hc_observability_alert_canary {value}\n"
            ).encode()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/evidence":
            with _LOCK:
                payload = {"canary": _CANARY, "deliveries": list(_DELIVERIES)}
            self._json(HTTPStatus.OK, payload)
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:
        global _CANARY
        try:
            payload = self._body()
            if self.path == "/canary":
                value = payload.get("value")
                if value not in {0, 1}:
                    raise ValueError("value must be zero or one")
                with _LOCK:
                    _CANARY = int(value)
                self._json(HTTPStatus.OK, {"canary": value})
                return
            if self.path == "/alerts":
                alerts = payload.get("alerts")
                if not isinstance(alerts, list) or not alerts:
                    raise ValueError("alerts must be a non-empty list")
                retained = []
                for item in alerts:
                    if not isinstance(item, dict):
                        raise TypeError("alert must be an object")
                    labels = item.get("labels")
                    if not isinstance(labels, dict):
                        raise TypeError("alert labels must be an object")
                    retained.append(
                        {
                            "status": item.get("status"),
                            "fingerprint": item.get("fingerprint"),
                            "starts_at": item.get("startsAt"),
                            "ends_at": item.get("endsAt"),
                            "labels": {
                                key: labels[key]
                                for key in sorted(_ALLOWED_LABELS)
                                if isinstance(labels.get(key), str)
                            },
                        }
                    )
                delivery = {
                    "received_at": _utc_now(),
                    "status": payload.get("status"),
                    "alerts": retained,
                }
                with _LOCK:
                    _DELIVERIES.append(delivery)
                self._json(HTTPStatus.OK, {"accepted": len(retained)})
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": type(exc).__name__})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8081), DrillHandler).serve_forever()
