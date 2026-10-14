#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
out="${AMDNR_BUILD_DIR:-$root/build}"
rocm="${ROCM_PATH:-/opt/rocm}"
mkdir -p "$out/generated" "$out/x86_64-unix" "$out/x86_64-windows"
"${CC:-cc}" -shared -fPIC -std=gnu11 -O2 -g -Wall -Wextra -Werror \
    "$root/bridge/signal_guard.c" -ldl -lpthread -o "$out/x86_64-unix/libamdnr_signal_guard.so"
python3 "$root/bridge/generate.py" "$out/generated"
"${WINEGCC:-winegcc}" -m64 -shared -std=gnu11 -O2 -g -Wall -Wextra -Werror=implicit-function-declaration \
    -I"$rocm/include" -I"$root/bridge" \
    "$root/bridge/loader.c" "$root/bridge/identity.c" "$root/bridge/interop.c" "$root/bridge/fence.c" "$root/bridge/vulkan_memory.c" "$root/bridge/diagnostics.c" \
    "$out/generated/forward.c" -Wb,--export="$out/generated/amdhip64_7.spec" \
    -ldl -lpthread -lntdll -ldxgi -ld3d12 -luuid -o "$out/x86_64-unix/amdhip64_7.dll"
"${WINEBUILD:-winebuild}" --dll --fake-module -E "$out/generated/amdhip64_7.spec" \
    -F amdhip64_7.dll -b x86_64-windows -o "$out/x86_64-windows/amdhip64_7.dll"
printf 'Built Linux HIP bridge: %s\n' "$out"
