#!/usr/bin/env bash
set -euo pipefail
umask 077
repo=$(cd "$(dirname "$0")/../../.." && pwd)
out=${1:?new absolute output directory}
[[ "$out" == /* && ! -e "$out" ]] || exit 2
mkdir -m 700 "$out"
svc=hc-data-platform-oss
src=$HOME/hc-dsw-transfer
cd "$out"
stage=$(mktemp -d "$out/.build-XXXXXX")
front=$(docker exec "$svc-frontend-1" mktemp -d /tmp/hc-dsw-XXXXXXXX)
trap 'docker exec "$svc-frontend-1" sh -c '\''case "$1" in /tmp/hc-dsw-????????) rm -rf -- "$1";; esac'\'' sh "$front" >/dev/null || true' EXIT
mkdir -p "$stage"/{bin,web,python,ctl}
docker exec -i "$svc-frontend-1" tar -xz -C "$front" --strip-components=1 frontend <"$src/code.tar.gz"
docker exec "$svc-frontend-1" sh -ec 'cd "$1"; ln -s /app/node_modules node_modules; VITE_MOCK_MODE=off VITE_API_BASE_URL=/api/v1 VITE_ROBOT_PLATFORM_BASE_URL="" VITE_PUBLISH_SOURCEMAPS=false pnpm exec vite build > /dev/null' sh "$front"
docker cp "$svc-frontend-1:$front/dist/." "$stage/web"
test -s "$stage/web/index.html"
docker exec "$svc-api-1" tar --exclude='*/__pycache__' --exclude='*.pyc' -C /opt/venv/lib/python3.12/site-packages -cf - . | tar -xf - --no-same-owner -C "$stage/python"
docker cp "$svc-temporal-1:/usr/local/bin/temporal-server" "$stage/bin/temporal-server"
docker cp "$svc-temporal-1:/etc/temporal/config/dynamicconfig/docker.yaml" "$stage/ctl/dynamic.yaml"
docker exec "$svc-temporal-1" cat /etc/temporal/config/docker.yaml | python3 -c 'import sys,yaml,json; c=yaml.safe_load(sys.stdin); assert c["clusterMetadata"]["currentClusterName"]=="active"; print(json.dumps({"shards":c["persistence"]["numHistoryShards"],"cluster":c["clusterMetadata"]}))' > "$stage/ctl/temporal-base.json"
cp /usr/libexec/docker/cli-plugins/docker-compose "$stage/bin/compose-config"
cp "$repo"/deploy/oss-transfer/dsw-runtime/{manage.sh,configure.py,service.py,nginx.conf,final-data.sh} "$stage/ctl/"
cp "$repo/deploy/oss-transfer/dsw-restore-db.sh" "$stage/ctl/db-restore.sh"
cp "$repo/deploy/oss-transfer/verify-oss.py" "$stage/ctl/verify-oss.py"
cp "$repo/deploy/oss-transfer/dsw-runtime/install.sh" .
chmod 755 install.sh "$stage"/bin/*
tar -C "$stage" -cf - . | pigz -2 -p 4 > runtime.tar.gz.partial
mv runtime.tar.gz.partial runtime.tar.gz
sha256sum install.sh runtime.tar.gz > SHA256SUMS
printf '%s\n' 'Runtime bundle ready.'
