#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
out="${AMDNR_BUILD_DIR:-$root/build}"
rocm="${ROCM_PATH:-/opt/rocm}"
wine_headers="${WINE_INCLUDE_DIR:-/usr/include/wine}"
vulkan_headers="${VULKAN_INCLUDE_DIR:-/usr/include}"
mkdir -p "$out/generated" "$out/x86_64-unix" "$out/x86_64-windows" "$out/pe-include"
# Keep native system headers off the PE compiler's include path. Only Vulkan's
# portable headers are shared with the Windows half of the bridge.
ln -sfn "$vulkan_headers/vulkan" "$out/pe-include/vulkan"
ln -sfn "$vulkan_headers/vk_video" "$out/pe-include/vk_video"
temporary="$(mktemp -d "$out/.bridge-build.XXXXXX")"
trap 'rm -rf -- "$temporary"' EXIT
"${CC:-cc}" -shared -fPIC -std=gnu11 -O2 -g -Wall -Wextra -Werror \
    "$root/bridge/signal_guard.c" -ldl -lpthread -o "$temporary/libamdnr_signal_guard.so"
python3 "$root/bridge/generate.py" "$out/generated"
"${CC:-cc}" -shared -fPIC -std=gnu11 -O2 -g -Wall -Wextra -Werror=implicit-function-declaration \
    -I"$rocm/include" -I"$root/bridge" -I"$out/generated" \
    "$root/bridge/loader.c" "$root/bridge/native_vulkan.c" "$root/bridge/fence_unix.c" \
    "$out/generated/unix_calls.c" -ldl -lpthread -o "$temporary/amdhip64_7.so"
"${WINEGCC:-winegcc}" -b "${MINGW_TARGET:-x86_64-w64-mingw32}" -m64 -shared \
    -std=gnu11 -O2 -g -Wall -Wextra -Werror=implicit-function-declaration \
    -I"$rocm/include" -I"$root/bridge" -I"$out/generated" -I"$out/pe-include" -I"$wine_headers" \
    "$root/bridge/pe_loader.c" "$root/bridge/identity.c" "$root/bridge/interop.c" "$root/bridge/registration.c" \
    "$root/bridge/fence_pe.c" "$root/bridge/vulkan_memory_pe.c" "$root/bridge/diagnostics.c" \
    "$out/generated/native_api.c" "$out/generated/forward.c" \
    -Wb,--export="$out/generated/amdhip64_7.spec" -lntdll -ldxgi -ld3d12 -luuid \
    -o "$temporary/amdhip64_7.dll"
"${WINEBUILD:-winebuild}" --builtin "$temporary/amdhip64_7.dll"
mv -- "$temporary/amdhip64_7.dll" "$out/x86_64-windows/amdhip64_7.dll"
mv -- "$temporary/amdhip64_7.so" "$out/x86_64-unix/amdhip64_7.so"
mv -- "$temporary/libamdnr_signal_guard.so" "$out/x86_64-unix/libamdnr_signal_guard.so"
# A retained legacy ELF builtin could be selected as a fallback and disable
# Wine's direct Windows syscall handling. Remove it only after a full build.
rm -f -- "$out/x86_64-unix/amdhip64_7.dll.so"
printf 'Built Linux HIP bridge: %s\n' "$out"
