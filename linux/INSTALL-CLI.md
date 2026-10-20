# AMDNR Linux v0.2.0 — advanced command-line installation

This experimental release runs AMDNR's **lmxxf neural rendering** through native
Linux ROCm while the game runs under Proton. It includes the tested Linux bridge
and patched vkd3d-proton binaries. **No compiling or system ROCm installation is
needed.** AMDNR is by [3zwr1](https://github.com/3zwr1/AMD-NR---OptiScaler); this is
an independently modified Linux fork.

## Before you start

- **Tested GPU:** Radeon RX 9070 XT (RDNA 4). Other GPUs, including Steam Deck,
  are not validated by this release. The upstream model must support your GPU.
- **Tested game:** Stellar Blade with **GE-Proton11-9**, using its original Steam
  prefix and an existing save. The Witcher 3 DX12 passed with an earlier bridge
  and Proton-Wineland 11.0 (2026-09-30); it has not been retested with this package.
  These were short gameplay checks, not long-duration stability tests.
- A **64-bit x86 Linux** installation, working AMD Vulkan driver, and usable
  `/dev/kfd` and `/dev/dri/renderD*` devices. Use the distribution's normal GPU
  permission setup; do not launch the game or installer as root.
- Wine 11-based Proton. Use GE-Proton11-9 for the tested configuration. This
  bridge depends on Wine 11's Vulkan layout; older Proton is not supported.
- The process runtime needs **glibc 2.34+** and **GCC 12-era libstdc++**
  (`GLIBCXX_3.4.30`, `CXXABI_1.3.13`), plus Vulkan/DRM, zlib and zstd libraries.
  The tested Steam Runtime 4 supplies these. Other distributions and runtimes
  have not been validated; meeting these version floors alone is not a guarantee.
- **Python 3.12+** (3.12/3.13 also need the Python `zstandard` package), `tar`, an internet connection and about
  **3 GB of free disk space** for downloads, extraction, and a prepared package.
  More space is needed for each game installation and any backed-up mod files.
- A **native 64-bit Microsoft `d3dcompiler_47.dll`** from a game you own. The
  tested version is **10.0.14393.795**. Wine's builtin replacement cannot compile
  all the neural renderer's shaders. This file is not distributed in this release.

First verify the game launches and loads your save with your chosen Proton and
without this add-on. Close it before installing. Remove conflicting OptiScaler,
ReShade, or other DLL-injection mods using their own uninstall instructions;
keep backups of your configurations. Test one change at a time.

## 1. Download and extract

From this fork's [Linux v0.2.0 release](https://github.com/hassanm0301/AMDNR-Linux/releases/tag/linux-v0.2.0),
download **`AMDNR-Linux-v0.2.0.tar.gz`** and **`SHA256SUMS`** into the same folder.
The separate vkd3d source archive is for rebuilding; normal installation does
not require it. GitHub's automatically generated “Source code” archives do not
contain the prebuilt binaries.

In a terminal in that folder:

```sh
sha256sum --ignore-missing -c SHA256SUMS
tar -xzf AMDNR-Linux-v0.2.0.tar.gz
cd AMDNR-Linux-v0.2.0
```

The archive check must say `OK`. Keep this extracted folder for installation,
removal, and troubleshooting.

## 2. Prepare the game package

Replace the example path with your own compiler file. Quote paths with spaces:

```sh
python3 linux/setup.py prepare --compiler "/path/to/your/game/d3dcompiler_47.dll"
```

This verifies the included binaries, downloads the checksum-pinned **AMDNR
0.3.5.6** archive from its upstream GitHub release, and downloads **ROCm 7.2.4**
and its small dependencies directly from AMD and Ubuntu. It extracts a private
runtime into the package; it does not install system packages, change drivers,
or run package installation scripts. Upstream notices and licenses are retained.
Read those notices for the downloaded components' terms.

When it finishes, the ready-to-install files are in **`linux/build/package`**.
Preparation does not change a game or a Proton prefix. Downloads are cached;
rerunning after a failed download reuses files whose checksums match.

If you already downloaded the exact upstream archive, you can use it:

```sh
python3 linux/setup.py prepare \
  --compiler "/path/to/d3dcompiler_47.dll" \
  --archive "/path/to/AMDNR-v0.3.5.6.zip"
```

An existing output folder is never overwritten. Reuse the prepared package for
another game, or specify a different `--output` directory when preparing again.

Current development builds can also stage danielblnc's runtime (DLSS-NR on AMD
by Daniel Blanco, downloaded unmodified from AMDNR's release) and OptiScaler's
OptiPatcher plugin:

```sh
python3 linux/setup.py prepare --compiler "/path/to/d3dcompiler_47.dll" \
  --daniel-runtime 0.6.0 --optipatcher
```

`--daniel-runtime` accepts 0.6.0, 0.5.1, 0.5.0, 0.4.3, 0.4.1 or 0.4.0 and selects
`[DlssNr] NrBackend=daniel`; the lmxxf runtime stays installed beside it. Use
0.4.0–0.6.0 on RDNA 4/RDNA 3 and 0.6.0 on RDNA 2; integrated GPUs are not
supported by danielblnc's runtime. Preparation refuses a runtime the AMDNR host
does not pin. `--optipatcher` (optionally with a version, 0.41 or 0.40) places
the plugin in `OptiScaler/plugins/` and sets `[Plugins] LoadAsiPlugins=true`. Every archive
and file is checked against pinned SHA-256 digests. Check danielblnc's own
`amd_presr.log` and `dlssnr_on_amd.log` instead of `lmxxf_backend.log` when you
use his runtime.

To use TheAutomatic's "OptiScaler AMD pre-SR" 1.9.1-alpha host instead of
AMDNR's, add `--host presr`:

```sh
python3 linux/setup.py prepare --compiler "/path/to/d3dcompiler_47.dll" --host presr
```

This independent OptiScaler fork runs danielblnc's runtime only, and only
danielblnc's own 0.3.1 build, so `--daniel-runtime` defaults to 0.3.1 and
refuses any other version (0.3.1 is not offered with the AMDNR host).
`--archive` then takes the pinned `OptiScaler-AMD-PreSR-1.9.1-alpha.zip`. The
package contains the host, its `OptiScaler/` folder, `experimental_lighting/`
shaders and `Licenses/`. Its Windows `Setup`/`Uninstall` scripts, READMEs and
lmxxf files are left out: its lmxxf backend needs weights that are not
published anywhere that can be pinned. `--optipatcher` works with it too.

## 3. Install beside the actual game executable

In Steam, use **Properties → Installed Files → Browse** to find the game.
Install into the directory containing the **actual 64-bit DX12 game executable**,
which may be below the directory Steam opens. For example:

| Game | Directory inside the game's installation |
| --- | --- |
| Stellar Blade | `SB/Binaries/Win64` |
| The Witcher 3 (DX12) | `bin/x64_dx12` |

With the game closed, replace the example directory below:

```sh
python3 linux/manage.py install linux/build/package "/path/to/game/executable/directory"
```

The installer backs up files it replaces and prints the correct Steam launch
option. Paste that option into **Properties → General → Launch Options**:

```text
"/path/to/game/executable/directory/amdnr-linux/run.sh" %command%
```

Current development builds also accept `--proxy auto` (the default),
`winmm.dll`, `version.dll`, `dbghelp.dll`, `wininet.dll`, or `winhttp.dll`.
Automatic selection skips occupied names and prefers a DLL imported by the main
executable. Supply `--exe "/path/to/game/Game.exe"` when several executables share
the folder. For example:

```sh
python3 linux/manage.py install linux/build/package "/path/to/game" --proxy version.dll
```

With the game closed, change an installed proxy without downloading again:

```sh
python3 linux/manage.py switch-proxy "/path/to/game" --proxy next
python3 linux/manage.py switch-proxy "/path/to/game" --proxy dbghelp.dll
```

The command skips previously tried names and refuses occupied slots. Once the
untried choices are exhausted, choose a free name explicitly. The wrapper reads
the managed `amdnr-linux/proxy.json`; do not rename DLLs by hand. Older packages
need to be prepared again with the current scripts before using alternatives.
For Steam games managed by the graphical launcher, use its controls to retain
coordinated Steam recovery. These commands are not present in v0.2.0 above.
Graphics DLL names (`dxgi`, `d3d11`, `d3d12`, `d3d12core`) are reserved.

If MangoHud and GameMode already work for this game, retain them:

```text
MANGOHUD=1 gamemoderun "/path/to/game/executable/directory/amdnr-linux/run.sh" %command%
```

Keep any working game arguments after `%command%`. For The Witcher 3, the tested
arguments were `--launcher-skip --rt-only`, selecting the DX12 version.

**Launch through Steam.** The wrapper uses the game's existing Steam prefix and
save locations. Do not start the Windows executable directly or create a new
prefix. On first launch, it backs up and installs the required shader compiler
and HIP bridge in that prefix. Keep the game's `amdnr-linux` folder, prefix
journal, and `.amdnr-linux-backup` directory intact while installed.

## 4. Check that neural rendering is active

Enable a supported upscaler in the game's graphics settings so OptiScaler
receives frames. Open the OptiScaler menu with **Insert**; the prepared package
selects the **lmxxf** neural backend. **Home** is the upstream default toggle for
the neural effect. A menu banner alone does not prove inference is working.

After loading a save, look in **`lmxxf_backend.log`** beside the game executable.
It should report `hip=1`, your AMD GPU, successful network initialization, and
increasing **model** and **fresh composition** counts. The successful Stellar
Blade test (AMDNR 0.3.5.5R4) recorded 3,599 model frames and 3,594 fresh
composed outputs, with temporal history and all 71 network blocks active.

Move around, compare Home on/off, and check loading, menus, and image quality.
Neural rendering has a substantial GPU cost: the tested kernel time was about
11–13 ms, in addition to the rest of the game's rendering. Frame generation,
Ray Regeneration, AMDNR Anywhere, and the danielblnc backend are outside this
release's validation coverage. (Current development builds add the danielblnc
runtime and test it in the automated host pipeline; see above.)

## Update or uninstall

Close the game and remove the wrapper path from Steam's launch options. From the
extracted release folder, run:

```sh
python3 linux/manage.py uninstall "/path/to/game/executable/directory"
```

This restores backed-up game files and prefix files and leaves unrelated saves
and logs alone. **Uninstall the old package before installing a newer release.**
Do not overwrite the installed folder with a newer archive.

If removal reports a file changed since installation, first copy that file
somewhere safe. This often happens after changing `OptiScaler.ini` in the menu.
Then, if you want to discard changes to the package's managed files and restore
the original backup, use:

```sh
python3 linux/manage.py uninstall "/path/to/game/executable/directory" --force
```

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Nothing opens with the wrapper | Confirm the full quoted path, `python3 --version` (3.12+), and that the game works without the wrapper. Keep `%command%` and working game arguments. |
| Save files appear missing | Launch from Steam with the original Proton prefix; do not run the executable directly. This installer does not move saves. |
| Legacy ELF HIP bridge error | Uninstall the previous managed installation and install this release. Never mix `amdhip64_7.dll.so` from an older build with the new PE/Unixlib pair. |
| No HIP device / no neural frames | Check RX 9070 XT support, GPU device permissions, private ROCm files, and `lmxxf_backend.log`. A working Vulkan game alone does not establish ROCm access. |
| Shader compilation fails | Supply a native **x64 Microsoft** compiler; the known-good version is 10.0.14393.795. Do not use Wine's builtin DLL or a random DLL download site. |
| Crash when rendering begins | Use the tested Proton version; disable conflicting mods. Compare with `[DlssNr] Enabled=false` in `OptiScaler.ini`, keeping the same prefix. Preserve logs from both runs. |
| RE9 stays black before rendering begins | Check `re2_framework_log.txt` for a stop at `Scanning DD2...` after thread suspension. Older REFramework builds have a known Proton deadlock; see the [REFramework troubleshooting note](INSTALL.md#troubleshooting-and-downloads). |
| Unsupported Wine Vulkan object ABI | Use GE-Proton11-9. Other Wine layouts are deliberately rejected rather than guessed. |
| Flatpak Steam / Steam Deck | Not validated. Flatpak additionally needs access to the game, wrapper, Python, and GPU devices inside its sandbox. This release does not change sandbox permissions. |

For a diagnostic Proton log, temporarily add `PROTON_LOG=1` at the beginning of
the Steam launch option. Collect `OptiScaler.log`, `lmxxf_backend.log`,
`amd_bridge.log` if present, your GPU/driver/Proton versions, and the exact symptom.
For REFramework games, also collect `re2_framework_log.txt`.
Review logs for personal paths or identifiers before sharing them.

See the [technical guide](https://github.com/hassanm0301/AMDNR-Linux/blob/linux-v0.2.0/linux/README.md)
for architecture, build instructions, and automated GPU tests. A copy is included
at `linux/README.md` in the extracted release.
