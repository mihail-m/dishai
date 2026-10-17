#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
trap 'status=$?; if [ "$status" -ne 0 ]; then printf "\nDishAI could not start. Check the error above and your internet connection.\n"; read -r -p "Press Return to close..." || true; fi' EXIT

printf 'DishAI - Inversion Watch\nFirst launch downloads the tools and dependencies into this project.\n'
case "$(uname -m)" in
    arm64) target=aarch64-apple-darwin ;;
    x86_64) target=x86_64-apple-darwin ;;
    *) printf 'Unsupported Mac architecture.\n'; exit 1 ;;
esac
uv_version=0.13.1
uv_dir="$PWD/.local/uv/$uv_version"
uv="$uv_dir/uv"
if [ ! -x "$uv" ]; then
    mkdir -p "$uv_dir"
    url="https://releases.astral.sh/github/uv/releases/download/$uv_version/uv-$target.tar.gz"
    printf 'Downloading the Python setup tool...\n'
    curl --fail --location --retry 3 "$url" -o "$uv_dir/uv.tar.gz"
    expected=$(curl --fail --silent --show-error --location "$url.sha256" | awk '{print $1}')
    actual=$(shasum -a 256 "$uv_dir/uv.tar.gz" | awk '{print $1}')
    [ "$actual" = "$expected" ] || { printf 'Setup download checksum mismatch. Please launch again.\n'; exit 1; }
    tar -xzf "$uv_dir/uv.tar.gz" -C "$uv_dir"
    install -m 755 "$uv_dir/uv-$target/uv" "$uv"
    rm -f -- "$uv_dir/uv.tar.gz"
fi
export UV_CACHE_DIR="$PWD/.local/cache/uv"
export UV_PYTHON_INSTALL_DIR="$PWD/.local/python"
export UV_PYTHON_INSTALL_BIN=0
export DISHAI_UV="$uv"
export PYTHONUTF8=1 PYTHONUNBUFFERED=1
"$uv" run --isolated --no-project --no-config --managed-python --python 3.12 scripts/launch.py "$@"
