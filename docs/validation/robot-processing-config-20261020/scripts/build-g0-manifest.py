import json
from uuid import uuid4
from pathlib import Path
from humanoid_upload.bundle import manifest_for
from humanoid_upload.common import DEFAULTS
cfg={**DEFAULTS,'robot_id':'synthetic-platform-robot-01','local_robot_id':'synthetic-openarmx-01','task_id':'synthetic-task-001','data_directory':'/fixtures','read_bytes_per_second':1000000000}
root,marker,manifest=manifest_for('/fixtures/valid-openarm',cfg,str(uuid4()))
Path('/g0/fixtures').mkdir(parents=True,exist_ok=True)
Path('/g0/generated-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print({'assets':len(manifest['assets']),'synthetic':True})
