#!/usr/bin/env bash
set -euo pipefail
umask 077
base=/mnt/workspace/hc-platform
run=$base/service
bin=$base/runtime/bin/cloudflared
[[ -x "$bin" && -s "$run/live-source" && "$(cat "$run/mode")" == live ]]
[[ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8089/api/v1/projects/01/collection-tasks)" == 401 ]]
cfg=$run/https-supervisor.conf
cat > "$cfg" <<CONF
[unix_http_server]
file=$run/https.sock
chmod=0600
[supervisord]
logfile=$run/https-supervisor.log
pidfile=$run/https.pid
childlogdir=$run
[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface
[supervisorctl]
serverurl=unix://$run/https.sock
[program:https]
command=$bin tunnel --no-autoupdate --protocol http2 --edge-ip-version 4 --metrics 127.0.0.1:20243 --url http://127.0.0.1:8089
user=hcplatform
environment=HOME="$base/cache/service-home"
autostart=true
autorestart=true
startretries=8
stdout_logfile=$run/https.log
stdout_logfile_maxbytes=5MB
redirect_stderr=true
CONF
if ! supervisorctl -c "$cfg" pid 2>/dev/null | grep -Eq '^[1-9][0-9]*$'; then supervisord -c "$cfg"; fi
for _ in {1..45}; do
  if curl -fsS --max-time 2 http://127.0.0.1:20243/ready >/dev/null 2>&1; then
    url=$(grep -Eo 'https://[a-z0-9-]+[.]trycloudflare[.]com' "$run/https.log" | tail -1)
    [[ -n "$url" ]]; echo "$url"; exit 0
  fi
  sleep 2
done
echo 'HTTPS connector not yet ready' >&2; exit 1
