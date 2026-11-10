#!/usr/bin/env bash
# "prepare" is online. "finalize" quiesces the source and leaves it stopped.
set -euo pipefail
umask 077
action="${1:-}"; dst="${2:-}"
[[ $# == 2 && ( "$action" == prepare || "$action" == finalize ) && "$dst" == /* ]] || { echo 'Usage: pack.sh prepare|finalize /absolute/private/bundle' >&2; exit 2; }
root="$(cd -- "$(dirname -- "$0")/../.." && pwd)"
project=hc-data-platform-oss
services=(postgres temporal migration runtime-cache-init api worker frontend gateway)
dbs=(hc_data temporal temporal_visibility)
get_image(){ docker inspect -f '{{.Image}}' "$project-$1-1"; }
if [[ "$action" == prepare ]]; then
  [[ ! -e "$dst" ]] || { echo 'Choose a new bundle directory.' >&2; exit 2; }
  [[ "$(docker image inspect --format '{{.Architecture}}/{{.Os}}' "$(get_image api)")" == amd64/linux ]]
  mkdir -m 700 -p "$dst"
  cd "$dst"
  : > image-map.tsv
  images=()
  for s in "${services[@]}"; do
    id="$(get_image "$s")"; h="${id#sha256:}"; tag="hc-oss-move/content:${h}"
    docker tag "$id" "$tag"
    printf '%s\t%s\t%s\n' "$s" "$tag" "$id" >>image-map.tsv
    if [[ " ${images[*]} " != *" $tag "* ]]; then images+=("$tag"); fi
  done
  python3 - <<'PY'
import json
from pathlib import Path
rows=[l.split() for l in Path('image-map.tsv').read_text().splitlines()]
Path('images.json').write_text(json.dumps({'services':{s:{'image':tag,'pull_policy':'never'} for s,tag,_ in rows}}))
PY
  docker image save -o images.tar.partial "${images[@]}"
  mv images.tar.partial images.tar
  install -m 700 "$root/deploy/oss-transfer/restore.sh" restore.sh
  printf '%s\n' "$project" >PREPARED
  echo "Online preparation complete: $dst"
  exit
fi
cd "$dst"
[[ -f PREPARED && -s images.tar && ! -e SHA256SUMS ]] || { echo 'Expected prepared, unfinalized bundle.' >&2; exit 2; }
while read -r s tag id; do [[ "$(get_image "$s")" == "$id" ]] || { echo "Image changed: $s; prepare a fresh bundle." >&2; exit 2; }; done < image-map.tsv
# Store live code, including changes not committed to Git. Configuration secrets are provisioned separately over SSH.
tar --exclude=node_modules --exclude=.venv --exclude=__pycache__ --exclude=.pytest_cache --exclude=.ruff_cache --exclude=dist --exclude=.env --exclude='.env.*' \
  -czf app.tar.gz.partial -C "$root" backend frontend compose.dev.yaml deploy/compose/gateway.conf deploy/local/current.compose.yaml deploy/local/oss.compose.yaml \
  -C "$root/deploy/oss-transfer" new-host.yaml compose-oss.sh verify-oss.py -C "$root/../.data-platform-tools" robot-previews
mv app.tar.gz.partial app.tar.gz
printf 'Stopping source gateway, frontend, API, worker and Temporal for the final snapshot.\n'
for s in gateway frontend api worker temporal; do docker stop -t 330 "$project-$s-1" >/dev/null; done
for s in gateway frontend api worker temporal; do [[ "$(docker inspect -f '{{.State.Running}}' "$project-$s-1")" == false ]]; done
for db in "${dbs[@]}"; do
  docker exec "$project-postgres-1" pg_dump -U hc -d "$db" -Fc --no-owner --no-acl >"$db.dump.partial"
  docker exec -i "$project-postgres-1" pg_restore -l <"$db.dump.partial" >/dev/null
  mv "$db.dump.partial" "$db.dump"
done
docker exec "$project-postgres-1" psql -X -v ON_ERROR_STOP=1 -U hc -d hc_data -Atc "SELECT h.dataset_id,h.collection_version_id,c.content_hash,jsonb_array_length(c.document->'episodes') FROM episode_store.collection_heads h JOIN episode_store.collections c USING(organization_id,project_id,region_code,dataset_id,collection_version_id) WHERE h.organization_id='01' AND h.project_id='01' AND h.region_code='global' ORDER BY 1" >dataset-heads.txt
[[ -s dataset-heads.txt ]]
image="$(get_image api)"
for name in alignment-cache media-staging oss-runtime-cache frontend-deps; do
  vol="$project"_"$name"
  if [[ "$name" == frontend-deps ]]; then
    vol="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/node_modules"}}{{.Name}}{{end}}{{end}}' "$project-frontend-1")"
  fi
  [[ -n "$vol" ]]; docker volume inspect "$vol" >/dev/null
  docker run --rm --network none --user 0:0 --entrypoint tar --mount "type=volume,src=$vol,dst=/v,readonly" "$image" --numeric-owner -C /v -cpf - . >"$name.tar.partial"
  mv "$name.tar.partial" "$name.tar"
done
sha256sum images.tar images.json image-map.tsv app.tar.gz restore.sh dataset-heads.txt *.dump alignment-cache.tar media-staging.tar oss-runtime-cache.tar frontend-deps.tar >SHA256SUMS.partial
mv SHA256SUMS.partial SHA256SUMS
echo "Final snapshot ready. Keep the source OSS workers stopped during cutover: $dst"
