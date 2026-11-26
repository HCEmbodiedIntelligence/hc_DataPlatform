import json
import os
import pwd
import sys
from pathlib import Path
base=Path("/mnt/workspace/hc-platform")
runtime=base/"runtime"
run=base/"service"
service_name=sys.argv[1]
replica = service_name in {f"worker-{index}" for index in range(2, 13)}
role = "worker" if replica else service_name
python=str(runtime/"venv/bin/python")
commands={"api":[python,"-m","uvicorn","hc_data_platform.core.app:create_app","--factory","--host","127.0.0.1","--port","18001"],
          "worker":[python,"-m","hc_data_platform.workflow.worker"],
          "temporal":[str(runtime/"bin/temporal-server"),"--root",str(run),"--config",".","--env","dsw","start"],
          "web":["/usr/sbin/nginx","-c",str(run/"nginx.conf"),"-p",str(run/"nginx"),"-g","daemon off;"],
          "verify":[python,str(runtime/"ctl/verify-oss.py")]}
env={"PATH":str(runtime/"venv/bin")+":/usr/local/bin:/usr/bin:/bin","HOME":str(base/"cache/service-home"),"LANG":"C.UTF-8","PYTHONUNBUFFERED":"1","PYTHONDONTWRITEBYTECODE":"1",
     "PYTHONPATH":str(base/"app/backend/src")+":"+str(runtime/"python")}
if role == "script":
    if len(sys.argv) < 3 or not Path(sys.argv[2]).is_absolute():
        raise SystemExit("Usage: service.py script /absolute/path/to/check.py [args...]")
    commands[role] = [python, *sys.argv[2:]]
if role in ("api","worker","verify","script"):
    env.update(json.loads((run/("worker.json" if role=="worker" else "api.json")).read_text()))
if role == "worker":
    # DSW mounts /tmp on the persistent PostgreSQL volume. Keep disposable MCAP
    # files and SQLite sorting on the separate container disk.
    env.setdefault("TMPDIR", "/var/tmp")
    env["HC_WORKER_READINESS_FILE"] = str(base/"cache/service-home"/f"{service_name}-ready")
    if service_name == "worker":
        env["HC_DIRECTORY_CLEANUP_WORKER"] = "primary"
    if replica:
        env.update(HC_WORKER_ROLE="main", HC_WORKER_BACKGROUND_SERVICES="false",
                   HC_WORKFLOW_ACTIVITY_FACTORY="hc_data_platform.runtime:activity_dependencies")
        env.pop("HC_INSTANCE_ID", None)
account=pwd.getpwnam("hcplatform")
os.setgroups([])
os.setgid(account.pw_gid)
os.setuid(account.pw_uid)
os.chdir(base/"app/backend")
os.execve(commands[role][0],commands[role],env)
