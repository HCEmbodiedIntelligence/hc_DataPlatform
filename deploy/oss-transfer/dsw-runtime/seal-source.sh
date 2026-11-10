
#!/usr/bin/env bash
set -euo pipefail
umask 077
repo=$(cd "$(dirname "$0")/../../.." && pwd)
dst=${1:?new absolute final snapshot directory}
[[ "$dst" == /* && ! -L "$dst" ]]
exec 9>"$HOME/.hc-dsw-seal.lock"
flock -n 9 || exit 1
if [[ -e "$dst" ]]; then
  [[ -s "$dst/FINAL-SNAPSHOT" ]] || { echo 'Existing incomplete output. Choose a new directory.' >&2; exit 1; }
  for svc in gateway frontend api worker temporal; do
    [[ "$(docker inspect -f '{{.State.Running}}' "hc-data-platform-oss-$svc-1")" == false ]] || { echo 'Source has restarted. Take a fresh final snapshot.' >&2; exit 1; }
  done
  (cd "$dst"; test "$(sha256sum SHA256SUMS | cut -d' ' -f1)" = "$(cat FINAL-SNAPSHOT)")
  exit
fi
docker inspect hc-data-platform-oss-postgres-1 >/dev/null
for svc in gateway frontend api worker temporal; do
  docker stop -t 330 "hc-data-platform-oss-$svc-1"
done
for svc in gateway frontend api worker temporal; do
  [[ "$(docker inspect -f '{{.State.Running}}' "hc-data-platform-oss-$svc-1")" == false ]]
done
bash "$repo/deploy/oss-transfer/dsw-snapshot.sh" "$dst"
sha256sum "$repo/.env" | cut -d' ' -f1 > "$dst/CONFIG-SHA256"
sha256sum "$dst/SHA256SUMS" | cut -d' ' -f1 > "$dst/FINAL-SNAPSHOT"
echo "Source stopped; final OSS data ready in $dst"
