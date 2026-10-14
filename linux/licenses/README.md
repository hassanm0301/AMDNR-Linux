# License text provenance

These notices accompany the prebuilt Linux bridge and graphics libraries.
Component-specific source notices continue to apply.

- `GPL-3.0.txt`: verbatim repository `LICENSE` (GNU GPL version 3).
- `LGPL-2.1.txt`: verbatim vkd3d-proton `LICENSE` at commit
  `0bb64c56c8f0dcc11ecc0f6c56e00e4f7bd8d506` (GNU LGPL version 2.1).
- `COPYING.MinGW-w64-runtime.txt`: verbatim notice from the installed
  MinGW-w64 CRT 14.0.0 toolchain used to build the release.
- `GCC-RUNTIME-EXCEPTION.txt`: verbatim
  [GCC 16.2.0 COPYING.RUNTIME](https://github.com/gcc-mirror/gcc/blob/releases/gcc-16.2.0/COPYING.RUNTIME).
- `Wine-winecrt0-NOTICES.txt`: verbatim copyright/license/exception headers
  from `crt_dllmain.c`, `gcc_ctors.c` and `unix_lib.c` in the official
  [Wine 11.19 source](https://github.com/wine-mirror/wine/tree/wine-11.19/libs/winecrt0).
  Each preserved header is preceded by its exact source URL.

The release also retains vkd3d-proton's `COPYING`, `AUTHORS`, full `LICENSE`
and dependency notices. Its complete corresponding-source archive contains
the patched sources and recursive submodules. See `linux/RELEASE-LICENSES.md`
for the separately downloaded upstream runtime, model and ROCm components.
