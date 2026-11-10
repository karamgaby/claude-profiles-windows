#!/usr/bin/env bash
# Freeze claude-swap for Linux, for Claude Profiles to unpack into WSL distros.
#
#   bash tools/cswap/build_linux.sh [OUT_DIR]     (default: dist/linux)
#
# Writes OUT_DIR/cswap-linux-X.Y.Z.tar.gz holding cswap/cswap. Runs on Linux or
# inside WSL. The Python is uv-managed (python-build-standalone, built against
# an old glibc), so the result runs on old distros whatever distro builds it.
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"   # uv's own install location
root="$(cd "$(dirname "$0")/../.." && pwd)"
out="$(realpath -m "${1:-$root/dist/linux}")"
# Build on the Linux filesystem: a venv on a Windows drive is slow and fragile.
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

uv venv --quiet --python 3.12 --python-preference only-managed "$work/venv"
py="$work/venv/bin/python"
uv pip install --quiet --python "$py" --require-hashes -r "$root/tools/cswap/requirements.txt"
uv pip install --quiet --python "$py" "pyinstaller>=6.22.3"
"$py" -m PyInstaller --noconfirm --clean --log-level WARN \
    --distpath "$work/dist" --workpath "$work/pyi" "$root/cswap.spec"

version="$("$work/dist/cswap/cswap" --version | awk '{print $2}')"
pinned="$(sed -n 's/^claude-swap==//p' "$root/tools/cswap/requirements.in" | tr -d '\r')"  # a Windows checkout has CRLF
[ "$version" = "$pinned" ] || { echo "frozen cswap reports '$version', expected '$pinned'" >&2; exit 1; }

mkdir -p "$out"
rm -f "$out"/cswap-linux-*.tar.gz
tar -czf "$out/cswap-linux-$version.tar.gz" -C "$work/dist" cswap
echo "$out/cswap-linux-$version.tar.gz"
