# Linux v0.3.5-preview — GPU recognition, a neural-rendering style, RE9 start-up

Development **pre-release**. The HIP bridge now reports the GPU's PCI revision, so
the AMDNR host recognises RX 9000 cards on Linux. Fresh installs come with a
measured neural-rendering style in the overlay's first custom style slot.
Resident Evil Requiem no longer freezes at start-up with REFramework, and the
manager cleans up leftovers a mod manager has linked. The AMDNR host stays at
0.3.5.6 and every AMDNR default is as upstream ships it.

## New

- **A neural-rendering style in custom slot 1.** In the AMDNR overlay's Neural tab,
  open **Style slots** beside NR style and press **Apply** on slot 1. It sets a
  residual limit of 0.15 and edge guard 1 (`StyleSlot1=limit=0.1500;edgeguard=1.0000;`
  under `[DlssNr]`) and nothing else; **Clear** and **Store** work on it as on any
  slot, and picking a named style afterwards overrides both values. For the
  complete set that was measured, also choose **AMDNR soft** as the NR style and
  turn off **Auto-exposure highlight cap**. Against the defaults, with lmxxf:
  - Black Myth: Wukong benchmark: edge overshoot 0.54 → 0.39 (0.27 with NR off),
    frame-to-frame flicker 3.83 → 3.34 (2.72 with NR off), 2 fps faster.
  - Resident Evil Requiem: the default look's colour drift is gone (yellow police
    tape had turned olive); overshoot 0.52 → 0.25, shimmer while panning
    1.07 → 0.82 (0.68 with NR off). The slot's residual limit alone did most of it
    there (0.19, 0.89).

  The relight stays, a little milder. Fresh installs get the slot; an existing
  installation keeps its `OptiScaler.ini`, so add the line above to its
  `[DlssNr]` section.
- **The host recognises the GPU.** Under Proton, DXVK reports PCI revision 0, so the
  host's device table missed the RX 9070 XT ("GPU not in the device table") and
  refused what it keeps for RX 9070-class GPUs, such as
  `AmdLmxxfTierCap=native`. The bridge now fills in the real revision from
  sysfs. The picture at default settings is unchanged.
- **Steam launch option button.** For an installed game, a button beside Launch
  in Steam shows the launch option line to paste, with a Copy button.
- **Leftovers linked by a mod manager are cleaned up.** Remove leftover AMDNR
  files now follows a mod manager's links (Amethyst's `overwrite` folder, for
  example) and removes the linked AMDNR file with its link, and also removes the
  manager's unlinked copies of leftover AMDNR files, in one pass. Another
  OptiScaler build is never removed.
- `manage.py stage` accepts the AMDNR 0.3.5.8 archive. The manager still installs
  0.3.5.6.
- `linux/quality` runs the Black Myth: Wukong Benchmark Tool unattended in a nested
  KWin and compares neural-rendering settings frame by frame (see its README).

## Fixed

- **Resident Evil Requiem froze at start-up with REFramework.** REFramework
  suspends the game's threads for its integrity check while the AMDNR host lists
  the PC's adapters on a thread of its own, and the game stopped at a black
  window. A fresh RE9 install now sets `[DlssNr] AmdAdapterList=false`, which only
  leaves that list out of `amd_bridge.log`. For an existing RE9 installation, add
  that line under `[DlssNr]` in its `OptiScaler.ini`.

## Validation

The complete GPU suite passed three times in a row on RX 9070 XT under
Proton-Wineland 11.0 (2026-09-30) with the 0.3.5.6 package and this release's
bridge. In every run:
- the host identified the GPU as gfx1201 ("danielblnc yes, lmxxf yes: RDNA 4").
  The new native-size runs at 2646x1108 → 3440x1440 with `AmdLmxxfTierCap=native`
  had lmxxf run its 3440x1440 network.
- the 720-frame NR-off/on host pipeline with lmxxf composed 597 fresh outputs in a
  599-frame reporting window.
- danielblnc 0.6.0 completed 716/716, 700/700 and 700/700 passes, with no
  timeouts.
- OptiPatcher 0.41 loaded in every host run.
- the host verified the Model E receipt and HelixSR's network ran; its output
  differed from FSR 3.1 on 16.6% of output channels. Model E was rebuilt from
  HelixSR 1.8.1 and a local DLSS 310.7.0 to the same DLL as before (SHA-256
  `11ed21a5…`).

The only skip is Wineland's known shared-fence rewind limitation. All 355 Linux
unit tests pass.

The style slot's numbers come from linux/quality runs of Black Myth: Wukong's
benchmark and from Resident Evil Requiem, both at 3440x1440 on the same card;
the full write-ups are not part of the release. The RE9 start-up fix was
checked twice with REFramework, in a nested KWin session. These are synthetic
API and pipeline checks plus two image-quality comparisons; no new game
compatibility or long-duration stability is claimed.

# Linux v0.3.4-preview — Proton-CachyOS and Unreal launcher fixes

Development **pre-release**. Neural rendering now starts under Proton-CachyOS,
and Add game installs AMDNR beside the executable an Unreal game really runs.
The game wrapper and the manager change; the AMDNR 0.3.5.6 host, Model E
support, the Linux bridge and the patched graphics binaries are the same as in
v0.3.3-preview. Both problems were found with BITS (Unreal Engine 4.27), a
non-Steam game started through umu-run with Proton-CachyOS.

## Fixed

- **Neural rendering never started under Proton-CachyOS.** Proton-CachyOS
  (seen in `cachyos-11.0-20261014`) deletes `system32/amdhip64_7.dll` from the
  game's prefix on every start unless its ZLUDA mode is on. That is where the
  AMDNR wrapper put the HIP bridge just before the game started, so the host
  could never load HIP: the AMDNR menu opened and the neural rendering toggle
  switched, but the picture never changed, and `amd_bridge.log` said
  `amdhip64_7.dll could not be loaded (Win32 error 126)`. Steam and umu-run
  launches were both affected. GE-Proton, Proton-Wineland, UMU-Proton and
  Valve's Proton do not delete the file, so the old name kept working with
  them.
- The wrapper now stores the bridge as `AMDHIP64_7.DLL`. Proton-CachyOS checks
  only the lowercase name, and Wine finds the file whatever its case, so games
  still load it as `C:\windows\system32\amdhip64_7.dll`.
- **Update or Repair each installed game** after updating the manager. The fix
  is in the wrapper inside the game folder, and v0.3.2-preview and
  v0.3.3-preview installations keep the old wrapper until then. Update and
  Repair also remove the bridge the old wrapper left under the lowercase name,
  and the new wrapper does the same at the next launch if a game still has it.
  As before, the wrapper never overwrites a different HIP DLL under either
  name; it stops and says so. With `manage.py`, uninstall the old package using
  this release's `manage.py` and install the new one.
- **Add game chose the wrong executable for Unreal games.** A packaged Unreal
  game's root `Game.exe` only starts
  `Game/Binaries/Win64/Game-Win64-Shipping.exe`. Add game took the root
  executable as picked, so AMDNR went into the root folder, where the game never
  loads it, and no AMDNR menu appeared. Add game and **Choose game executable**
  now select the Shipping executable when exactly one matches the launcher's
  name. Point your Proton/Wine runner at that executable too, so it finds
  `amdnr-linux/run.sh` beside it. If an earlier release installed AMDNR in an
  Unreal game's root folder, Uninstall it there, use Choose game executable,
  then Install.
- **A manual game forgot its chosen executable at the next rescan.** Choices
  made with Choose game executable were kept for Steam, Heroic and Lutris games
  only; a game added with Add game went back to the folder it was added from.
  Manual games now keep theirs.

## Changed

- The install guide's troubleshooting table covers both problems, and notes
  that many Unreal Engine 4 games start in DX11, where no AMDNR menu appears:
  add `-dx12` to the launch options, or `[D3DRHIPreference]` with
  `bUseD3D12InGame=True` to the game's `GameUserSettings.ini`.

## Validation

The complete GPU suite passed three times in a row on RX 9070 XT under
Proton-Wineland 11.0 (2026-09-30) with the 0.3.5.6 package, on the same bridge
and graphics binaries as v0.3.3-preview. The wrapper stored the bridge as
`AMDHIP64_7.DLL` in each run, and the host loaded it as
`C:\windows\system32\amdhip64_7.dll`. The runs covered the released host under
its hooks; the 720-frame NR-off/on host pipeline with lmxxf (597 fresh composed
outputs in a 599-frame reporting window in each run, mean absolute RGB change
0.0256 from the NR-off image); a host run with danielblnc 0.6.0 (700, 700 and
717 passes completed, no timeouts); OptiPatcher 0.41 loading; and the Model E
runs, where the host verified the receipt, HelixSR's network ran on all 720
frames and its output differed from FSR 3.1 on 16.6% of output channels. Model E
was the same build as for v0.3.3-preview (SHA-256 `11ed21a5…`). The only skip is
Wineland's known shared-fence rewind limitation. All 344 Linux unit tests pass.

Under Proton-CachyOS `cachyos-11.0-20261014` through umu-run, the bridge stored
as `AMDHIP64_7.DLL` survived the runner's startup, `hipInit` returned 0 and HIP
reported 2 devices.

These are synthetic API and pipeline checks. No new game compatibility or
long-duration gameplay stability is claimed.

# Linux v0.3.3-preview — remove AMDNR leftovers

Development **pre-release**. The manager can now clean up after AMDNR in a game
folder it does not see as installed. The AMDNR 0.3.5.6 host, Model E support,
the Linux bridge and the patched graphics binaries are the same as in
v0.3.2-preview. No game has been played with 0.3.5.6 or Model E on Linux yet.

## Added

- **Remove leftover AMDNR files.** Uninstall needs the installation record,
  `.amdnr-linux-install.json`, so a folder without one had no way back to a
  clean state: the host's logs, crash dumps and session markers stayed after an
  uninstall, and a folder whose record was lost kept the whole installation,
  with "Original backups exist without an installation record" or leftover
  prefix state blocking a new install. When a game has no record, the manager
  now shows **Remove leftover AMDNR files** in place of Uninstall, and
  `manage.py clean` does the same from a terminal. Both list every change and
  ask first.
- Files are removed only on evidence: this addon's own folders, or AMDNR named
  inside the host DLL, `OptiScaler.ini` or the host's logs. The patched
  vkd3d-proton DLLs and the shader compiler go only beside this addon's own
  traces. Originals in `.amdnr-linux-backup` and the Proton prefix journal are
  put back (a prefix file changed since is left as it is, and a deleted prefix
  is not recreated), and the AMDNR wrapper is taken out of Steam's launch
  options, also when the manager has no record of setting them. Finished
  `AMDNR-report-*.zip` reports, other OptiScaler builds, links such as a mod
  manager's, and anything that cannot be attributed stay; the list says which
  and why. Like Uninstall, the removal is one recoverable transaction and the
  last `OptiScaler.ini` is kept in the manager's data folder.

## Validation

The complete GPU suite passed three times in a row on RX 9070 XT under
Proton-Wineland 11.0 (2026-09-30) with the 0.3.5.6 package, on the same bridge
and graphics binaries as v0.3.2-preview: the released host under its hooks; the
720-frame NR-off/on host pipeline with lmxxf (597 fresh composed outputs in a
599-frame reporting window in each run, mean absolute RGB change 0.0240–0.0258
from the NR-off image); a host run with danielblnc 0.6.0 (717, 715 and 717
passes completed, no timeouts); OptiPatcher 0.41 loading; and the Model E runs,
where the host verified the receipt, HelixSR's network ran on all 720 frames and
its output differed from FSR 3.1 on 16.6% of output channels. Model E was
rebuilt here from a local copy of NVIDIA's DLSS 310.7.0 DLL and produced the
same file as before (SHA-256 `11ed21a5…`). The only skip is Wineland's known
shared-fence rewind limitation. All 337 Linux unit tests pass.

The cleanup was also run on a real install of the 0.3.5.6 package whose record
had been deleted: the game folder and the Proton prefix were byte-identical to
their state before the install. On a copy of a real game folder's leftovers
after an uninstall, it removed only AMDNR's logs, session marker and empty
`OptiScaler` folder, leaving the game's own FidelityFX DLLs and a linked
REFramework DLL alone.

These are synthetic API and pipeline checks. No new game compatibility or
long-duration gameplay stability is claimed.

# Linux v0.3.2-preview — install-guide corrections

Development **pre-release**. The manager, the AMDNR 0.3.5.6 host, Model E
support, the Linux bridge and the patched graphics binaries are the same as in
v0.3.1-preview; this release corrects the install guides and brings `main` up
to date. No game has been played with 0.3.5.6 or Model E on Linux yet.

## Changed

- `INSTALL-CLI.md` still said that `setup.py` downloads AMDNR 0.3.5.5R4. It
  has downloaded 0.3.5.6 since v0.3.1-preview; the guide and its `--archive`
  example now name `AMDNR-v0.3.5.6.zip`.
- Both install guides say that the recorded Stellar Blade run used AMDNR
  0.3.5.5R4.
- `main` now holds all of the preview work since v0.2.1 (danielblnc's runtime,
  the pre-SR host, Heroic and Lutris, AMDNR 0.3.5.6 and Model E) and upstream
  AMDNR's latest manifest.

## Validation

The complete GPU suite passed three times in a row on RX 9070 XT under
Proton-Wineland 11.0 (2026-09-30) with the 0.3.5.6 package, on the same bridge
and graphics binaries as v0.3.1-preview: the released host under its hooks; the
720-frame NR-off/on host pipeline with lmxxf (597 fresh composed outputs in a
599-frame reporting window, mean absolute RGB change 0.0256 from the NR-off
image); a host run with danielblnc 0.6.0 (700, 717 and 712 passes completed, no
timeouts); OptiPatcher 0.41 loading; and the Model E runs, where the host
verified the receipt, HelixSR's network ran on all 720 frames and its output
differed from FSR 3.1 on 16.6% of output channels. Model E was rebuilt here from
a local copy of NVIDIA's DLSS 310.7.0 DLL and produced the same file as for
v0.3.1-preview (SHA-256 `11ed21a5…`). The only skip is Wineland's known
shared-fence rewind limitation. All 324 Linux unit tests pass.

These are synthetic API and pipeline checks. No new game compatibility or
long-duration gameplay stability is claimed.

# Linux v0.3.1-preview — AMDNR 0.3.5.6 host and Model E

Development **pre-release**. The manager and `setup.py` now install upstream
**AMDNR 0.3.5.6**, and can add its new **Model E** upscaler (HelixSR). The Linux
bridge and patched graphics binaries are the same files as in v0.3.0-preview.
No game has been played with 0.3.5.6 or Model E on Linux yet.

## New

- **AMDNR 0.3.5.6 is the default host.** It is pinned to the archive on
  upstream's GitHub release (`AMDNR-v0.3.5.6.zip`, SHA-256 `e7ffc096…49968d8`,
  753,230,845 bytes), which upstream's own manifest now names too. Its archive
  has the same files as 0.3.5.5R4 and an identical `OptiScaler.ini`; the host,
  the lmxxf runtime, its kernel pack and upstream's documents and notice change.
  See upstream's changelog for what 0.3.5.6 changes in-game (Full Model Look by
  default, extra face detail, less lmxxf ghosting, the danielblnc "timeout" fix,
  a smaller default network on RX 7000 and handhelds, new menu rows).
- **Model E (HelixSR by lonewolf0622), optional.** HelixSR runs NVIDIA's DLSS
  network as Direct3D 12 shaders and replaces FSR 3.1 / FSR 4 in D3D12 games,
  with either neural runtime. Tick **Model E upscaler** in a game's panel (or
  `setup.py prepare --model-e`). After a one-time notice, the manager downloads
  HelixSR 1.8.1 from its author's release, Microsoft's DirectX shader compiler,
  and NVIDIA's DLSS 310.7.0 DLL from NVIDIA's GitHub under NVIDIA's licence, and
  runs HelixSR's own setup scripts to build its network on your PC (about a
  minute, once). It needs Python 3 with numpy and Proton or Wine. NVIDIA's DLL is
  deleted afterwards; the built files contain NVIDIA's network and stay on your
  PC. The game gets `AMDNR-SR-E/` with the receipt AMDNR 0.3.5.6 checks before it
  loads HelixSR, and `[Upscalers] FfxSRProvider=helix_e`. Unticking it puts the
  game's FSR provider back.
- The 0.3.5.6 host pins the same danielblnc pass digests as 0.3.5.5R4, so the
  manager keeps offering danielblnc 0.4.0–0.6.0 with it.
- **Update** in the manager moves an installed game from 0.3.5.5R4 to 0.3.5.6
  and keeps the game's `OptiScaler.ini`, including your own edits.
- Save logs includes `AMDNR-SR-E/helixsr.log`.
- 0.3.5.5R4 and 0.3.4.2 archives are still accepted by `manage.py stage`.
- GPU tests: `--model-e` adds FSR 3.1 host runs without and with Model E, and
  with Model E plus lmxxf.

## Validation

The complete GPU suite passed under Proton-Wineland 11.0 (2026-09-30) on
RX 9070 XT with the 0.3.5.6 package: the released host under its hooks; the
720-frame NR-off/on host pipeline with lmxxf (597 fresh composed outputs in a
599-frame reporting window, mean absolute RGB change 0.0256 from the NR-off
image); a host run with danielblnc 0.6.0 (every pass completed, no timeouts);
OptiPatcher 0.41 loading from `OptiScaler/plugins`; and the new Model E runs.
With Model E, the host verified the receipt and created HelixSR's context,
HelixSR's network ran on all 720 frames, its output differed from FSR 3.1 on
16.6% of output channels, and Model E with lmxxf NR passed the same composition
checks as lmxxf alone. HelixSR's network was built here from NVIDIA's DLSS
310.7.0 DLL in about 30 seconds. The only skip is Wineland's known shared-fence
rewind limitation. All 324 Linux unit tests pass. The real 0.3.5.6 package
completed install and uninstall in a temporary game folder through `manage.py`,
and an install of the v0.3.0-preview package (0.3.5.5R4) updated to it through
the manager's backend and uninstalled cleanly.

These are synthetic API and pipeline checks. No new game compatibility or
long-duration gameplay stability is claimed.

## Not included

- **Model L** (d4r by countervolts), new in 0.3.5.6: it needs HIP/ZLUDA
  functions the bridge does not provide.
- AMDNR's own "AMD SDK" package for HelixSR: it is published only on AMDNR's
  server, not on GitHub, and HelixSR's upscaling does not need it.
- AMDNR Anywhere (needs Windows window capture) and the NVIDIA-only
  `nvngx.dll_dlssnr.dll` forwarder.

# Linux v0.3.0-preview — danielblnc runtime, pre-SR host, Heroic and Lutris

Development **pre-release**. It adds the remaining runtime choices and game
sources that upstream AMDNR's Windows launcher offers, as far as they can work on
Linux. The bundled host/model/runtime remain AMDNR 0.3.5.5R4; the Linux bridge is
rebuilt with new HIP exports. No game has been played with the new runtime
choices yet, and this build's bridge has automated GPU validation only.

## New

- **danielblnc's runtime on Linux.** The HIP bridge now exports the compiler's
  module ABI that HIP-compiled DLLs use (fat-binary, kernel and variable
  registration, push/pop call configuration, `hipLaunchKernel`, symbol copies,
  occupancy and stream priorities). DLSS-NR on AMD by Daniel Blanco 0.4.0, 0.4.1,
  0.4.3, 0.5.0, 0.5.1 and 0.6.0 each completed all 700 GPU jobs with no timeouts
  through the released 0.3.5.5R4 host's NGX/FSR2 pipeline on RX 9070 XT, with
  output materially different from the NR-off baseline. No game has been
  reported with it yet.
- Choose the neural runtime per game in the manager (or `setup.py prepare
  --daniel-runtime`). Choices follow the detected GPU generation and the
  digests the AMDNR host pins; integrated GPUs are not offered danielblnc. The
  runtime is downloaded from AMDNR's release only when chosen and checked
  against pinned digests; it is never republished by this fork.
- Optional alternate host: TheAutomatic's **OptiScaler AMD pre-SR 1.9.1-alpha**,
  which upstream's launcher also offers (manager **Host build**, or `setup.py
  prepare --host presr`). It drives danielblnc's own 0.3.1 build only, downloaded
  from AMDNR's release; its Windows installer scripts are not installed. The
  released host NGX/FSR2 pipeline passes with it on RX 9070 XT (577/577 GPU
  passes, no timeouts, OptiPatcher loads). No game has been reported with it yet.
  Switching host builds starts `OptiScaler.ini` from the new host's file and
  keeps the previous one in the manager's state folder.
- Optional OptiScaler **OptiPatcher** plugin (0.41/0.40), installed into
  `OptiScaler/plugins/` with `LoadAsiPlugins=true`, and removed again on uninstall.
- Changing the runtime or plugin rewrites only the matching `OptiScaler.ini`
  keys and keeps the rest of the user's configuration. An in-game runtime choice
  survives Repair while its files are installed.
- Game notes before installing: titles without a temporal upscaler, anti-cheat
  software, other OptiScaler/standalone danielblnc/ReShade installs, and setup
  notes. Blocking notes ask for confirmation. First installs seed GTA V
  Enhanced `[Spoofing] Dxgi=false` and Resident Evil Requiem
  `[Menu] OverlayMenu=false`. Cyberpunk 2077 prefers `wininet.dll` and Marvel's
  Spider-Man `dbghelp.dll` as the loading DLL.
- Save logs and startup diagnostics include `amd_presr.log`,
  `dlssnr_on_amd.log/.ini`, `amdnr_crash.log` and the installed component list.
- GPU tests: a hipcc module ABI test, `--daniel-runtime` and `--optipatcher`
  options, and a wait for the host's GPU identification thread that avoids a
  DXVK deadlock in the harness.
- Include `re2_framework_log.txt` in startup diagnostics and Save logs, and
  document the older REFramework Proton startup deadlock that can leave RE9
  black before neural rendering starts. This does not claim RE9 gameplay support.
- Choose an OptiScaler loading DLL per game under **Advanced compatibility**.
  Automatic selection prefers imports from the selected x64 executable and skips
  occupied names, including differently cased filenames. Supported choices are
  `winmm.dll`, `version.dll`, `dbghelp.dll`, `wininet.dll` and `winhttp.dll`;
  graphics translation DLL names stay reserved.
- **Try next DLL** changes the installed host without downloading again, tracks
  attempts and skips occupied/tried names. Explicit selection allows a retry.
  DLL moves, wrapper settings and ownership records commit or roll back together.
  Repair/Update retain the proxy choice and history; older wrappers need Repair
  using a current package before switching. The CLI supports `install --proxy`
  and `switch-proxy --proxy next` too.
- The library also lists Windows games installed through **Heroic** (Epic Games,
  GOG, Amazon and sideloaded apps) and **Lutris** (Wine runner), with a
  **Launch in Heroic** / **Launch in Lutris** button. The manager only reads their
  settings; you add the wrapper line it shows to that launcher's wrapper/command
  prefix. Not yet tried against a real Heroic or Lutris installation.
- Steam thumbnails and banners in the library, cached for offline use; manually
  added games show the icon embedded in their `.exe`.

## Validation

The full GPU suite passed under Proton-Wineland 11.0 (2026-09-30) on RX 9070 XT
with the rebuilt bridge, including the new hipcc module ABI test, the released
host under its hooks, the 720-frame NR-off/on host pipeline with lmxxf, a third
host run with danielblnc 0.6.0 (700/700 GPU jobs, no timeouts, material change
from the NR-off image) and OptiPatcher loading from `OptiScaler/plugins`. The
only skip is Wineland's known shared-fence rewind limitation (see below). Each
danielblnc version 0.4.0–0.6.0 also passed that host run on its own. All Linux
unit tests pass, and a real pinned package with danielblnc 0.6.0 and OptiPatcher
completed install and uninstall in a temporary game folder.

Two early runs of the release binaries failed at the first lmxxf host-pipeline
frame (one crash, one hang), minutes after the display was reconnected. The
same binaries then passed five consecutive runs, and a side-by-side comparison
passed 3/3 for the previous bridge, the development build and the release
build. The cause was not identified; please report a crash at the first frame
with `OptiScaler.log`, `lmxxf_backend.log` and `amd_bridge.log`.

All **20 CPU proxy probes** passed: five aliases, both explicit loading and
static imports, under GE-Proton11-9 and Proton-Wineland 11.0. Each verified the
released AMDNR host and representative Windows API forwarding. The real pinned
payload also completed install → switch → repair → update → uninstall in a
temporary game folder, preserving original game/prefix files and user settings.
No games were launched for proxy validation; these results do not claim new
game compatibility or improved FPS.

These are synthetic API and pipeline checks. No new game compatibility or
long-duration gameplay stability is claimed.

## Not included

- AMDNR 0.3.5.6 (Model E/L): its GitHub asset still differs from the archive
  upstream's own manifest pins, so it is not pinned yet. Model L (d4r) needs
  HIP/ZLUDA functions the bridge does not provide.
- AMDNR Anywhere (needs Windows window capture) and the NVIDIA-only
  `nvngx.dll_dlssnr.dll` forwarder.

# Linux v0.2.2-preview — Synchronization and compatibility fixes

Local development preview. Existing game installations and saves were not
changed, and no games were launched during this optimization pass. The bundled
host/model/runtime remain AMDNR 0.3.5.5R4; the Linux bridge is rebuilt.

## Improvements

- Batch external-fence operations into one native callback per API call. Already
  completed waits avoid event registration, and device removal is reported as
  an error before/after waiting. Full-batch validation, duplicate handles and
  queued-operation ownership remain covered by the GPU regression test.
- Discover game executables without walking helper, backup and bundled-source
  trees. Recognize Crash Bandicoot executables instead of treating every name
  beginning with `crash` as a crash reporter.
- Detect running Wine games using relative and DOS executable names before
  changing files. Resume interrupted prefix setup and restore missing owned
  DLLs while preserving original files and refusing modified files or redirected
  paths.
- Support preview versions consistently in the launcher, installation records,
  and upgrade ordering. Automatic update discovery still offers stable releases.
- Record reproducible benchmark data and binary hashes. Failed setup, failed
  shutdown and skipped subchecks remain visible in the result. A release can
  attach validation only for the exact binaries tested; it no longer inherits
  gameplay claims from an earlier release automatically.

## Measurements on RX 9070 XT

Four alternating baseline/candidate pairs under GE-Proton11-9, with 32 warmup
and 256 measured samples per fence test, gave these medians of run means:

| Measurement | Previous bridge | Preview bridge |
| --- | ---: | ---: |
| Three-fence wait/signal round trip | 0.09463 ms | 0.04827 ms |
| 1720×720 serialized neural pipeline with readback | 13.856 ms | 13.769 ms |

The fence operation is about **49% cheaper**. The small whole-pipeline difference
is within observed run-to-run variation; **no gameplay FPS increase is claimed**.
All eight neural runs produced the same final RGB checksum, with full network,
history input and controls enabled. Neural measurements exclude 10 warmup frames
and contain 110 measured frames per run.

Steam discovery across the nine installed games returned identical selections
in 20 alternating warm-cache comparisons: median **34.230 ms → 12.745 ms**
(about 63% less discovery time). This is launcher responsiveness, not game FPS.

## Validation

**172 Linux tests pass** under system Python 3.14 and bundled Python 3.12. The
rebuilt bridge passed the full GE-Proton11-9 GPU suite, including 1,200 full-network
1720×720 frames directly and 1,200 under the released host's hooks, alternating
synchronous/asynchronous submission. The host's 720-frame NR-off/on comparison
also passed with finite output and fresh neural composition counters.

Proton-Wineland 11.0 (2026-09-30) passed the remaining GPU and host tests at 120
neural frames per mode, with one explicit limitation: its direct D3D12 shared
fence does not rewind from 202 to 201. The bridge matches that behavior; the
rewind subcheck is marked skipped and the run is `passed_with_skips`. GE passed
that subcheck. Tests also verify that their isolated Wine server exits.

These are synthetic API and pipeline checks. No new game compatibility or
long-duration gameplay stability is claimed.

## Host-source fixes

The 0.3.4.2 source snapshot now confirms fence completion before resize releases
textures, retains resources when completion cannot be proved, and rejects the
removed-device sentinel when retiring runtime sessions. Optional statistics
allocation errors are handled and the exposure readback range covers all bytes
read. Three CPU/compiler checks cover the changed resource code.

These changes need a separate Windows/MSVC host rebuild and game validation;
they are not active in the prebuilt 0.3.5.5R4 host. Improved discovery and API tests
do not establish support for additional games.

# Linux v0.2.1 — Compact launcher and Steam library fix

**Download `NR-Linux-Manager-v0.2.1-linux-x86_64.tar.gz`, extract it, and open `Start.sh`.** Python and Qt are included. Select a game, close the game and Steam, and choose **Install neural rendering**. Launch through Steam using your existing Proton version and saves. Enable DLSS, FSR or XeSS in the game; **Insert** opens OptiScaler and **Home** toggles neural rendering.

[Installation, update and removal guide](https://github.com/hassanm0301/AMDNR-Linux/blob/linux-v0.2.1/linux/INSTALL.md).

## What changed

- Removed the navigation sidebar and oversized library heading. Add game, Rescan library and Settings now share a compact toolbar.
- Moved release checks into Settings. Game actions retain Install, Repair, Update and Uninstall.
- Fixed resizing with evenly sized game rows, consistent button heights and spacing, and scrolling game details. Game folder and Save logs now have clear button borders.
- Fixed stale selections after filtering, empty-library states and installation-state refresh after an error.
- Fixed duplicate Steam game detection: the launcher now follows Steam’s active library index instead of selecting an older copy on another drive.
- Improved Repair for missing runtime files and lost launch-script permissions. Installation status describes file health without claiming a running neural session.
- Added wrapper startup diagnostics and a more complete log export, including when the host never loads. Saving over an earlier export now works without risking that export if collection fails.

## Dragon’s Dogma 2

The reported missing AMDNR overlay was caused by patching an older game copy while Steam launched another drive’s copy. After restoring the misplaced installation and patching Steam’s active copy, neural rendering ran on **RX 9070 XT / GE-Proton11-9**, with FSR 3 Quality and the existing REFramework installation. The player confirmed normal-looking gameplay using the existing save; live model and fresh-composition counters confirmed neural processing.

If v0.2.0 already patched a stale copy, use that launcher’s **Uninstall and restore originals** on its old entry before installing into the active copy with v0.2.1. This restores the old wrapper and any prefix files it owns. Keep its backup and ownership records until removal completes.

## Validation and downloads

**146 automated tests pass** under both system Python 3.14 and bundled Python 3.12, including GUI resizing from 800×600 through 1920×1080, Steam duplicate-library selection, install/repair/update/uninstall, and interrupted-operation recovery. A real isolated runtime repair test removed a standard-library module and verified that repair restored a working runtime.

Rendering binaries and the pinned AMDNR 0.3.5.5R4 / ROCm 7.2.4 dependencies are unchanged. Stellar Blade remains validated with this bridge; The Witcher 3 DX12 passed an earlier bridge build. Game and GPU coverage remains limited to short tests; see the guide for requirements and limitations.

- **NR-Linux-Manager-v0.2.1-linux-x86_64.tar.gz** — recommended native portable app, editable launcher source and Python/Qt runtime with notices and corresponding Qt/PySide source.
- **AMDNR-Linux-v0.2.1.tar.gz** — smaller addon for advanced command-line installation.
- **AMDNR-Linux-v0.2.1-vkd3d-source.tar.gz** — corresponding patched graphics source.
- **INSTALL.md** and **SHA256SUMS** — instructions and asset checksums.

AMDNR is by [3zwr1](https://github.com/3zwr1/AMD-NR---OptiScaler). This independent Linux manager uses no upstream Windows launcher code or visual assets. Upstream model/runtime and ROCm are obtained directly from their publishers with pinned checksums; game files, saves, user logs and the Microsoft compiler are not redistributed.
