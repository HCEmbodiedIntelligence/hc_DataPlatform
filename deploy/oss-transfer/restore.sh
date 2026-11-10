#!/usr/bin/env bash
# Run on a new, empty Linux amd64 host with Docker Engine and the Compose plugin.
set -euo pipefail
umask 077
bundle="$(cd -- "$(dirname -- "$0")" && pwd)"
target="${1:-}"
[[ $# == 1 && "$target" == /* ]] || { echo 'Usage: restore.sh /absolute/new/application-directory' >&2; exit 2; }
cd "$bundle"
[[ -s SHA256SUMS ]] || { echo 'Bundle not finalized.' >&2; exit 2; }
sha256sum -c SHA256SUMS
[[ "$(docker info --format '{{.OSType}}/{{.Architecture}}')" =~ ^linux/(amd64|x86_64)$ ]] || { echo 'Expected a Linux amd64 Docker host.' >&2; exit 2; }
docker compose version >/dev/null
project=hc-data-platform-oss
volumes=(postgres-data alignment-cache media-staging oss-runtime-cache frontend-deps)
bundle_id="$(sha256sum SHA256SUMS | cut -d' ' -f1)"
envfile="${HC_MOVE_ENV_FILE:-$HOME/.config/hc-data-platform/oss.env}"
[[ -f "$envfile" && -r "$envfile" ]] || { echo 'Provision the original deployment .env in the private config directory first.' >&2; exit 2; }
if [[ ! -e "$target" ]]; then
  [[ -z "$(docker ps -aq --filter "label=com.docker.compose.project=$project")" ]] || { echo 'OSS deployment already exists: refusing to overwrite.' >&2; exit 2; }
  for v in "${volumes[@]}"; do
    if docker volume inspect "${project}_$v" >/dev/null 2>&1; then echo "Existing volume: ${project}_$v" >&2; exit 2; fi
  done
  mkdir -m 700 -p "$target/.restore"
  printf '%s\n' "$bundle_id" >"$target/.restore/bundle-id"
else
  [[ -f "$target/.restore/bundle-id" && "$(cat "$target/.restore/bundle-id")" == "$bundle_id" ]] || { echo 'Target is not a new directory or this bundle’s restore.' >&2; exit 2; }
fi
cd "$target"
if [[ ! -e .restore/code ]]; then
  tar -xzpf "$bundle/app.tar.gz"
  # These were only read-only capacity mounts for the old MinIO version.
  python3 - <<'PY'
from pathlib import Path
p=Path('deploy/local/oss.compose.yaml')
s=p.read_text()
for key in ('local-alignment-capacity','local-media-capacity'):
 import re
 s=re.sub(r'^  '+key+r':\n(?:    [^\n]*\n)+', '', s,flags=re.M)
p.write_text(s)
PY
  install -m 600 "$bundle/images.json" images.json
  touch .restore/code
fi
bash compose-oss.sh config --quiet
if [[ ! -e .restore/images ]]; then
  docker load -i "$bundle/images.tar"
  while read -r s tag id; do [[ "$(docker inspect --type=image -f '{{.Id}}' "$tag")" == "$id" ]]; done <"$bundle/image-map.tsv"
  touch .restore/images
fi
for v in "${volumes[@]}"; do
  name="${project}_$v"
  if docker volume inspect "$name" >/dev/null 2>&1; then
    [[ "$(docker volume inspect -f '{{index .Labels "hc.oss.bundle"}}' "$name")" == "$bundle_id" ]] || { echo "Foreign volume: $name" >&2; exit 2; }
  else
    docker volume create --label "hc.oss.bundle=$bundle_id" "$name" >/dev/null
  fi
done
image="$(awk '$1=="api" {print $2}' "$bundle/image-map.tsv")"
for v in alignment-cache media-staging oss-runtime-cache frontend-deps; do
  [[ ! -e ".restore/$v" ]] || continue
  docker run --rm -i --network none --user 0:0 --entrypoint tar --mount "type=volume,src=${project}_$v,dst=/v" "$image" --numeric-owner -C /v -xpf - <"$bundle/$v.tar"
  touch ".restore/$v"
done
c(){ bash compose-oss.sh "$@"; }
c up -d --wait --wait-timeout 180 --no-build postgres
for db in hc_data temporal temporal_visibility; do
  [[ ! -e ".restore/$db" ]] || continue
  if [[ "$(c exec -T postgres psql -XAt -U hc -d postgres -c "SELECT count(*) FROM pg_database WHERE datname='$db'")" == 0 ]]; then
    c exec -T postgres createdb -U hc --owner=hc "$db"
  fi
  [[ "$(c exec -T postgres psql -XAt -U hc -d "$db" -c "SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')")" == 0 ]] || { echo "Non-empty database: $db. Restore stopped without overwriting it." >&2; exit 2; }
  c exec -T postgres pg_restore --single-transaction --exit-on-error --no-owner --no-acl -U hc -d "$db" <"$bundle/$db.dump"
  touch ".restore/$db"
done
c exec -T postgres psql -X -v ON_ERROR_STOP=1 -U hc -d hc_data -Atc "SELECT h.dataset_id,h.collection_version_id,c.content_hash,jsonb_array_length(c.document->'episodes') FROM episode_store.collection_heads h JOIN episode_store.collections c USING(organization_id,project_id,region_code,dataset_id,collection_version_id) WHERE h.organization_id='01' AND h.project_id='01' AND h.region_code='global' ORDER BY 1" >.restore/heads.actual
diff -u "$bundle/dataset-heads.txt" .restore/heads.actual
# Verify that the restored encrypted settings can actually read immutable data before resuming jobs.
c run --rm --no-deps -T --entrypoint python api - < verify-oss.py
c up -d --no-build postgres temporal migration runtime-cache-init api frontend gateway worker
for _ in {1..120}; do
  if curl --noproxy '*' -fsS --max-time 3 http://127.0.0.1:8089/healthz >/dev/null &&
     curl --noproxy '*' -fsS --max-time 4 http://127.0.0.1:8089/ >/dev/null &&
     [[ "$(docker inspect -f '{{.State.Health.Status}}' hc-data-platform-oss-worker-1 2>/dev/null)" == healthy ]]; then
      echo 'OSS platform ready: http://127.0.0.1:8089'; cat .restore/heads.actual; exit 0
  fi
  sleep 2
done
c ps -a
echo 'Restore data retained. Check API, frontend and worker logs.' >&2
exit 1
