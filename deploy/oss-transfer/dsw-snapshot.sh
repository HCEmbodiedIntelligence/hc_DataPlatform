#!/usr/bin/env bash
# Online file transfer only. Does not stop or switch any service.
set -euo pipefail
umask 077
root="$(cd -- "$(dirname -- "$0")/../.." && pwd)"
out="${1:-$HOME/hc-dsw-transfer}"
[[ "$out" == /* && ! -e "$out" ]] || { echo 'Choose a new absolute snapshot directory.' >&2; exit 2; }
project=hc-data-platform-oss
pg="$project-postgres-1"
image="$(docker inspect -f '{{.Image}}' "$project-api-1")"
[[ "$(docker inspect -f '{{.State.Health.Status}}' "$pg")" == healthy ]]
mkdir -m 700 "$out"
exec 3>"$out/expanded-bytes"
compress(){ if command -v pigz >/dev/null; then pigz -p 2 -1; else gzip -1; fi; }
cd "$out"
tar --exclude=node_modules --exclude=.git --exclude=.venv --exclude=__pycache__ --exclude=.pytest_cache --exclude=.ruff_cache --exclude=dist --exclude=.env --exclude='.env.*' --exclude='*.env' \
  -C "$root" -cf - backend frontend deploy scripts start.sh stop.sh compose.dev.yaml compose.single-server.yaml | compress >code.tar.gz
tar -C "$root/../.data-platform-tools" -cf - robot-previews | compress >models.tar.gz
# Use an upper bound for source files. The target extracts from a different filesystem.
du --apparent-size -s -B1 "$root/backend" "$root/frontend" "$root/deploy" "$root/scripts" "$root/../.data-platform-tools/robot-previews" | cut -f1 >&3
for db in hc_data temporal temporal_visibility; do
  echo "Backing up $db"
  docker exec "$pg" pg_dump -Fc --no-owner --no-acl -U hc -d "$db" >"$db.dump"
  docker exec -i "$pg" pg_restore -l <"$db.dump" >/dev/null
  stat -c %s "$db.dump" >&3
done
for name in alignment-cache media-staging oss-runtime-cache; do
  vol="${project}_$name"
  docker volume inspect "$vol" >/dev/null
  echo "Copying $name"
  flags=(--rm --network none --user 0:0 --mount "type=volume,src=$vol,dst=/v,readonly")
  docker run "${flags[@]}" --entrypoint du "$image" --apparent-size -s -B1 /v | cut -f1 >&3
  docker run "${flags[@]}" --entrypoint tar "$image" -C /v --numeric-owner -cf - . | compress >"$name.tar.gz"
done
exec 3>&-
docker exec "$pg" psql -XAt -v ON_ERROR_STOP=1 -U hc -d hc_data -c "SELECT t.task_code,h.collection_version_id,jsonb_array_length(c.document->'episodes') FROM episode_store.collection_heads h JOIN episode_store.collections c USING(organization_id,project_id,region_code,dataset_id,collection_version_id) JOIN collection_tasks.collection_tasks t USING(organization_id,project_id,dataset_id) WHERE h.organization_id='01' AND h.project_id='01' AND h.region_code='global' ORDER BY 1" >dataset-heads.txt
install -m 700 "$root/deploy/oss-transfer/dsw-unpack.sh" dsw-unpack.sh
sha256sum code.tar.gz models.tar.gz *.dump alignment-cache.tar.gz media-staging.tar.gz oss-runtime-cache.tar.gz expanded-bytes dataset-heads.txt dsw-unpack.sh >SHA256SUMS.partial
mv SHA256SUMS.partial SHA256SUMS
echo "Snapshot prepared: $out"; cat dataset-heads.txt
