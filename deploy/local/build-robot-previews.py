#!/usr/bin/env python3
"""Build content-addressed visual LODs offline; never replace source meshes."""
import argparse
import hashlib
import json
import re
from pathlib import Path

import fast_simplification
import numpy as np
import trimesh

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("source", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
report = []
for source in sorted(args.source.rglob("*")):
    if source.suffix.lower() != ".stl":
        continue
    content = source.read_bytes()
    # Preserve small details, tools and grippers at their original precision.
    if len(content) < 512 * 1024 or re.search(r"grip|finger|hand|ban|gongju", source.name, re.I):
        report.append(dict(path=str(source.relative_to(args.source)), source_bytes=len(content), preview_bytes=len(content), preserved=True))
        continue
    source_sha = hashlib.sha256(content).hexdigest()
    folder = args.output / source_sha
    folder.mkdir(parents=True, exist_ok=True)
    mesh = trimesh.load_mesh(source, file_type="stl", process=True)
    # Back off for fine protrusions instead of distorting the source envelope.
    tolerance = max(0.0005, float(np.max(mesh.extents)) * 0.005)
    for reduction in (0.8, 0.6, 0.4):
        vertices, faces = fast_simplification.simplify(mesh.vertices, mesh.faces, target_reduction=reduction)
        preview = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        error = float(np.max(np.abs(preview.bounds - mesh.bounds)))
        if error <= tolerance and np.isfinite(vertices).all() and len(faces):
            break
    else:
        report.append(dict(path=str(source.relative_to(args.source)), source_bytes=len(content), preview_bytes=len(content), preserved=True, reason="envelope"))
        continue
    payload = preview.export(file_type="stl")
    preview_sha = hashlib.sha256(payload).hexdigest()
    (folder / (preview_sha + ".stl")).write_bytes(payload)
    metadata = dict(source_sha256=source_sha, sha256=preview_sha, size_bytes=len(payload),
                    source_size_bytes=len(content), source_faces=len(mesh.faces), faces=len(faces),
                    media_type="model/stl", policy="mesh-qem-v1", bounds_error=error)
    temporary = folder / "manifest.tmp"
    temporary.write_text(json.dumps(metadata, indent=2) + "\n")
    temporary.replace(folder / "manifest.json")
    report.append(dict(path=str(source.relative_to(args.source)), source_bytes=len(content), preview_bytes=len(payload), **metadata))
args.output.mkdir(parents=True, exist_ok=True)
(args.output / "build-report.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(dict(files=len(report), source_bytes=sum(x["source_bytes"] for x in report), preview_bytes=sum(x["preview_bytes"] for x in report), preserved=sum(bool(x.get("preserved")) for x in report))))
