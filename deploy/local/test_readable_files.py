"""Contract checks for file-manager reads, seeking, and copying failures."""

import hashlib
import http.client
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import quote

from readable_files import FileTree, ThreadingHTTPServer, View, byte_range, handler


class ReadableFileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.content = b"recording bytes\x00\xff" * 100
        (self.root / "raw").mkdir()
        (self.root / "raw" / "a.mcap").write_bytes(self.content)
        self.path = "青山项目/拿取任务/原始文件/a.mcap"
        self.catalog = {
            "schema_version": 1,
            "files": [
                {
                    "path": self.path,
                    "key": "raw/a.mcap",
                    "size": len(self.content),
                    "sha256": hashlib.sha256(self.content).hexdigest(),
                    "modified": "2026-10-21T06:00:00+00:00",
                }
            ],
        }
        self.view = View(self.catalog, self.root, "test-token")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler(self.view))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temporary.cleanup()

    def request(self, path=None, *, method="GET", headers=None, auth=True):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=5
        )
        request_headers = {"Authorization": "Bearer test-token"} if auth else {}
        request_headers.update(headers or {})
        connection.request(
            method,
            quote("/" + (path if path is not None else self.path)),
            headers=request_headers,
        )
        return connection, connection.getresponse()

    def test_directory_and_file_copy_return_real_bytes(self):
        connection, response = self.request("")
        self.assertEqual(response.status, 200)
        self.assertIn("青山项目", response.read().decode())
        connection.close()
        connection, response = self.request()
        self.assertEqual(response.read(), self.content)
        connection.close()

    def test_head_and_video_seek(self):
        connection, response = self.request(method="HEAD")
        self.assertEqual(int(response.getheader("Content-Length")), len(self.content))
        self.assertEqual(response.read(), b"")
        connection.close()
        connection, response = self.request(headers={"Range": "bytes=5-14"})
        self.assertEqual(response.status, 206)
        self.assertEqual(response.read(), self.content[5:15])
        connection.close()
        self.assertEqual(
            byte_range("bytes=-7", len(self.content))[:2],
            (len(self.content) - 7, len(self.content)),
        )

    def test_read_only_auth_and_path_boundary(self):
        for method, path, auth, expected in [
            ("GET", self.path, False, 401),
            ("PUT", self.path, True, 501),
            ("GET", "../raw/a.mcap", True, 400),
            ("GET", "raw/a.mcap", True, 404),
        ]:
            connection, response = self.request(path, method=method, auth=auth)
            self.assertEqual(response.status, expected)
            response.read()
            connection.close()
        self.assertEqual((self.root / "raw/a.mcap").read_bytes(), self.content)

    def test_invalid_range_and_missing_bytes_are_errors(self):
        connection, response = self.request(headers={"Range": "bytes=999999-"})
        self.assertEqual(response.status, 416)
        response.read()
        connection.close()
        (self.root / "raw/a.mcap").unlink()
        connection, response = self.request()
        self.assertEqual(response.status, 502)
        response.read()
        connection.close()

    def test_checksum_failure_is_not_a_successful_copy(self):
        (self.root / "raw/a.mcap").write_bytes(b"x" * len(self.content))
        connection, response = self.request()
        with self.assertRaises(http.client.IncompleteRead):
            response.read()
        connection.close()

    def test_atomic_refresh_and_invalid_catalog_preserve_old_view(self):
        bad = {
            "schema_version": 1,
            "files": [dict(self.catalog["files"][0], path="../bad")],
        }
        with self.assertRaises(ValueError):
            self.view.update(bad)
        self.assertIn(self.path, self.view.tree.files)
        self.view.update({"schema_version": 1, "files": []})
        self.assertEqual(self.view.tree.files, {})
        with self.assertRaises(ValueError):
            FileTree({"schema_version": 1, "files": self.catalog["files"] * 2})


if __name__ == "__main__":
    unittest.main()
