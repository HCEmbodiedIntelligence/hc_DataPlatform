import json
import os
import pwd
import sys
from pathlib import Path
base=Path("/mnt/workspace/hc-platform")
runtime=base/"runtime"
run=base/"service"
role=sys.argv[1]
python=str(runtime/"venv/bin/python")
commands={"api":[python,"-m","uvicorn","hc_data_platform.core.app:create_app","--factory","--host","127.0.0.1","--port","18001"],
          "worker":[python,"-m","hc_data_platform.workflow.worker"],
          "temporal":[str(runtime/"bin/temporal-server"),"--root",str(run),"--config",".","--env","dsw","start"],
          "web":["/usr/sbin/nginx","-c",str(run/"nginx.conf"),"-p",str(run/"nginx"),"-g","daemon off;"],
          "verify":[python,str(runtime/"ctl/verify-oss.py")]}
env={"PATH":str(runtime/"venv/bin")+":/usr/local/bin:/usr/bin:/bin","HOME":str(base/"cache/service-home"),"LANG":"C.UTF-8","PYTHONUNBUFFERED":"1","PYTHONDONTWRITEBYTECODE":"1",
     "PYTHONPATH":str(base/"app/backend/src")+":"+str(runtime/"python")}
if role in ("api","worker","verify"):
    env.update(json.loads((run/("worker.json" if role=="worker" else "api.json")).read_text()))
if role == "worker":
    env["HC_WORKER_READINESS_FILE"] = str(base/"cache/service-home/worker-ready")
account=pwd.getpwnam("hcplatform")
os.setgroups([])
os.setgid(account.pw_gid)
os.setuid(account.pw_uid)
os.chdir(base/"app/backend")
os.execve(commands[role][0],commands[role],env)
