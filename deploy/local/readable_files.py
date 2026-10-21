"""A private read-only file view, mounted by rclone as ordinary local files.

The object mount is private to the OS user. Only indexed committed files are
exposed through the authenticated loopback HTTP listener. No symlinks, full
mirror, write-through, or new Python dependencies are used.
"""

from __future__ import annotations

import configparser
import hashlib
import hmac
import html
import json
import mimetypes
import os
import re
import secrets
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit


def valid_path(path: str) -> str:
    if (
        not path
        or "\\" in path
        or "\x00" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise ValueError("invalid file path")
    return path


def byte_range(header: str | None, size: int) -> tuple[int, int, int]:
    if not header:
        return 0, size, 200
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header)
    if not match or not any(match.groups()) or size == 0:
        raise ValueError("invalid range")
    first, last = match.groups()
    if first:
        start, end = int(first), min(int(last) + 1 if last else size, size)
    else:
        count = int(last)
        if not count:
            raise ValueError("empty suffix")
        start, end = max(0, size - count), size
    if start >= size or end <= start:
        raise ValueError("unsatisfiable range")
    return start, end, 206


def modified_date(value) -> str:
    try:
        date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise ValueError("missing timezone")
    except (ValueError, TypeError):
        date = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return format_datetime(date.astimezone(timezone.utc), usegmt=True)


class FileTree:
    def __init__(self, catalog: dict):
        if catalog.get("schema_version") != 1:
            raise ValueError("unsupported catalog")
        self.files = {}
        self.directories: dict[str, set[str]] = {"": set()}
        for file in catalog["files"]:
            path = valid_path(file["path"])
            if "key" in file:
                valid_path(file["key"])
            if int(file["size"]) < 0 or not re.fullmatch(
                r"[0-9a-f]{64}", file["sha256"]
            ):
                raise ValueError("invalid file metadata")
            if path in self.files:
                raise ValueError("duplicate view path")
            self.files[path] = file
            parts = path.split("/")
            for index, part in enumerate(parts):
                parent = "/".join(parts[:index])
                self.directories.setdefault(parent, set()).add(part)
        if self.files.keys() & self.directories.keys():
            raise ValueError("file/directory conflict")


class View:
    def __init__(self, catalog: dict, objects: Path, token: str):
        self.tree = FileTree(catalog)
        self.objects, self.token = objects, token
        self.slots = threading.BoundedSemaphore(8)
        self.error: str | None = None

    def update(self, catalog: dict):
        self.tree = FileTree(catalog)
        self.error = None

    def chunks(self, file: dict, start: int, end: int):
        if "text" in file:
            yield file["text"].encode("utf-8")[start:end]
            return
        path = self.objects / valid_path(file["key"])
        # The root is our read-only S3 mount, never the MinIO on-disk directory.
        with path.open("rb") as stream:
            if os.fstat(stream.fileno()).st_size != file["size"]:
                raise OSError("object size differs from committed metadata")
            stream.seek(start)
            remaining = end - start
            digest = hashlib.sha256()
            while remaining:
                chunk = stream.read(min(1024**2, remaining))
                if not chunk:
                    raise OSError("incomplete object")
                remaining -= len(chunk)
                digest.update(chunk)
                yield chunk
            if (
                start == 0
                and end == file["size"]
                and digest.hexdigest() != file["sha256"]
            ):
                raise OSError("object checksum differs from committed metadata")


def handler(view: View):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            # Do not journal file names or authorization headers on every read.
            pass

        def do_HEAD(self):
            self.read(head=True)

        def do_GET(self):
            self.read(head=False)

        def read(self, *, head: bool):
            supplied = self.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied, "Bearer " + view.token):
                self.send_error(401)
                return
            try:
                path = unquote(urlsplit(self.path).path, errors="strict").removeprefix(
                    "/"
                )
                path = path.removesuffix("/")
                if path:
                    valid_path(path)
            except (ValueError, UnicodeError):
                self.send_error(400)
                return
            tree = view.tree
            if path in tree.directories:
                if not urlsplit(self.path).path.endswith("/"):
                    self.send_response(301)
                    self.send_header("Location", quote("/" + path + "/", safe="/"))
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                entries = []
                for name in sorted(tree.directories[path]):
                    target = path + "/" + name if path else name
                    suffix = "/" if target in tree.directories else ""
                    entries.append(
                        '<a href="'
                        + quote(name + suffix, safe="/")
                        + '">'
                        + html.escape(name + suffix)
                        + "</a><br>"
                    )
                body = (
                    '<!doctype html><meta charset="utf-8">' + "\n".join(entries)
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if not head:
                    self.wfile.write(body)
                return
            file = tree.files.get(path)
            if file is None:
                self.send_error(404)
                return
            try:
                start, end, status = byte_range(self.headers.get("Range"), file["size"])
            except ValueError:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{file['size']}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if not view.slots.acquire(timeout=1):
                self.send_error(503)
                return
            headers_sent = False
            chunks = None
            try:
                if not head:
                    chunks = view.chunks(file, start, end)
                    pending = next(chunks, b"")
                self.send_response(status)
                self.send_header("Content-Length", str(end - start))
                self.send_header(
                    "Content-Type",
                    mimetypes.guess_type(path)[0] or "application/octet-stream",
                )
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Last-Modified", modified_date(file.get("modified")))
                self.send_header("ETag", '"' + file["sha256"] + '"')
                if status == 206:
                    self.send_header(
                        "Content-Range", f"bytes {start}-{end - 1}/{file['size']}"
                    )
                self.end_headers()
                headers_sent = True
                if not head:
                    # Retain the last block until the generator verifies length/hash.
                    for chunk in chunks:
                        self.wfile.write(pending)
                        pending = chunk
                    self.wfile.write(pending)
            except (OSError, ValueError):
                if not headers_sent:
                    self.send_error(502, "File temporarily unavailable")
                self.close_connection = True
            finally:
                if chunks is not None:
                    chunks.close()
                view.slots.release()

    return Handler


def atomic_json(path: Path, value: dict):
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, ensure_ascii=False)
    os.replace(temporary, path)


def load_catalog(api_container: str) -> dict:
    result = subprocess.run(
        [
            "docker",
            "exec",
            api_container,
            "python",
            "-m",
            "hc_data_platform.tools.local_file_catalog",
        ],
        capture_output=True,
        text=True,
        timeout=90,
        check=True,
    )
    return json.loads(result.stdout)


def mount(options):
    target, objects = options.directory(), options.STATE / "objects"
    objects.mkdir(exist_ok=True)
    for path in (target, objects):
        if os.path.ismount(path) or any(path.iterdir()):
            raise RuntimeError(f"Mount directory must be empty and unmounted: {path}")
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    children = []
    server = None
    flags = [
        "--read-only",
        "--vfs-cache-mode",
        "off",
        "--buffer-size",
        "8M",
        "--dir-cache-time",
        "5s",
        "--attr-timeout",
        "1s",
        "--file-perms",
        "0444",
        "--dir-perms",
        "0555",
        "--umask",
        "022",
        "--log-level",
        "NOTICE",
    ]
    try:
        raw = subprocess.Popen(
            options.rclone("mount", options.REMOTE, str(objects), *flags)
        )
        children.append(raw)
        deadline = time.monotonic() + 30
        while not os.path.ismount(objects):
            if raw.poll() is not None or stop.is_set() or time.monotonic() > deadline:
                raise RuntimeError("Private object mount failed to start")
            stop.wait(0.1)
        container = options.settings().get(
            "HC_LOCAL_FILES_API_CONTAINER", "hc-data-platform-dev-api-1"
        )
        catalog = load_catalog(container)
        token = secrets.token_hex(32)
        view = View(catalog, objects, token)
        atomic_json(options.STATE / "file-catalog.json", catalog)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler(view))
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        config = configparser.ConfigParser(interpolation=None)
        config["view"] = {
            "type": "http",
            "url": f"http://127.0.0.1:{server.server_port}/",
            "headers": "Authorization,Bearer " + token,
        }
        config_path = options.STATE / "readable-rclone.conf"
        descriptor = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            config.write(stream)

        def refresh():
            while not stop.wait(10):
                try:
                    updated = load_catalog(container)
                    view.update(updated)
                    atomic_json(options.STATE / "file-catalog.json", updated)
                    atomic_json(
                        options.STATE / "file-view-status.json",
                        {
                            "available": True,
                            "refreshed_at": updated["generated_at"],
                            "warnings": updated["warnings"],
                        },
                    )
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    view.error = type(exc).__name__
                    notice = (
                        "目录暂时无法刷新，正在显示最近一次成功读取的内容。\n"
                        "平台恢复后会自动刷新；读取文件需要本机存储服务运行。\n"
                    )
                    previous = {
                        "schema_version": 1,
                        "files": [
                            f
                            for f in view.tree.files.values()
                            if f["path"] != "目录刷新状态.txt"
                        ],
                    }
                    previous["files"].append(
                        {
                            "path": "目录刷新状态.txt",
                            "text": notice,
                            "size": len(notice.encode()),
                            "sha256": hashlib.sha256(notice.encode()).hexdigest(),
                        }
                    )
                    view.tree = FileTree(previous)
                    atomic_json(
                        options.STATE / "file-view-status.json",
                        {
                            "available": False,
                            "error": type(exc).__name__,
                        },
                    )
                    print(
                        "File index refresh failed; retaining last complete index: "
                        + type(exc).__name__,
                        flush=True,
                    )

        threading.Thread(target=refresh, daemon=True).start()
        # The mount enforces read-only access; copied-out files keep normal,
        # writable mode bits in the user's destination.
        friendly_flags = list(flags)
        friendly_flags[friendly_flags.index("--file-perms") + 1] = "0644"
        friendly_flags[friendly_flags.index("--dir-perms") + 1] = "0755"
        friendly = subprocess.Popen(
            [
                str(options.BINARY),
                "--config",
                str(config_path),
                "mount",
                "view:",
                str(target),
                *friendly_flags,
                "--volname",
                "平台数据",
                "--vfs-read-chunk-size",
                "32M",
            ]
        )
        children.append(friendly)
        while not stop.wait(0.25):
            if any(child.poll() is not None for child in children):
                raise RuntimeError("File mount process stopped")
    finally:
        stop.set()
        # Stop the readable mount before its underlying object reader.
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        if server is not None:
            server.shutdown()
            server.server_close()
