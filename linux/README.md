# Neural rendering on Linux

This is a modified Linux fork of **AMDNR by 3zwr1**, implementing the ROCm bridge
requested in [upstream issue #9](https://github.com/3zwr1/AMD-NR---OptiScaler/issues/9).
The Windows lmxxf neural runtime calls a PE-format Wine builtin `amdhip64_7.dll`.
It reaches native Linux ROCm through a separate `amdhip64_7.so` and Wine's Unix-call
dispatcher. The model and neural runtime are unmodified.

**Installing a prebuilt release? Start with [INSTALL.md](INSTALL.md).** The
[Linux v0.2.0 release](https://github.com/hassanm0301/AMD-NR---OptiScaler-rocm/releases/tag/linux-v0.2.0)
includes a portable native Qt launcher, the tested bridge and patched graphics
binaries. The launcher discovers Steam libraries, finds a native shader compiler,
and handles downloads, install, repair, update, uninstall and Steam settings.
The independent UI lives in `linux/native_launcher`; it uses no upstream launcher
source. See `linux/build-native-launcher.py` to build the portable app. The build tools below
are needed only when compiling from source or running GPU tests.

**Status: working in tested games; experimental compatibility.** Stellar Blade
now loads the player's existing Steam save and runs neural rendering on RX 9070 XT
with GE-Proton11-9. The player confirmed normal-looking gameplay, and fresh model
and composition counters confirm inference was applied. The Witcher 3 DX12 also
passed an earlier gameplay check with Proton-Wineland 11.0 (2026-09-30).
These are short gameplay checks, not long-duration stability claims. The current
PE/Unixlib bridge is game-validated in Stellar Blade; The Witcher 3's gameplay
check used the preceding bridge build.
The source snapshot in
`main/AMDNR-source` is 0.3.4.2; the package uses separately downloaded release
binaries, not a rebuilt host.

## Requirements

- x86-64 Linux, an AMD GPU supported by the supplied neural model, and access to
  `/dev/kfd` and its render node. RX 9070 XT (`gfx1201`) is the tested GPU.
- Wine 11 / Proton based on Wine 11. GE-Proton11-9 and Proton-Wineland 11.0
  (2026-09-30) pass the automated tests and the respective game checks above.
  Shared-memory import currently depends on Wine 11's Vulkan object layout;
  other Wine versions are not assumed compatible.
- A working Vulkan driver and a D3D12 game already working with your chosen Proton.
- Python 3.12 or newer, Git, binutils (`ar`), Meson, Ninja, MinGW-w64, Wine
  development tools (`winegcc`, `winebuild`), Vulkan headers, and ROCm 7 headers.
  Building the GPU test kernel additionally requires `hipcc`.
- An unmodified AMDNR release archive and a native Microsoft `d3dcompiler_47.dll`
  from your own game installation. The tested compiler is version 10.0.14393.795.
  Wine's builtin compiler cannot compile all of the host's temporal shader.

The private runtime download contains the required ROCm libraries, approximately
186 MiB extracted. It does not install packages, run package scripts, change the
system driver, or require root. ROCm headers/compiler are build dependencies only.
The danielblnc runtime, AMDNR Anywhere, frame generation, and Ray Regeneration
are outside this bridge's current validation coverage.

## Build and stage

Run from the repository root:

```sh
./linux/build.sh
./linux/build-vkd3d.sh
./linux/fetch-rocm.py linux/build/rocm
./linux/manage.py stage \
  --archive /path/to/AMDNR-v0.3.5.5R4.zip \
  --compiler /path/to/your/game/d3dcompiler_47.dll \
  --rocm linux/build/rocm \
  --output linux/build/package
```

The stage command accepts these checksum-pinned upstream archives:

| Version | SHA-256 |
| --- | --- |
| [0.3.4.2](https://github.com/3zwr1/AMD-NR---OptiScaler/releases/download/Alpha0.3.4.2/AMDNR-v0.3.4.2.zip) | `013081a3a225ef927d96d8996b64910d62f796022b20bef6a6ea102670b0cb63` |
| [0.3.5.5R4](https://github.com/3zwr1/AMD-NR---OptiScaler/releases/download/Alpha0.3.5.5R4/AMDNR-v0.3.5.5R4.zip) | `725cef9da0608621fe3233a7127e6511b9543061fcecaba8348b2c55c608d448` |

It keeps each release's host, runtime, and model together, enables lmxxf neural
rendering in `OptiScaler.ini`, and stages the bridge, patched graphics DLLs,
compiler, licenses, and bridge source. It refuses an existing output directory.
No game or prefix is changed by staging.

`ROCM_PATH` defaults to `/opt/rocm`. `AMDNR_BUILD_DIR`, `WINEGCC`, `WINEBUILD`,
`MINGW_CC`, and `MINGW_CXX` can override build locations/tools. The graphics
script pins vkd3d-proton commit `0bb64c56c8f0dcc11ecc0f6c56e00e4f7bd8d506` and
uses an isolated checkout at `linux/deps/vkd3d-proton`; `AMDNR_VKD3D_SOURCE` can
point to another checkout at that exact revision. `AMDNR_BUILD_JOBS` defaults to 8.
Omit `--rocm` only when the game can access a compatible system ROCm 7 installation.

## Install in a Steam game

Close the game. Install beside the actual game executable (for Unreal games this
is commonly `Game/Binaries/Win64`, not the launcher directory):

```sh
./linux/manage.py install linux/build/package "/path/to/game/executable/directory"
```

Keep the Proton version that already works for that game. Put the printed
launcher path in Steam's launch options, preserving your existing wrappers:

```text
MANGOHUD=1 gamemoderun "/path/to/game/executable/directory/amdnr-linux/run.sh" %command%
```

**Launch from Steam.** The launcher uses Steam's existing
`STEAM_COMPAT_DATA_PATH/pfx`, preserving the game's Steam identity and save paths.
It does not create a replacement game prefix. Running the executable directly
can omit Steam's account context and make existing saves appear absent even when
they are still on disk. For non-Steam Wine use, set the intended `WINEPREFIX`
explicitly and pass the full Wine command to `run.sh`.

At first launch the wrapper installs the PE HIP bridge and the supplied
native shader compiler into that prefix's `system32`. It backs up the old compiler
(including symlinks), journals changes in `amdnr-linux/prefixes.json`, and refuses
to overwrite an unrelated HIP DLL. Native DXVK DXGI must remain enabled: Wine's
builtin DXGI is incompatible with the vkd3d-proton device used here.

Enable the game's supported upscaler so OptiScaler receives its frames. The
package selects the lmxxf backend. Check `lmxxf_backend.log` for `hip=1`, the
correct GPU, and increasing fresh model-frame counts. These prove inference ran;
also load a save, inspect the image, move around, and exercise menu/load/resolution
transitions before calling a game compatible.

## Restore the previous installation

Close the game and remove the wrapper from Steam's launch options. Then:

```sh
./linux/manage.py uninstall "/path/to/game/executable/directory"
```

The installer journals files and backs up replacements in
`.amdnr-linux-backup`. Uninstall restores the original game files and compiler,
removes only files it installed, and preserves unrelated saves and logs. If an
installed file changed, uninstall stops and identifies it. Save that change first;
`--force` explicitly discards modifications to managed files in favor of the
original backup. Restore before installing a newer package. Do not manually
delete the backup or prefix journal while the package is installed.

## Validation

CPU/package checks:

```sh
python3 -m unittest discover -s linux/tests -v
```

Real GPU checks (use an idle GPU):

```sh
./linux/build-tests.sh
./linux/test.py --proton /path/to/GE-Proton11-9 \
  --package linux/build/package --frames 60 --with-host --host-pipeline
```

Only this test suite creates an isolated prefix. It never substitutes that prefix
for a game. Logs go to `linux/build/test-run/` (override with `--output`). The
suite checks exit status and an explicit success marker for every executable:

1. Windows HIP API calls, matching adapter identity, GPU allocations/copies, a
   compiled HIP kernel, and all 4,096 computed results.
2. A DXGI swapchain, shared D3D12 GPU buffers, 256 D3D12/HIP fence round trips,
   and 64 batches of three buffers transferring changing data in both directions.
   Checks import recreation, unequal timeline values, invalid arguments, and
   unsupported external-fence graph capture.
3. The actual host temporal shader compiled using its absolute System32 path.
4. Windows divide-by-zero, access-violation, INT3 and UD2 handling before, during, and after
   HIP initialization, module loading, GPU computation, and cleanup. A background
   thread continuously checks general registers, all XMM registers, MXCSR, and
   the x87 control word while HIP works on another thread.
5. The real lmxxf model, comparing neural frames against a codec-only reference.
   All output values must be finite and a meaningful number must differ. Jobs
   alternate between synchronous and asynchronous submission before final drain
   and destruction.
6. With `--with-host`, a second neural run loads the released OptiScaler host
   with its graphics hooks and GPU spoofing enabled. It requires fresh evidence
   that the host captured the D3D12 device. This checks the bridge under those
   hooks; a game test is still needed for host input/resource selection.
7. With `--host-pipeline`, two 720-frame runs feed synthetic colour, depth, and
   motion through the released host's NGX/FSR2 pipeline, first with NR disabled,
   then enabled. The test requires finite output, a material difference from the
   baseline, and fresh logs proving neural output reached composition. It loads
   the runtime dependency before graphics initialization; all rendering calls go
   through the host's NGX exports, without direct neural API calls.
8. A CPU-only direct Windows system-call regression checks the caller, an
   existing thread and a new thread before/after loading the bridge. It requires
   an exact recognized Wine/Linux stub and explicitly skips unsupported layouts;
   a skip is never reported as a pass. The old ELF bridge fails this comparison.

Recorded passing configuration: Arch Linux, RX 9070 XT, GE-Proton11-9,
private ROCm 7.2.4, AMDNR 0.3.4.2 and 0.3.5.5R4 (60 neural frames each),
and the pinned patched vkd3d-proton.
The 0.3.5.5R4 runtime also passes 120 frames at 1720x720 with the full network,
temporal history, and runtime controls enabled. The concurrent exception test
reproduces a failure without the signal guard and passes with it.
The bridge that reuses existing graphics devices passes those 120 frames both
directly and under the released host's graphics hooks and forced GPU spoofing;
the host captures the same D3D12 device throughout the imports. The same suite
passes 60 frames at that size with Proton-Wineland 11.0 (2026-09-30).
The full NGX/FSR2 host test also passes under GE-Proton11-9 inside Steam's Linux
runtime: 720 frames per mode, 599 model frames and 597 fresh composed outputs
in the neural reporting window, and a mean absolute RGB difference of 0.02208
from the NR-disabled baseline.
The same core inference path also passed shorter runs on system Wine 11.19 and
Proton 11.

Game validation on 2026-10-15: The Witcher 3 DX12, AMDNR 0.3.5.5R4, private ROCm
7.2.4, RX 9070 XT, and Proton-Wineland 11.0 (2026-09-30), using the original Steam
prefix. The player loaded an existing save and confirmed normal-looking gameplay.
The session reached 1,454 frames; two logged statistics windows confirm 1,199
model frames and 1,197 fresh neural outputs applied, with temporal history and
all 71 network blocks active. The input was 2293x960, fed to the network at
1920x804. Mean HIP kernel time was 10.84–10.89 ms; this is not total frame time.
Shutdown was normal. Conflicting game mods were removed before this test.

Stellar Blade validation on 2026-10-15: GE-Proton11-9, the PE/Unixlib bridge,
AMDNR 0.3.5.5R4, private ROCm 7.2.4, and the same RX 9070 XT. The player loaded
their existing save and confirmed normal-looking gameplay. Six reporting windows
contained 3,599 model frames and 3,594 fresh composed outputs, all 71 blocks and
temporal history active, at 1720x720 input. HIP kernel means were 11.18–12.63 ms
(not total frame time). This is a short-session result, not a claim about every
scene or long-duration stability. Neither game result establishes compatibility
with other games.

Controlled Stellar Blade comparisons isolated the previous startup crash:
NR enabled with native HIP forced unavailable still crashed; blocking the HIP DLL
entirely allowed gameplay; a minimal ELF DLL returning error 801 reproduced the
same audio-thread crash; an equivalent native PE DLL allowed gameplay. A separate
direct-syscall regression reproduced the lost Windows dispatch after ELF loading
and preserved it with the real PE/Unixlib bridge. The new bridge also passed the
GPU/interoperability suite and the 720-frame NR-off/on host pipeline comparison.

## How it works

`bridge/generate.py` produces typed Windows exports, shared argument layouts,
and matching Unix-call table entries. The real PE DLL performs all Win32, COM,
and Wine Vulkan operations. Only the native helper loads ROCm and invokes its
Linux ABI. Missing functions return errors; there is no fake-success fallback.
The PE module is pinned on first use because fence workers and optional exception
diagnostics can outlive a caller's DLL reference. Native HIP is never unloaded.

The PE/Unixlib split is required for game compatibility. GE-Proton11-9 disables
Windows direct-syscall dispatch process-wide when it loads an old-style Winelib
`.dll.so`. In Stellar Blade, even a minimal ELF DLL returning an unavailable
error caused the same audio-thread crash; an equivalent PE DLL allowed gameplay.
Neither control ran neural inference. The launcher rejects leftover
`amdhip64_7.dll.so` files to prevent the legacy loader path being selected.

`identity.c` matches HIP and Wine Vulkan adapters by PCI address (UUID fallback),
returning the LUID expected by the Windows neural runtime.
It borrows DXVK's existing adapter handles and matches physical PCI/UUID
identifiers, which remain valid when the host spoofs GPU names and vendor IDs.

`signal_guard.c` preserves Wine's signal handlers during native COMGR compiler
work. COMGR temporarily installs LLVM's process-wide diagnostic handlers; LLVM
re-sends a fault without its original CPU context, which breaks Windows exceptions
on concurrent game threads. The native preload only suppresses COMGR replacing
an existing Wine handler. Other callers, queries, and restoration pass through.
This disables LLVM-specific crash reporting for those signals; faults still reach
Wine's normal exception handling. See the pinned
[LLVM signal dispatch](https://github.com/ROCm/llvm-project/blob/rocm-7.2.4/llvm/lib/Support/Unix/Signals.inc#L377-L435)
and [COMGR save/restore](https://github.com/ROCm/llvm-project/blob/rocm-7.2.4/amd/comgr/src/comgr.cpp#L1219-L1233).

Shared resources need more than function forwarding. The vkd3d-proton patch makes
shared D3D12 buffers exportable, dedicated Vulkan allocations. `vulkan_memory_pe.c`
borrows the existing vkd3d-proton device through `ID3D12DeviceExt`, imports Wine's
shared handle, validates Wine's private object headers, and obtains a Linux fd
for ROCm. The fd is closed after ROCm duplicates it. The bridge creates no private
Vulkan instance or device that the host's hooks could mistake for the game's.

D3D12 fences use virtual timeline values; passing a raw Vulkan timeline fd to HIP
would synchronize the wrong values. `fence_pe.c` and `fence_unix.c` keep the D3D12
fence semantics on Wine worker threads and order them with native HIP stream callbacks. The native
callbacks use only pthread synchronization, never Win32 APIs. Pending jobs hold
fence references; stream destruction drains and joins the worker. Neural graph
capture remains supported, but capturing external fence waits/signals is rejected.

The bridge is deliberately scoped to the runtime's HIP API and D3D12 shared
buffers/fences. It is not a general replacement for every Windows ROCm API or
external memory/semaphore handle type.

## Producing release assets

The prebuilt add-on is assembled from an already tested package. Commit all
source and documentation changes first, then run:

```sh
python3 linux/package-release.py --version 0.1.0 \
  --package linux/build/package \
  --vkd3d-source linux/deps/vkd3d-proton \
  --output linux/build/release-v0.1.0
```

The vkd3d checkout must be at the pinned revision with exactly the shared-buffer
patch applied and matching initialized submodules. The packager checks those
conditions, copies the five tested binaries without rebuilding them, records
their hashes and the fork commit, and creates a separate complete corresponding
graphics-source archive. It includes dependency notices with the binaries and
excludes downloaded upstream assets, shader compilers, ROCm binaries, prefixes,
game files, and logs. Publish both archives, `INSTALL.md`, and `SHA256SUMS` together.
See [RELEASE-LICENSES.md](RELEASE-LICENSES.md) for distribution details.

## Troubleshooting

- **No neural frames / missing HIP:** check the full Steam wrapper path, ROCm
  libraries, `/dev/kfd` access, and that the game uses the intended prefix.
  Add `PROTON_LOG=1` before the launch option and inspect `amdnr-hip:` messages in
  the Proton log. `AMDNR_HIP_TRACE=1` adds per-function tracing; remove it for
  performance measurements.
- **Unsupported Wine Vulkan object ABI:** use the tested Wine 11 runner. The
  bridge fails the import rather than dereferencing an unfamiliar object layout.
- **Temporal shader compilation failed:** use a compatible native compiler. The
  host loads it from System32; putting it only next to the executable is insufficient.
- **Swapchain crash:** retain `dxgi=n` and both patched graphics DLLs. The wrapper
  sets these overrides without removing unrelated user overrides.
- **Fatal game error:** preserve the game crash report and `OptiScaler.log` /
  `lmxxf_backend.log`. A successful HIP probe or menu is insufficient evidence of
  stable gameplay. Compare the same Steam prefix and runner with NR disabled.

## Credits and licensing

- **AMDNR by 3zwr1**, the OptiScaler contributors, and their existing acknowledgments
  remain unchanged. This fork is modified and is not an upstream release.
- **lmxxf / Kien** supplies the neural runtime/model components under their
  accompanying terms. **Daniel Blanco (danielblnc)** owns his separate runtime;
  this Linux package does not validate or claim authorship of it.
- [d4r by countervolts](https://github.com/countervolts/d4r) provided the reference
  approach for a Wine HIP bridge and a small private ROCm runtime.
- Wine, vkd3d-proton, DXVK, AMD ROCm, and Ubuntu dependency packages retain their
  own licenses. The new bridge uses GPL-3.0-or-later; the vkd3d-proton patch uses
  LGPL-2.1-or-later. See the root `LICENSE` and the staged license files.
- Runtime/model binaries and Microsoft's shader compiler are supplied separately
  and are not checked into this fork. Retain their redistribution terms when
  sharing a package; the build scripts and bridge source are included with it.
