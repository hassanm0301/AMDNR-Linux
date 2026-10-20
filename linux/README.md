# AMDNR for Linux (AMDNR-Linux)

AMDNR-Linux is a modified Linux fork of **AMDNR by 3zwr1**, implementing the ROCm bridge
requested in [upstream issue #9](https://github.com/3zwr1/AMD-NR---OptiScaler/issues/9).
The Windows lmxxf neural runtime calls a PE-format Wine builtin `amdhip64_7.dll`.
It reaches native Linux ROCm through a separate `amdhip64_7.so` and Wine's Unix-call
dispatcher. The model and neural runtime are unmodified.

**Installing a prebuilt release? Start with [INSTALL.md](INSTALL.md).** The
[Linux v0.2.1 release](https://github.com/hassanm0301/AMDNR-Linux/releases/tag/linux-v0.2.1)
includes a portable native Qt launcher, the tested bridge and patched graphics
binaries. The launcher discovers Steam libraries, finds a native shader compiler,
and handles downloads, install, repair, update, uninstall and Steam settings.
The independent UI lives in `linux/native_launcher`; it uses no upstream launcher
source. See `linux/build-native-launcher.py` to build the portable app. The build tools below
are needed only when compiling from source or running GPU tests.

The launcher builder also accepts an `.AppImage` output. From an assembled addon
release (the folder containing `RELEASE-MANIFEST.json`), run:

```sh
python3 linux/build-native-launcher.py \
  --release-root /path/to/AMDNR-Linux-v0.3.4-preview \
  --version 0.3.4-preview \
  --output linux/build/AMDNR-Linux-Manager-v0.3.4-preview-x86_64.AppImage
```

This packages the same Python, Qt, addon, notices and corresponding Qt source as
the portable archive. The AppImage tool and runtime are versioned and verified
against pinned SHA-256 checksums; the runtime is supplied explicitly so the tool
does not download an unpinned runtime. Building does not require FUSE. The existing
`.tar.gz` output remains available. Match `--version` to the assembled release;
stable builds require matching manifest and app versions.

Make the resulting AppImage executable and open it. On systems without usable
FUSE, run `./AMDNR-Linux-Manager-v0.3.4-preview-x86_64.AppImage --appimage-extract-and-run`.
Host requirements remain glibc 2.34+, the GCC 11 C++ runtime, and an X11/Wayland
desktop with graphics drivers. Settings, downloads and the persistent game Python
runtime stay in the same user data/cache directories used by portable releases.

**Status: working in tested games; experimental compatibility.** Stellar Blade
now loads the player's existing Steam save and runs neural rendering on RX 9070 XT
with GE-Proton11-9. The player confirmed normal-looking gameplay, and fresh model
and composition counters confirm inference was applied. The Witcher 3 DX12 also
passed an earlier gameplay check with Proton-Wineland 11.0 (2026-09-30).
These are short gameplay checks, not long-duration stability claims. The current
PE/Unixlib bridge is game-validated in Stellar Blade and Dragon’s Dogma 2; The Witcher 3's gameplay
check used the preceding bridge build.
The native launcher follows Steam’s current library index when duplicate game
copies exist. Dragon’s Dogma 2’s initial missing overlay was caused by patching
a stale copy; installing beside Steam’s active executable resolved it. Its
FSR 3 Quality run produced live neural model/composition frames, and the player
confirmed normal-looking gameplay with the existing REFramework installation.

The source snapshot in
`main/AMDNR-source` is 0.3.4.2; the package uses separately downloaded release
binaries, not a rebuilt host.

The development branch also contains host-source fixes for GPU resource lifetime
on resize/device removal and optional statistics allocation. These require a
separate Windows host rebuild and are **not active in the prebuilt 0.3.5.6 host**.
Their CPU/compiler checks are described in
[`main/AMDNR-source/tests/README.md`](../main/AMDNR-source/tests/README.md).

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
- Only for the optional Model E: Python 3 with numpy, and Proton or Wine to run
  Microsoft's Windows shader compiler while HelixSR's network is built.

The private runtime download contains the required ROCm libraries, approximately
186 MiB extracted. It does not install packages, run package scripts, change the
system driver, or require root. ROCm headers/compiler are build dependencies only.
danielblnc's runtime and Model E are optional (see below) and pass the automated
host pipeline test; they have no gameplay report on Linux yet. AMDNR Anywhere, frame
generation and Ray Regeneration are outside this bridge's validation coverage.

## Build and stage

Run from the repository root:

```sh
./linux/build.sh
./linux/build-vkd3d.sh
./linux/fetch-rocm.py linux/build/rocm
./linux/manage.py stage \
  --archive /path/to/AMDNR-v0.3.5.6.zip \
  --compiler /path/to/your/game/d3dcompiler_47.dll \
  --rocm linux/build/rocm \
  --output linux/build/package
```

The stage command accepts these checksum-pinned upstream archives:

| Version | SHA-256 |
| --- | --- |
| [0.3.4.2](https://github.com/3zwr1/AMD-NR---OptiScaler/releases/download/Alpha0.3.4.2/AMDNR-v0.3.4.2.zip) | `013081a3a225ef927d96d8996b64910d62f796022b20bef6a6ea102670b0cb63` |
| [0.3.5.5R4](https://github.com/3zwr1/AMD-NR---OptiScaler/releases/download/Alpha0.3.5.5R4/AMDNR-v0.3.5.5R4.zip) | `725cef9da0608621fe3233a7127e6511b9543061fcecaba8348b2c55c608d448` |
| [0.3.5.6](https://github.com/3zwr1/AMD-NR---OptiScaler/releases/download/Alpha0.3.5.6/AMDNR-v0.3.5.6.zip) | `e7ffc09696f1a4e06d26dc0c228d15a9c74a0e56e3d24be6a130a0f5e49968d8` |

`setup.py` and the manager download 0.3.5.6. Its archive has the same files as
0.3.5.5R4 and an identical `OptiScaler.ini`; the host, the lmxxf runtime, its
kernel pack and upstream's documents and notice change. The game results below were recorded with 0.3.4.2
and 0.3.5.5R4; 0.3.5.6 passes the automated GPU suite but has no game report yet.

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

## Optional components: danielblnc's runtime and OptiPatcher

AMDNR can drive two neural runtimes. **lmxxf** is built into the AMDNR archive
and is the default. **DLSS-NR on AMD by Daniel Blanco (danielblnc)** ships as
separate `vX.Y.Z-Runtime.zip` files on AMDNR's releases; AMDNR distributes it
unmodified with his permission and all rights stay with him. This fork never
republishes it: setup downloads the pinned archive from AMDNR's release only
when you ask for it, checks the archive digest, extracts exactly the three pass
DLLs, the weights and his notices, and checks every pass DLL against its pinned
digest. **OptiPatcher** (OptiScaler project) is an optional ASI plugin that
patches supported games so they offer upscaler inputs on AMD; it is fetched from
its GitHub release, verified, placed in `OptiScaler/plugins/` and enabled with
`[Plugins] LoadAsiPlugins=true`.

```sh
./linux/setup.py prepare --compiler /path/to/d3dcompiler_47.dll \
  --daniel-runtime 0.6.0 --optipatcher
./linux/manage.py stage --archive AMDNR-v0.3.5.6.zip --compiler d3dcompiler_47.dll \
  --rocm linux/build/rocm --output linux/build/package \
  --daniel-runtime v0.6.0-Runtime.zip --optipatcher OptiPatcher_v0.41.asi
```

| danielblnc | Archive SHA-256 | Pass DLL SHA-256 | GPUs offered | Hosts |
| --- | --- | --- | --- | --- |
| 0.6.0 | `d2bb1702…351f7e` | `195c4a89…775721` | RDNA 4, 3, 2 | 0.3.5.5R4, 0.3.5.6 |
| 0.5.1 | `ed4c4deb…129292` | `493b4a3b…48acd` | RDNA 4, 3 | 0.3.5.5R4, 0.3.5.6 |
| 0.5.0 | `7a49ab0e…e56ed46f` | `cddfb09e…aec31a` | RDNA 4, 3 | all three |
| 0.4.3 | `07dd7774…4b231e` | `d1e32086…a67a457` | RDNA 4, 3 | all three |
| 0.4.1 | `d6aa1ff2…e4d492` | `823063eb…b1d376` | RDNA 4, 3 | all three |
| 0.4.0 | `15ebed60…5b973` | `d62be3d8…798b80` | RDNA 4, 3 | all three |

Full digests are in `linux/runtimes.py`. The AMDNR host pins the SHA-256 of every
pass DLL it can drive, so staging reads compatibility from the host binary itself
and refuses a pairing the host would reject at run time (0.3.4.2 drives up to
0.5.0). Staging selects `[DlssNr] NrBackend=daniel` and records the components in
`amdnr-linux/components.json`. Both runtimes stay installed side by side, so the
in-game Neural tab can still switch back to lmxxf. GPU filtering uses the ROCm
KFD topology: integrated GPUs (including handheld APUs) are not offered danielblnc.

danielblnc's DLLs are HIP-compiled modules. Besides the HIP API used by lmxxf,
they need the compiler's module ABI: `__hipRegisterFatBinary`,
`__hipRegisterFunction`, `__hipRegisterVar`, `__hipUnregisterFatBinary`,
`__hipPushCallConfiguration`/`__hipPopCallConfiguration`, `hipLaunchKernel`,
`hipMemcpyToSymbol`, `hipOccupancyMaxActiveBlocksPerMultiprocessor` and
`hipDeviceGetStreamPriorityRange`. The bridge exports all of them (see
`bridge/registration.c`); every amdhip64_7.dll import of runtimes 0.4.0–0.6.0 is
covered.

## Optional upscaler: Model E (HelixSR)

AMDNR 0.3.5.6 adds **Model E**, HelixSR by lonewolf0622
([github.com/lonewolf0622/HelixSR](https://github.com/lonewolf0622/HelixSR),
HelixSR Freeware License). HelixSR runs NVIDIA's DLSS network as Direct3D 12
compute shaders, without HIP, so on Linux it runs through vkd3d-proton rather
than the bridge, with either neural runtime. Its release carries no NVIDIA
material: its own setup scripts (Apache-2.0) build the network on the user's PC
from NVIDIA's DLSS 310.7.0 DLL. This fork does the same and never ships the
result:

```sh
./linux/setup.py prepare --compiler /path/to/d3dcompiler_47.dll --model-e \
  --dlss /path/to/a/game/nvngx_dlss.dll      # NVIDIA's DLSS 310.7.0 DLL you already have
# or, to download NVIDIA's DLL from NVIDIA's GitHub under NVIDIA's licence:
./linux/setup.py prepare --compiler /path/to/d3dcompiler_47.dll --model-e --accept-nvidia-dlss-license
```

`runtimes.build_model_e` downloads the pinned HelixSR 1.8.1 release and
Microsoft's DXC 1.9.2607 (the build HelixSR's Linux setup uses), checks both and
NVIDIA's DLL against pinned SHA-256 digests, and runs HelixSR's
`setup/helixsr_setup.py` with the system's Python 3 and numpy. DXC is the Windows
build, run through Steam's Proton or the system's Wine in a throwaway prefix;
absolute paths are passed as `Z:` paths. Python settings of the packaged manager
are not passed on. The result, HelixSR's DLL with the network appended, is kept
in the download cache under `model-e/HelixSR-1.8.1/` with a build record and
reused; NVIDIA's DLL, the extracted release and the prefix are deleted.

The 0.3.5.6 host loads Model E when `[Upscalers] FfxSRProvider=helix_e` and
`[Libraries] HelixSRPath` names its folder (absolute or relative to the host DLL).
It first checks a receipt, `amdnr-helix-e.ini`, beside two copies of HelixSR's
DLL: exactly the keys `Schema=1`, `Provider=HelixSR`, `Model=E`, `MainSHA256`
(of `amd_fidelityfx_dx12.dll`) and `SR_SHA256` (of
`amd_fidelityfx_upscaler_dx12.dll`) under `[HelixSR]`, in lowercase hex. Staging
writes `AMDNR-SR-E/` with both copies, the receipt and HelixSR's notices, sets the
two keys, and refuses a host binary that does not read the receipt (0.3.5.5R4 and
older, and the pre-SR host). `manage.py stage --model-e DIR` stages a folder
built this way. If Model E cannot start, the host runs the game's FSR provider.

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
explicitly and pass the full Wine command to `run.sh`. Heroic and Lutris already
do both when `run.sh` is their wrapper or command prefix: Heroic passes
`WINEPREFIX` (or, for Proton, `STEAM_COMPAT_DATA_PATH`) and Lutris passes
`WINEPREFIX`.

At first launch the wrapper installs the PE HIP bridge and the supplied
native shader compiler into that prefix's `system32`. It backs up the old compiler
(including symlinks), journals changes in `amdnr-linux/prefixes.json`, and refuses
to overwrite an unrelated HIP DLL. The bridge is stored as `AMDHIP64_7.DLL`:
Proton-CachyOS deletes `system32/amdhip64_7.dll` on every start unless its ZLUDA
mode is on, and Wine finds the file whatever its case. A lowercase bridge left by
an earlier wrapper is removed at the next launch, Update or Repair. Native DXVK DXGI must remain enabled: Wine's
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

Uninstall keeps the logs the host wrote while the game ran, and it needs the
installation record. For a folder without a record that still holds AMDNR files,
`./linux/manage.py clean "/path/to/game/executable/directory"` lists them, asks,
and removes them in one recoverable transaction (the manager's **Remove leftover
AMDNR files**). Originals in `.amdnr-linux-backup` and in the prefix journal go
back. A file is removed only on evidence: this addon's folders, or AMDNR named
inside the host DLL, `OptiScaler.ini` or the host's logs; the patched
vkd3d-proton DLLs and the shader compiler only beside this addon's own traces.
Finished report zips, other OptiScaler builds and anything it cannot attribute
stay. A link goes with the file it points to only when it looks like a link-based
mod manager's (Amethyst, Vortex): a direct link to a regular file outside the
game folder whose path ends in the link's own relative path, which passes the
same checks. That folder's copy of a removed file that is not a link goes too,
at the same relative path and on the same checks, so the manager has nothing to
link back. Other links stay.

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
   unsupported external-fence graph capture. Additional tests cover multi-fence
   batches, duplicate-handle rewinds, rejected batches without partial effects,
   and destroying imports while their operations are still pending.
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
9. The hipcc module ABI used by danielblnc's runtime: a real offload bundle is
   registered once before `hipInit` (lazy) and once from a DLL constructor under
   the loader lock (eager); kernels launch through push/pop call configuration
   and `hipLaunchKernel`; registered constants and device variables round-trip
   through the symbol APIs; occupancy and stream priorities are queried; and the
   DLL's destructor unregisters its copy while the other keeps running.
10. With `--daniel-runtime DIR` (implies `--host-pipeline`), a third 720-frame
    host run uses `NrBackend=daniel` with that folder's pass DLLs and weights. It
    requires fresh completed jobs in `amd_presr.log` and output that differs
    materially from the NR-disabled baseline.
11. With `--optipatcher ASI`, the host runs load `OptiScaler/plugins/OptiPatcher.asi` (where
    the released host searches) and must log it.
12. With `--model-e DIR` (a Model E built by `setup.py`), three more 720-frame
    host runs use the FSR 3.1 path: plain FSR 3.1, Model E, and Model E with lmxxf
    NR. The Model E runs stage the same `AMDNR-SR-E/` layout and receipt as a game
    install, require the host to log the verified receipt and an accepted HelixSR
    context, require `network running` in `helixsr.log`, and require Model E's
    output to differ materially from FSR 3.1 on the same frames.

Without a connected monitor KWin's Xwayland reports a 0×0 screen and Wine's
Vulkan instance creation blocks. Run the suite inside a nested session, for
example `kwin_wayland --virtual --xwayland --exit-with-session "./linux/test.py …"`.
The host test also waits for the released host's GPU identification thread
before creating its own DXGI factory; the two can otherwise deadlock under DXVK.

Each run writes `results.json` with individual pass/skip/fail outcomes, settings,
Proton version, and hashes of the rendering and bridge binaries. Tests use their
own process groups; timeouts terminate that invocation's children. Shutdown is
bounded and restricted to the isolated prefix. Setup and shutdown failures must
not leave an earlier passing result behind.

For repeatable performance comparisons after a complete correctness run:

```sh
./linux/test.py --proton /path/to/GE-Proton11-9 \
  --package linux/build/package --output linux/build/benchmark \
  --frames 120 --width 1720 --height 720 \
  --full-network --history --controls --benchmark
```

The fence benchmark measures completed wait/signal round trips with one and
three semaphores per call (32 warmup, 256 measured samples). The neural benchmark
uses synchronous submission and excludes 10 warmup frames; it reports mean,
median, p95, and a final RGB checksum. Its time includes input/output GPU copies,
synchronization and readback completion, but excludes CPU pixel validation and
logging. It is **not kernel time or game FPS**, and the synthetic workload is not
a visual-quality test. Normal runs still exercise alternating synchronous and
asynchronous submission. `--neural-only` skips the other correctness tests for
repeated neural measurements; it is not a substitute for the complete suite.
Use separate output directories for runs with and without host fixtures.

The extended shared-fence test compares rewinds through the bridge with direct
D3D12 operations on an exported/reopened fence. GE-Proton11-9 supports the tested
rewind. Proton-Wineland 11.0 (2026-09-30) does not: both direct D3D12 and the bridge
retain 202 after a signal to 201. That subcheck is explicitly skipped, the other
tests continue, and the report records `passed_with_skips`. This is a limitation
of that runner's shared-fence behavior; it is not new game-validation evidence.

Use several alternating baseline/candidate runs with the same package assets,
resolution, flags, Proton version and an idle GPU. Keep quality-affecting settings
unchanged. The shipped runtime already enables its exact-output C32w, FastK and
L37/L39/L41 kernel paths by default; reducing network size or enabling fast history
changes the quality tradeoff and should be evaluated separately.

`package-release.py --validation-report /path/to/results.json` attaches a compact
automated-validation record only when every shipped binary matches that report.
It omits local paths and does not carry earlier gameplay claims onto a new build.

Recorded passing configuration: Arch Linux, RX 9070 XT, GE-Proton11-9,
private ROCm 7.2.4, AMDNR 0.3.4.2 and 0.3.5.5R4 (60 neural frames each),
and the pinned patched vkd3d-proton.
AMDNR 0.3.5.6 passed the complete suite three times in a row under
Proton-Wineland 11.0 (2026-09-30) on the same card, with the bridge unchanged
from v0.3.0-preview: in each 720-frame host run lmxxf composed 597 fresh outputs
in a 599-frame reporting window (mean absolute RGB change 0.0256 from the NR-off
baseline), danielblnc 0.6.0 completed every pass (700–717) with no timeouts, and
OptiPatcher loaded. With Model E (HelixSR 1.8.1 built here from NVIDIA's DLSS
310.7.0 DLL), the host verified the receipt and created HelixSR's context,
HelixSR's network ran on all 720 frames (19 launches per frame, wave64 network
kernels on RDNA 4), its output differed from FSR 3.1 on 16.6% of output channels
(mean absolute change 0.0013 on the synthetic scene), and Model E with lmxxf NR
passed the same composition checks as lmxxf alone.
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

HIP-compiled modules register their embedded offload bundle, kernels and
variables from static constructors. The wrapper, its bundle, kernel names and
host-stub addresses are ordinary process memory that native ROCm can read, and
the bundle's amdgcn code objects are the same for Windows and Linux, so
`registration.c` forwards the pointers after validating the wrapper magic.
`dim3` launch shapes are passed by value; each compiled half lowers its own ABI
(by reference on Windows, in registers on SysV). Unregistration during process
shutdown is skipped: Wine has already ended the other Windows threads, possibly
inside HIP, and the exiting process releases the code objects anyway.

The bridge is deliberately scoped to the runtimes' HIP API and D3D12 shared
buffers/fences. It is not a general replacement for every Windows ROCm API or
external memory/semaphore handle type.

## Upstream feature coverage

| Upstream (Windows) | Linux fork |
| --- | --- |
| lmxxf runtime | Supported and game-tested (Stellar Blade, Dragon's Dogma 2, The Witcher 3). |
| danielblnc runtimes 0.4.0–0.6.0, GPU-based choice | Supported in setup, staging and the launcher; automated host pipeline passes for every version on RX 9070 XT. No gameplay report yet. |
| OptiPatcher 0.41 / 0.40 | Supported (optional, per game); load verified in the host test, game patching depends on OptiPatcher's own game list. |
| Per-game DLL name rules, mod-loader protection | Automatic selection skips occupied names; Cyberpunk 2077 prefers `wininet.dll`, Marvel's Spider-Man `dbghelp.dll`. `dxgi`/`d3d11`/`d3d12` stay reserved for DXVK/vkd3d. |
| Unsupported-game notices, anti-cheat and conflict checks, route notes | Shown before installing; blocking cases ask for confirmation. First-install INI seeds: GTA V Enhanced `Spoofing Dxgi=false`, RE9 `Menu OverlayMenu=false`. |
| Collect logs | Save logs adds `amd_presr.log`, `dlssnr_on_amd.log/.ini`, `amdnr_crash.log` and the component list. |
| AMDNR hosts 0.3.4.2, 0.3.5.5R4 and 0.3.5.6 | Supported; 0.3.5.6 is the default. Older hosts are not pinned. 0.3.5.6 is pinned to its GitHub release asset (`e7ffc096…`, 753,230,845 bytes), which upstream's manifest now names too. Its host drives the same danielblnc runtimes as 0.3.5.5R4, and its lmxxf runtime needs no further bridge exports. |
| Model E (HelixSR by lonewolf0622), new in 0.3.5.6 | Supported as an option (see "Optional upscaler: Model E"): built on the user's PC by HelixSR's own setup scripts, after a notice in the manager; needs Python 3 with numpy and Proton or Wine. Staged as `AMDNR-SR-E/` with the receipt the 0.3.5.6 host checks. The automated host pipeline passes with it on RX 9070 XT, alone and with lmxxf. No gameplay report yet. AMDNR's own "AMD SDK" package for HelixSR is not used: it is published only on AMDNR's own server, not on GitHub, and upscaling does not need it. |
| Model L (d4r by countervolts), new in 0.3.5.6 | Not supported. d4r 0.1.4's Windows package ships its own Windows HIP 7.17 runtime, which needs AMD's Windows kernel driver, and the 0.3.5.6 host loads that copy from d4r's folder by full path. The bridge's `amdhip64_7.dll` cannot stand in: d4r's ZLUDA `nvcuda.dll` imports 99 HIP functions and the bridge exports 39 of them. The missing ones include texture and surface objects, arrays, synchronous copies and fills, occupancy queries and graph updates. d4r's own Linux build needs its patched vkd3d-proton as the game's `d3d12.dll`, which this fork already replaces with its own. |
| nvngx.dll_dlssnr.dll forwarder | Not applicable: it only serves NVIDIA's own DLSS-NR snippet. |
| AMDNR Anywhere (Magpie capture host) | Not supported: it depends on Windows window capture that Wine/DXVK do not implement. |
| TheAutomatic's "OptiScaler AMD pre-SR" 1.9.1-alpha (alternate host) | Supported as an optional host build in setup (`--host presr`), staging and the launcher; AMDNR stays the default. It drives danielblnc 0.3.1 only (it identifies danielblnc's own 0.2.17–0.3.1 builds by digest, not 0.4.0+), which is downloaded from AMDNR's Alpha0.3.3 release. Its own lmxxf backend is not offered: its weights are not published anywhere that can be pinned. Its Setup/Uninstall scripts and READMEs are not installed; licences are. The automated host pipeline passes on RX 9070 XT (danielblnc 0.3.1: 577/577 passes, no timeouts; OptiPatcher loads). One of five runs hung once at host start-up and did not reproduce. No gameplay report yet. |
| Epic/GOG/Amazon and other stores | Discovered read-only through Heroic (Epic Games via legendary, GOG, Amazon via nile, sideloaded apps) and Lutris (Wine runner), native and Flatpak. The game's directory is found as for Steam games, falling back to the store's own launch target. The manager shows the wrapper line to add in Heroic (Wrapper command) or Lutris (Command prefix) and checks it on rescan, but never edits either launcher's settings. Other non-Steam games can be added manually. |

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
