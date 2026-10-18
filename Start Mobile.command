#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
trap 'status=$?; if [ "$status" -ne 0 ]; then printf "\nAirPath Mobile could not start. Check the error above.\n"; read -r -p "Press Return to close..." || true; fi' EXIT
printf 'AirPath Mobile - local demo preview\n'

case "$(uname -m)" in
  arm64) target=darwin-arm64 ;;
  x86_64) target=darwin-x64 ;;
  *) printf 'This launcher supports Intel and Apple Silicon Macs.\n'; exit 1 ;;
esac
node_version=22.23.3
node_root="$PWD/.local/node/node-v$node_version-$target"
node_bin="$node_root/bin/node"
npm_cli="$node_root/lib/node_modules/npm/bin/npm-cli.js"
if [ ! -x "$node_bin" ] || [ ! -f "$npm_cli" ]; then
  if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1 && node -e 'const [a,b]=process.versions.node.split(".").map(Number); process.exit(a > 22 || (a === 22 && b >= 12) ? 0 : 1)'; then
    candidate_node=$(command -v node)
    candidate_npm=$("$candidate_node" -e 'const fs=require("fs"),p=require("path"),d=p.dirname(process.execPath); const found=[p.join(d,"node_modules/npm/bin/npm-cli.js"),p.resolve(d,"../lib/node_modules/npm/bin/npm-cli.js")].find(file=>fs.existsSync(file)); console.log(found||"")')
    if [ -n "$candidate_npm" ] && [ -f "$candidate_npm" ]; then
      node_bin="$candidate_node"
      npm_cli="$candidate_npm"
    fi
  fi
fi
if [ ! -x "$node_bin" ] || [ ! -f "$npm_cli" ]; then
  download_dir="$PWD/.local/node"
  mkdir -p "$download_dir"
  filename="node-v$node_version-$target.tar.gz"
  archive="$download_dir/$filename"
  base_url="https://nodejs.org/dist/v$node_version"
  printf 'Downloading Node.js %s into the project...\n' "$node_version"
  curl --fail --location --retry 3 "$base_url/$filename" -o "$archive"
  expected=$(curl --fail --silent --show-error --location "$base_url/SHASUMS256.txt" | awk -v file="$filename" '$2 == file { print $1 }')
  actual=$(shasum -a 256 "$archive" | awk '{ print $1 }')
  [ "$actual" = "$expected" ] || { printf 'Node.js download checksum mismatch. Please launch again.\n'; exit 1; }
  tar -xzf "$archive" -C "$download_dir"
  rm -f -- "$archive"
fi
export AIRPATH_NPM_CLI="$npm_cli"
"$node_bin" "$PWD/mobile/scripts/launch.mjs" "$@"
