#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
out="${AMDNR_BUILD_DIR:-$root/build}"
rocm="${ROCM_PATH:-/opt/rocm}"
mkdir -p "$out"
"${MINGW_CC:-x86_64-w64-mingw32-gcc}" -std=c11 -O2 -Wall -Wextra -static \
    "$root/tests/hip_smoke.c" -o "$out/hip_smoke.exe"
"${MINGW_CC:-x86_64-w64-mingw32-gcc}" -std=c11 -O2 -Wall -Wextra -static \
    "$root/tests/exception_smoke.c" -o "$out/exception_smoke.exe"
"${MINGW_CC:-x86_64-w64-mingw32-gcc}" -std=c11 -O2 -Wall -Wextra -Werror -static \
    "$root/tests/exception_context.c" -o "$out/exception_context.exe"
"${MINGW_CC:-x86_64-w64-mingw32-gcc}" -std=c11 -O2 -Wall -Wextra -Werror -static -municode \
    "$root/tests/syscall_dispatch.c" -o "$out/syscall_dispatch.exe"
"${MINGW_CXX:-x86_64-w64-mingw32-g++}" -std=c++17 -O2 -Wall -Wextra -static -I"$rocm/include" \
    "$root/tests/d3d12_interop.cpp" -ld3d12 -ldxgi -ldxguid -o "$out/d3d12_interop.exe"
"${MINGW_CXX:-x86_64-w64-mingw32-g++}" -std=c++17 -O2 -Wall -Wextra -static -municode \
    "$root/tests/neural_smoke.cpp" -ld3d12 -ldxgi -ldxguid -o "$out/neural_smoke.exe"
"${MINGW_CXX:-x86_64-w64-mingw32-g++}" -std=c++17 -O2 -Wall -Wextra -Werror -static -municode \
    "$root/tests/host_neural.cpp" -ld3d12 -ldxgi -ldxguid -o "$out/host_neural.exe"
"${MINGW_CXX:-x86_64-w64-mingw32-g++}" -std=c++17 -O2 -Wall -Wextra -static \
    "$root/tests/host_shaders.cpp" -ld3dcompiler -ld3d12 -ldxguid -o "$out/host_shaders.exe"
"$rocm/bin/hipcc" --genco --offload-arch=gfx1201 "$root/tests/compute.hip" -o "$out/compute.hsaco"
"${MINGW_CC:-x86_64-w64-mingw32-gcc}" -std=c11 -O2 -Wall -Wextra -Werror -static \
    "$root/tests/hip_registration.c" -o "$out/hip_registration.exe"
"${MINGW_CC:-x86_64-w64-mingw32-gcc}" -std=c11 -O2 -Wall -Wextra -Werror -shared -static-libgcc \
    "$root/tests/hip_registration_module.c" -o "$out/hip_registration_module.dll"
# hipcc --genco emits a clang offload bundle: the same payload a HIP module embeds.
"$rocm/bin/hipcc" --genco --offload-arch=gfx1201 "$root/tests/registered.hip" -o "$out/registered.hipfb"
