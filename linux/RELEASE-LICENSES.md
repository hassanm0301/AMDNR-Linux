# Linux release: licenses, credits and source

This is a modified Linux compatibility add-on for **AMDNR by 3zwr1 —
https://github.com/3zwr1/AMD-NR---OptiScaler**. It is published by the
[Linux fork](https://github.com/hassanm0301/AMDNR-Linux), not as an
official upstream AMDNR release. The OptiScaler contributors and all upstream
acknowledgments retain their credit. No endorsement by AMD, NVIDIA, Microsoft,
Valve, Wine or the other upstream projects is implied.

The downloadable add-on contains the Linux bridge and patched graphics
libraries. Setup obtains the separate upstream runtime and ROCm dependencies
from their original distributors. The user supplies Microsoft's shader
compiler from their own installation. A locally assembled game installation
therefore contains files that are not included in this fork's release assets.

## Files published in this release

| Component | License and source |
| --- | --- |
| `amdhip64_7.dll`, `amdhip64_7.so`, `libamdnr_signal_guard.so`, Linux setup/build scripts | GPL-3.0-or-later for this fork's new code. The matching bridge source, build scripts and GPL text accompany the add-on. See individual source notices. |
| Patched `d3d12.dll` and `d3d12core.dll` | vkd3d-proton, LGPL-2.1-or-later. Copyright 2016–2024 the vkd3d-proton project authors; see its `AUTHORS`, `COPYING` and full `LICENSE`. The Linux shared-buffer patch is identified separately and uses the same license. |
| Compiler/runtime support embedded in these binaries | The Wine, MinGW-w64 and GCC terms described below, plus the retained dependency notices. These components are not relicensed as work authored by this fork. |

The graphics binaries are based on vkd3d-proton commit
`0bb64c56c8f0dcc11ecc0f6c56e00e4f7bd8d506`, with
`linux/patches/vkd3d-proton-shared-buffers.patch` applied. The corresponding
source archive on the same release page includes the patched working files,
the exact recursive submodule revisions, their notices and build instructions.
The source archive must remain available alongside the binaries. A patch alone
or a link to the latest upstream branch is not the matching source distribution.
See [GPLv3 section 6](https://www.gnu.org/licenses/gpl.en.html#section6) and
[LGPLv2.1 section 4](https://www.gnu.org/licenses/old-licenses/lgpl-2.1.en.html).

The graphics dependency notices include dxil-spirv by Hans-Kristian Arntzen for
Valve Corporation, dxbc-spirv by Philip Rebohle, SPIRV-Cross, SPIRV-Tools,
Vulkan-Headers and SPIRV-Headers. Preserve their complete `LICENSE`, `NOTICE`
and `LICENSES` files where present, including the third-party notices within
dxil-spirv's `LICENSE.MIT`. The source archive preserves the original directory
structure and copyright notices.

## Separately obtained components

| Component | How it is supplied |
| --- | --- |
| AMDNR host, `LmxxfNrRuntime.dll`, `LmxxfNrRuntime.pak` and the upstream `OptiScaler` support directory | Downloaded unchanged from the pinned upstream AMDNR release. These binaries are not republished in this add-on. Setup retains the upstream license directory and notices. |
| Microsoft `d3dcompiler_47.dll` | Copied locally from a path supplied by the user. It is not in the release archive and is not covered by this fork's GPL license. |
| Private ROCm 7.2.4 runtime and Ubuntu support libraries | Downloaded from the AMD and Ubuntu package URLs pinned in `linux/fetch-rocm.py`; package SHA-256 hashes are checked. The extracted local runtime retains the accompanying copyright/license files and package provenance. These packages are not mirrored in the add-on. |
| Proton/Wine, DXVK, the Vulkan driver and Steam | Supplied by the user's system or chosen Proton installation. They are not distributed by this add-on. |
| danielblnc's runtime (`dlssnr_amd_pass1..3.dll`, `dlssnr_on_amd_weights.bin`), optional | DLSS-NR on AMD by Daniel Blanco (danielblnc), https://github.com/danielblnc/DLSS-NR-on-AMD, all rights reserved. AMDNR ships it unmodified with his permission in its `vX.Y.Z-Runtime.zip` assets. Setup downloads the pinned archive from AMDNR's release only when the user chooses it and keeps his `danielblnc_ATTRIBUTION.txt` (and `danielblnc_LICENSE.txt`) beside the files when the archive ships them; the older 0.3.1 archive ships neither, so `SOURCE.txt` names his project and terms instead. This fork does not republish it. The weights file derives from NVIDIA's DLSS-NR; NVIDIA's rights apply. |
| OptiPatcher plugin, optional | From the OptiScaler project's OptiPatcher GitHub releases, downloaded unchanged when chosen and checked against a pinned digest. Not republished by this fork. |
| "OptiScaler AMD pre-SR" 1.9.1-alpha host, optional | An independent GPL-3.0 OptiScaler fork by TheAutomatic, https://github.com/TheAutomatic/dlss-5-amd-project. Setup downloads `OptiScaler-AMD-PreSR-1.9.1-alpha.zip` (SHA-256 `bc0799ee408f50a0766f250118022a6c030f46a3d2bf75e9715266ad2a3c247a`) unchanged from that project's release only when the user chooses it, and keeps its `Licenses/` directory. Its corresponding source is in that repository at tag `v1.9.1-alpha`. Not republished by this fork. |

The pinned AMDNR archive is `AMDNR-v0.3.5.6.zip`, SHA-256
`e7ffc09696f1a4e06d26dc0c228d15a9c74a0e56e3d24be6a130a0f5e49968d8`.
Its `Licenses/AMDNR_NOTICE.txt` identifies the host as GPL-3.0 with additional
attribution terms, and says that source for that release will be published with
AMDNR 0.5.0. The older host source in this fork is not represented as the
corresponding source for the downloaded 0.3.5.6 binaries.

The upstream notice credits **lmxxf / Kien** for the MIT-licensed port, kernels
and runtime contributions. It separately identifies **NVIDIA** as the owner
of the neural-network design and weights; the MIT license for porting code
must not be presented as a license for those weights. **Daniel Blanco
(danielblnc)** owns the separate closed runtime covered by
`danielblnc_ATTRIBUTION.txt`; this add-on does not distribute that runtime.

Preserve `AMDNR_NOTICE.txt`, `AMDNR_RDNA3_BACKEND.txt`, the upstream copyright
lines, the visible AMDNR menu credit and the credits in the runtime/model
files. Keep the AMDNR attribution above in documentation and any credits or
About presentation. Mark Linux modifications as modifications; do not claim
authorship of the upstream menu or use its names/logos as a new brand.

The downloaded ROCm components retain these terms: HIP and rocprofiler-register
use MIT; ROCr uses the University of Illinois/NCSA license; COMGR uses Apache
2.0 with LLVM exceptions and additional notices. The downloaded `libnuma`
uses LGPL-2.1 and `libelf` offers LGPL-3.0-or-later/GPL alternatives. Someone
republishing an assembled installation must separately satisfy those
components' applicable source and notice requirements. Download hashes and
short Debian copyright summaries do not replace full license texts or
corresponding source where required.

## Embedded build support

The tested PE bridge was linked with unmodified Wine 11.19 `winecrt0` helpers:

- `crt_dllmain.c`: Copyright 2019 Jacek Caban for CodeWeavers.
- `gcc_ctors.c`: Copyright 2026 Yuxuan Shui for CodeWeavers.
- `unix_lib.c`: Copyright 2019 Alexandre Julliard.

Each of these files carries LGPL-2.1-or-later plus an explicit additional
permission for linking its compiled code into other programs and distributing
the result. The exception does not remove the terms for modifying or
distributing the helper files themselves. The original notices are available
in the [Wine 11.19 helper sources](https://github.com/wine-mirror/wine/tree/wine-11.19/libs/winecrt0).
Preserve their attribution and linking-exception notice with the binary
release. Wine's system DLLs are imported, rather than copied into the add-on.

The graphics binaries also contain MinGW-w64 runtime support. Include the
complete `COPYING.MinGW-w64-runtime.txt` from the toolchain with the binary
release; its notices apply to embedded runtime portions. The tested toolchain
used MinGW-w64 CRT 14.0.0 and GCC 16.2.0. GCC runtime portions retain GPLv3 with
the [GCC Runtime Library Exception 3.1](https://www.gnu.org/licenses/gcc-exception-3.1.en.html);
include that exception and the GPL text. This does not replace licenses for
the program or other dependencies.

The implementation also acknowledges
[d4r by countervolts](https://github.com/countervolts/d4r) for the reference
approach to a Wine HIP bridge and a private ROCm runtime. Existing upstream
notices and the individual source-file licenses remain authoritative.
