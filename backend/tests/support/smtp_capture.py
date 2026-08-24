"""Loopback-only SMTP capture used by real-browser account-recovery tests."""

from __future__ import annotations

import argparse
import asyncore
import json
import signal
import smtpd
import threading
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class CaptureServer(smtpd.SMTPServer):
    def __init__(self, address: tuple[str, int]) -> None:
        # The stdlib stub excludes None although smtpd uses it to disable relay.
        super().__init__(address, None, decode_data=False)  # type: ignore[arg-type]
        self._lock = threading.Lock()
        self._messages: list[dict[str, Any]] = []

    def process_message(
        self,
        peer: tuple[str, int],
        mailfrom: str,
        rcpttos: list[str],
        data: bytes | str,
        **kwargs: Any,
    ) -> None:
        del peer, kwargs
        encoded = data.encode("utf-8") if isinstance(data, str) else data
        message = BytesParser(policy=policy.default).parsebytes(encoded)
        body = message.get_body(preferencelist=("plain",))
        text = "" if body is None else body.get_content()
        with self._lock:
            self._messages.append(
                {
                    "id": len(self._messages) + 1,
                    "from": mailfrom,
                    "to": rcpttos,
                    "subject": str(message.get("Subject", "")),
                    "text": text,
                }
            )
        return None

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(message) for message in self._messages]

    def clear(self) -> None:
        with self._lock:
            self._messages.clear()


def handler_for(capture: CaptureServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self._json({"status": "ok"})
                return
            if self.path == "/messages":
                self._json({"messages": capture.snapshot()})
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_DELETE(self) -> None:  # noqa: N802
            if self.path != "/messages":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            capture.clear()
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            del format, args

        def _json(self, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smtp-port", type=int, required=True)
    parser.add_argument("--http-port", type=int, required=True)
    args = parser.parse_args()
    capture = CaptureServer(("127.0.0.1", args.smtp_port))
    smtp_thread = threading.Thread(
        target=asyncore.loop,
        kwargs={"timeout": 0.1},
        daemon=True,
    )
    smtp_thread.start()
    httpd = ThreadingHTTPServer(
        ("127.0.0.1", args.http_port),
        handler_for(capture),
    )

    def stop(_signum: int, _frame: object) -> None:
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        httpd.serve_forever()
    finally:
        capture.close()
        asyncore.close_all()
        httpd.server_close()


if __name__ == "__main__":
    main()
