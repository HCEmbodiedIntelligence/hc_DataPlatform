
#!/usr/bin/env bash
# Run on DSW with the stopped source's final snapshot.
set -euo pipefail
umask 077
source_dir=${1:?pass the final snapshot directory}
src=$(realpath -e "$source_dir")
base=/mnt/workspace/hc-platform
db=/mnt/workspace/hc-live-backup
rt=$base/runtime
cd "$src"
id=$(sha256sum SHA256SUMS | cut -d' ' -f1)
[[ "$(cat FINAL-SNAPSHOT)" == "$id" ]] || { echo 'Expected a final snapshot from the stopped OSS service.' >&2; exit 1; }
sha256sum -c SHA256SUMS
[[ "$(sha256sum "$base/config/oss.env" | cut -d' ' -f1)" == "$(cat CONFIG-SHA256)" ]] || { echo 'Copy the final source .env to config/oss.env before proceeding.' >&2; exit 1; }
exec 9>"$base/config/.cutover.lock"
flock -n 9 || exit 1
if [[ -e "$base/service/live-source" ]]; then
  [[ "$(cat "$base/service/live-source")" == "$id" ]] || { echo 'Another final snapshot was installed.' >&2; exit 1; }
  exec bash "$rt/ctl/manage.sh" live 9>&-
fi
if [[ ! -e "$db" ]]; then
  mkdir -m 700 "$db"
  printf '%s\n' "$id" > "$db/.snapshot-id"
fi
[[ "$(cat "$db/.snapshot-id")" == "$id" ]] || { echo 'Refusing to overwrite a different live database.' >&2; exit 1; }
install -d -m 700 "$db/config" "$db/db-backups"
install -m 600 "$base/config/oss.env" "$db/config/oss.env"
install -m 600 SHA256SUMS "$db/db-backups/transfer.sha256"
for f in hc_data.dump temporal.dump temporal_visibility.dump dataset-heads.txt; do install -m 600 "$f" "$db/db-backups/$f"; done
HC_DSW_RESTORE_MODE=live bash "$rt/ctl/db-restore.sh" 9>&-
stage=$src/.unpacked-for-live
mkdir -p "$stage"
for name in code models alignment-cache media-staging oss-runtime-cache; do
  mkdir -p "$stage/$name"
  if [[ ! -f "$stage/$name.done" ]]; then
    tar -xzf "$src/$name.tar.gz" --no-same-owner -C "$stage/$name"
    touch "$stage/$name.done"
  fi
done
for path in frontend/src frontend/public frontend/package.json frontend/pnpm-lock.yaml frontend/vite.config.ts frontend/index.html backend/pyproject.toml backend/uv.lock; do
  diff -qr "$base/app/$path" "$stage/code/$path" >/dev/null || { echo "Runtime build inputs changed: $path. Build a runtime from the final code before cutover." >&2; exit 1; }
done
bash "$rt/ctl/manage.sh" stop
for n in alignment-cache media-staging oss-runtime-cache; do
  rsync -a --checksum --delete-delay --chown=hcplatform:hcplatform "$stage/$n/" "$base/cache/$n/"
done
rsync -a --delete-delay "$stage/models/" "$base/assets/"
rsync -a --checksum --delete-delay "$stage/code/backend/" "$base/app/backend/"
for path in compose.dev.yaml deploy/local/current.compose.yaml deploy/local/oss.compose.yaml; do install -m 600 "$stage/code/$path" "$base/app/$path"; done
setfacl -R -m u:hcplatform:rX "$base/app/backend" "$base/assets"
printf '%s\n' "$id" > "$base/service/live-source.tmp"
mv "$base/service/live-source.tmp" "$base/service/live-source"
exec bash "$rt/ctl/manage.sh" live 9>&-
