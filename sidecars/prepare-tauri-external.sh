#!/usr/bin/env bash
# Tauri externalBin expects sidecars/aria2c-$TARGET_TRIPLE (see tauri.conf.json).
set -euo pipefail
cd "$(dirname "$0")"
host="$(rustc -vV | sed -n 's/^host: //p')"

if [[ "$host" == *"-pc-windows-"* ]]; then
  src="aria2c.exe"
  dst="aria2c-${host}.exe"
else
  src="aria2c"
  dst="aria2c-${host}"
fi

if [[ ! -f "$src" ]]; then
  echo "sidecars/prepare-tauri-external.sh: missing sidecars/$src (see docs/DEV.md)" >&2
  exit 1
fi

cp -f "$src" "$dst"
chmod +x "$dst" 2>/dev/null || true
echo "sidecars/$dst"
