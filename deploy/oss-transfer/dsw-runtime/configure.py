"""Render the native services using the same configuration as the OSS Compose deployment."""
import json
import os
import pwd
import secrets
import subprocess
import sys
from pathlib import Path
import yaml

base = Path("/mnt/workspace/hc-platform")
run = base / "service"
runtime = base / "runtime"
repo = base / "app"
mode = sys.argv[1]
if mode not in {"preview", "live"}:
    raise SystemExit("Expected preview or live.")
dbbase = base if mode == "preview" else Path("/mnt/workspace/hc-live-backup")
host, port, _, user, password = (dbbase / "config/postgres.pgpass").read_text().strip().split(":")
assert host == "127.0.0.1" and user == "hc" and port == ("55432" if mode == "preview" else "55433")
command = [str(runtime/"bin/compose-config"), "--project-directory", str(repo), "--env-file", str(base/"config/oss.env"), "-p", "hc-data-platform-oss"]
for file in ("compose.dev.yaml","deploy/local/current.compose.yaml","deploy/local/oss.compose.yaml"):
    command.extend(["-f",str(repo/file)])
composed = json.loads(subprocess.check_output(command+["config","--format","json"], env={"PATH":os.defpath,"HOME":"/root"}))["services"]
cache = base / "cache"
assets = base / "assets/robot-previews"
roots = {k:str(v) for k,v in {"oss_cache":cache/"oss-runtime-cache","alignment":cache/"alignment-cache","media":cache/"media-staging","models":assets}.items()}
account = pwd.getpwnam("hcplatform")
def write(path, content, readable=False):
    temporary = path.with_suffix(path.suffix+".new")
    temporary.write_text(content)
    temporary.chmod(0o600)
    if readable:
        os.chown(temporary, account.pw_uid, account.pw_gid)
    temporary.replace(path)

for role in ("api","worker"):
    env = {k:str(v) for k,v in composed[role]["environment"].items() if k.startswith("HC_") and v is not None}
    assert env["HC_OBJECT_STORE_PROVIDER"] == "oss" and env["HC_PLATFORM_ENVIRONMENT_ID"] == "hc-oss"
    env.update(HC_POSTGRES_DSN=f"postgresql+asyncpg://hc:{password}@{host}:{port}/hc_data",
               HC_TEMPORAL_TARGET="127.0.0.1:7233", HC_MIGRATIONS_DIR=str(repo/"backend/migrations"),
               HC_OBJECT_STORE_CACHE_ROOT=roots["oss_cache"], HC_ALIGNMENT_STAGING_ROOT=roots["alignment"],
               HC_ALIGNED_MEDIA_STAGING_ROOT=roots["media"], HC_ROBOT_MODEL_ASSET_ROOT=str(cache/"robot-assets"),
               HC_ROBOT_MODEL_PREVIEW_ROOT=str(assets), HC_STORAGE_LOCAL_USAGE_ROOTS=json.dumps(roots),
               HC_STORAGE_DISK_USAGE_PATH=str(base))
    if mode == "live" and env.get("HC_JWT_ALGORITHMS") == '["HS256"]':
        keyfile = base/"config/site-jwt.key"
        if not keyfile.exists():
            write(keyfile,secrets.token_urlsafe(64))
        env["HC_JWT_SIGNING_KEY"] = keyfile.read_text().strip()
    write(run/f"{role}.json",json.dumps(env))
template = json.loads((runtime/"ctl/temporal-base.json").read_text())
stores = {k:{"sql":{"pluginName":"postgres12","databaseName":db,"connectAddr":f"{host}:{port}","connectProtocol":"tcp","user":user,"password":password,"maxConns":12,"maxIdleConns":4}} for k,db in [("default","temporal"),("visibility","temporal_visibility")]}
temporal = {"log":{"stdout":True,"level":"warn"},"persistence":{"numHistoryShards":template["shards"],"defaultStore":"default","visibilityStore":"visibility","datastores":stores},
            "global":{"membership":{"maxJoinDuration":"30s","broadcastAddress":"127.0.0.1"},"pprof":{"port":0}},
            "services":{k:{"rpc":{"grpcPort":p,"membershipPort":m,"bindOnIP":host}} for k,p,m in [("frontend",7233,6933),("history",7234,6934),("matching",7235,6935),("worker",7239,6939)]},
            "clusterMetadata":template["cluster"], "dynamicConfigClient":{"filepath":str(runtime/"ctl/dynamic.yaml"),"pollInterval":"60s"}}
temporal["services"]["frontend"]["rpc"]["httpPort"]=7243
write(run/"dsw.yaml",yaml.safe_dump(temporal),True)
conf=(runtime/"ctl/nginx.conf").read_text()
guard = 'if ($hc_preview_allowed = 0) { return 403; }' if mode=="preview" else ""
write(run/"nginx.conf",conf.replace("@PREVIEW@",guard),True)
header = f"""[unix_http_server]
file={run}/control.sock
chmod=0600
[supervisord]
logfile={run}/supervisor.log
pidfile={run}/supervisor.pid
childlogdir={run}
[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface
[supervisorctl]
serverurl=unix://{run}/control.sock
"""
for n, role in enumerate(("temporal","api","web","worker")):
    start = "false" if role=="worker" and mode=="preview" else "true"
    header+=f"""
[program:{role}]
command={runtime}/venv/bin/python {runtime}/ctl/service.py {role}
priority={10+n*10}
autostart={start}
autorestart=true
startsecs=2
startretries=8
stopasgroup=true
killasgroup=true
stopwaitsecs=120
stdout_logfile={run}/{role}.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=2
redirect_stderr=true
"""
write(run/"supervisor.conf",header)
