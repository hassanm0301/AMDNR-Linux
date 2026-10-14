#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source="${AMDNR_VKD3D_SOURCE:-$root/deps/vkd3d-proton}"
output="${AMDNR_BUILD_DIR:-$root/build}"
revision=0bb64c56c8f0dcc11ecc0f6c56e00e4f7bd8d506
if [[ ! -d "$source/.git" ]]; then
    mkdir -p "$(dirname -- "$source")"
    git clone --no-checkout https://github.com/HansKristian-Work/vkd3d-proton.git "$source"
    git -C "$source" checkout --detach "$revision"
fi
[[ "$(git -C "$source" rev-parse HEAD)" == "$revision" ]] || {
    echo "vkd3d-proton must be at $revision; use a separate dependency checkout." >&2; exit 1;
}
git -C "$source" submodule update --init --recursive
patch="$root/patches/vkd3d-proton-shared-buffers.patch"
if git -C "$source" apply --reverse --check "$patch" 2>/dev/null; then
    : # already patched
else
    git -C "$source" apply --check "$patch"
    git -C "$source" apply "$patch"
fi
if [[ ! -f "$source/build-amdnr/build.ninja" ]]; then
    meson setup "$source/build-amdnr" "$source" --cross-file "$source/build-win64.txt" \
        --buildtype release -Denable_tests=false -Denable_extras=false
fi
ninja -C "$source/build-amdnr" -j "${AMDNR_BUILD_JOBS:-8}"
mkdir -p "$output"
cp "$source/build-amdnr/libs/d3d12/d3d12.dll" "$output/d3d12.dll"
cp "$source/build-amdnr/libs/d3d12core/d3d12core.dll" "$output/d3d12core.dll"
cp "$source/COPYING" "$output/vkd3d-proton-COPYING"
printf '%s\n' "$revision" > "$output/vkd3d-proton-revision"
