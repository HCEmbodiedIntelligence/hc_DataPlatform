#!/usr/bin/env bash
set -euo pipefail
umask 077
base=/mnt/workspace/hc-platform
run=$base/service
rt=$base/runtime
mode=${1:-status}
super(){ supervisorctl -c "$run/supervisor.conf" "$@"; }
running(){ [[ -S "$run/control.sock" ]] && super pid 2>/dev/null | grep -Eq '^[1-9][0-9]*$'; }
if [[ "$mode" == status ]]; then super status; exit; fi
exec 8>"$run/.manage-lock"
flock -n 8 || { echo 'Another platform operation is active.' >&2; exit 1; }
if [[ "$mode" == stop ]]; then
  if running; then super shutdown; for _ in {1..130}; do [[ ! -S "$run/control.sock" ]] && break; sleep 1; done; fi
  [[ ! -S "$run/control.sock" ]]; exit
fi
if [[ "$mode" == resume ]]; then mode=$(cat "$run/mode" 2>/dev/null || echo preview); fi
case "$mode" in
  preview) db=/mnt/workspace/hc-platform-pg16; port=55432 ;;
  live) db=/mnt/workspace/hc-live-pg16; port=55433; test -s /mnt/workspace/hc-live-backup/db-backups/dataset-heads.restored.txt ;;
  *) echo 'Expected preview, live, resume, status or stop.' >&2; exit 1 ;;
esac
pg=/usr/lib/postgresql/16/bin
if ! runuser -u postgres -- "$pg/pg_ctl" status -D "$db/data" >/dev/null; then
  runuser -u postgres -- "$pg/pg_ctl" start -D "$db/data" -o "-h 127.0.0.1 -k $db/socket -p $port" -l "$db/data/server.log" -w 8>&-
fi
if running && [[ "$(cat "$run/mode")" != "$mode" ]]; then echo 'Stop the preview before changing mode.' >&2; exit 1; fi
if ! running; then
  PYTHONPATH="$rt/python" "$rt/venv/bin/python" "$rt/ctl/configure.py" "$mode"
  "$rt/venv/bin/python" "$rt/ctl/service.py" verify
  printf '%s\n' "$mode" > "$run/mode"
  supervisord -c "$run/supervisor.conf" 8>&-
else
  super start temporal api web >/dev/null || true
  if [[ "$mode" == live ]]; then super start worker >/dev/null || true; fi
fi
for _ in {1..120}; do
  if curl --noproxy '*' -fsS --max-time 3 http://127.0.0.1:8089/health/ready >/dev/null 2>&1 &&
     curl --noproxy '*' -fsS http://127.0.0.1:8089/ | grep -q 'id="root"' &&
     { [[ "$mode" == preview ]] || super status worker | grep -q RUNNING; }; then
    echo "DSW $mode ready on 127.0.0.1:8089"; exit 0
  fi
  sleep 2
done
super status || true
echo "Read service logs in $run" >&2
exit 1
