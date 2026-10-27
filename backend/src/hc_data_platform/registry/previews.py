"""Read independently built visual previews, keyed by immutable source content."""
import hashlib
import json
import re
from pathlib import Path


class RobotModelPreviews:
    def __init__(self, root: str | None):
        self.root = Path(root) if root else None

    def describe(self, source_sha256: str, format: str = "stl"):
        if self.root is None or not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
            return None
        try:
            name = "manifest-glb.json" if format == "glb" else "manifest.json"
            value = json.loads((self.root / source_sha256 / name).read_text())
        except FileNotFoundError:
            return None
        if (value.get("source_sha256") != source_sha256
                or value.get("policy") != ("mesh-indexed-glb-v2" if format == "glb" else "mesh-qem-v1")
                or not re.fullmatch(r"[0-9a-f]{64}", value.get("sha256", ""))
                or not 0 < value.get("size_bytes", 0) < value.get("source_size_bytes", 0)
                or not 0 < value.get("faces", 0) <= value.get("source_faces", 0)
                or value.get("media_type") != ("model/gltf-binary" if format == "glb" else "model/stl")):
            raise ValueError("Invalid visual preview metadata")
        return value

    def read(self, source_sha256: str, preview_sha256: str) -> bytes:
        metadata = next((value for format in ("stl", "glb")
                         if (value := self.describe(source_sha256, format))
                         and value["sha256"] == preview_sha256), None)
        if not metadata or metadata["sha256"] != preview_sha256:
            raise KeyError("Preview does not belong to the authorized source asset")
        suffix = ".glb" if metadata["media_type"] == "model/gltf-binary" else ".stl"
        payload = (self.root / source_sha256 / (preview_sha256 + suffix)).read_bytes()
        if len(payload) != metadata["size_bytes"] or hashlib.sha256(payload).hexdigest() != preview_sha256:
            raise ValueError("Visual preview content digest mismatch")
        return payload
