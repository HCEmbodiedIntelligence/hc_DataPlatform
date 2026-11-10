#!/usr/bin/env bash
set -euo pipefail
umask 022
rt=/mnt/workspace/hc-platform/runtime
cd "${1:-/root/hc-dsw-media-release}"
sha256sum -c SHA256SUMS
if [[ ! -d "$rt/media" ]]; then
  staged=$(mktemp -d "$rt/.media-XXXXXXXX")
  chmod 755 "$staged"
  tar -xzf ffmpeg.tar.gz --no-same-owner -C "$staged"
  chmod 755 "$staged/bin" "$staged/lib"
  mv -T "$staged" "$rt/media"
fi
setfacl -R -m u:hcplatform:rX "$rt/media"
for prog in ffmpeg ffprobe; do
  target="$rt/venv/bin/$prog"
  printf '%s\n' '#!/bin/sh' "exec \"$rt/media/lib/ld-linux-x86-64.so.2\" --library-path \"$rt/media/lib\" \"$rt/media/bin/$prog\" \"\$@\"" >"$target.new"
  chmod 755 "$target.new"
  mv "$target.new" "$target"
done
sample=$(mktemp -d /tmp/hc-dsw-video-XXXXXXXX)
chown hcplatform:hcplatform "$sample"
trap 'rm -f "$sample/check.mp4"; rmdir "$sample"' EXIT
runuser -u hcplatform -- "$rt/venv/bin/ffmpeg" -hide_banner -loglevel error -f lavfi -i color=c=black:s=96x64:r=10 -frames:v 5 -c:v libx264 -pix_fmt yuv420p -fps_mode cfr -y "$sample/check.mp4"
runuser -u hcplatform -- "$rt/venv/bin/ffprobe" -v error -select_streams v:0 -show_entries stream=codec_name,nb_frames,width,height -of json "$sample/check.mp4"
"$rt/venv/bin/ffmpeg" -version | sed -n '1p'
