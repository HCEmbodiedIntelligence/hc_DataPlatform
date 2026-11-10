#!/usr/bin/env bash
# Stage native deployment files on the DSW work disk. No Docker or database service required.
set -euo pipefail
umask 077
bundle="$(cd -- "$(dirname -- "$0")" && pwd)"
dst="${1:-/mnt/workspace/hc-platform}"
[[ "$dst" == /mnt/workspace/* && "$(realpath -m "$dst")" == "$dst" ]] || { echo 'Use a directory on /mnt/workspace.' >&2; exit 2; }
cd "$bundle"; test -s SHA256SUMS
sha256sum -c SHA256SUMS
id="$(sha256sum SHA256SUMS | cut -d' ' -f1)"
if [[ -e "$dst" ]]; then
  [[ -f "$dst/.snapshot-id" && "$(cat "$dst/.snapshot-id")" == "$id" ]] || { echo 'Target already exists. Choose a new directory.' >&2; exit 2; }
else
  mkdir -m 700 "$dst"; printf '%s\n' "$id" > "$dst/.snapshot-id"
fi
fresh="$(df -B1 --output=avail "$dst" | tail -1)"
used="$(du --apparent-size -s -B1 "$dst" | cut -f1)"
needed="$(awk '{n+=$1}END{printf "%.0f",n}' expanded-bytes)"
(( fresh + used > needed + 2147483648 )) || { echo 'Insufficient work-disk space for extraction. Expand the DSW work disk first.' >&2; exit 2; }
mkdir -p "$dst"/{app,config,db-backups,assets,cache,.unpacked}
for name in code models alignment-cache media-staging oss-runtime-cache; do
  case "$name" in
     code) dir="$dst/app";;
     models) dir="$dst/assets";;
     *) dir="$dst/cache/$name";;
  esac
  mkdir -p "$dir"
  if [[ ! -f "$dst/.unpacked/$name" ]]; then
    tar -xzpf "$bundle/$name.tar.gz" --no-same-owner -C "$dir"
    touch "$dst/.unpacked/$name"
  fi
done
for db in hc_data temporal temporal_visibility; do
  install -m 600 "$db.dump" "$dst/db-backups/$db.dump"
  test "$(sha256sum "$db.dump" | cut -d' ' -f1)" = "$(sha256sum "$dst/db-backups/$db.dump" | cut -d' ' -f1)"
done
install -m 600 dataset-heads.txt "$dst/db-backups/dataset-heads.txt"
echo "Files verified in $dst. Database dumps are staged, not yet restored."
cat dataset-heads.txt
