#!/usr/bin/env python3
"""Convert validated STL display LODs to indexed GLB; preserve original URDF/assets."""
import argparse, gzip, hashlib, json
from pathlib import Path
import trimesh

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('source',type=Path)
parser.add_argument('output',type=Path)
args=parser.parse_args()
report=[]
for source in sorted(args.source.rglob('*')):
 if source.suffix.lower()!='.stl': continue
 original=source.read_bytes();source_sha=hashlib.sha256(original).hexdigest()
 folder=args.output/source_sha;folder.mkdir(parents=True,exist_ok=True)
 old=json.loads((folder/'manifest.json').read_text()) if (folder/'manifest.json').exists() else None
 display=folder/(old['sha256']+'.stl') if old else source
 mesh=trimesh.load_mesh(display,process=True)
 payload=mesh.export(file_type='glb',include_normals=False)
 if len(payload)>=len(original): continue
 sha=hashlib.sha256(payload).hexdigest();(folder/(sha+'.glb')).write_bytes(payload)
 metadata=dict(source_sha256=source_sha,sha256=sha,size_bytes=len(payload),source_size_bytes=len(original),
   source_faces=old['source_faces'] if old else len(mesh.faces),faces=len(mesh.faces),
   media_type='model/gltf-binary',policy='mesh-indexed-glb-v2',bounds_error=old.get('bounds_error',0) if old else 0)
 temp=folder/'manifest-glb.tmp';temp.write_text(json.dumps(metadata,indent=2)+'\n');temp.replace(folder/'manifest-glb.json')
 report.append(dict(path=str(source.relative_to(args.source)),gzip_bytes=len(gzip.compress(payload,compresslevel=5)),**metadata))
(args.output/'glb-build-report.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(dict(files=len(report),source_bytes=sum(x['source_size_bytes'] for x in report),glb_bytes=sum(x['size_bytes'] for x in report),gzip_bytes=sum(x['gzip_bytes'] for x in report))))
