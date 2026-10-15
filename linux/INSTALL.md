# NR Linux Manager — native Linux installation

**Download [NR-Linux-Manager-v0.2.1-linux-x86_64.tar.gz](https://github.com/hassanm0301/AMD-NR---OptiScaler-rocm/releases/tag/linux-v0.2.1), extract it, and open `Start.sh`.**
Python and the native Qt interface are included. No Proton setup is needed for
the launcher itself, and no compiling or system ROCm installation is needed.
If your file manager opens the script as text, choose “Run as program”, or open a
terminal in the extracted folder and run `./Start.sh`.

This independent community launcher installs the tested AMDNR Linux bridge.
AMDNR is by [3zwr1](https://github.com/3zwr1/AMD-NR---OptiScaler). It provides a
familiar game library and installation workflow with an original interface;
it does not include or port the restricted upstream Windows launcher.

## Install a Steam game

1. First check that the game launches and loads your save normally with Proton.
2. Open **NR Linux Manager**. It detects installed Steam games across library
   drives. Select a game. If asked, choose its actual **64-bit DirectX 12 game
   executable**, rather than a store launcher.
3. Close the game and **exit Steam completely**. Steam must be closed while its
   launch options are updated, otherwise it can overwrite the change.
4. Click **Install neural rendering**. The app finds a native shader compiler,
   downloads the pinned AMDNR and ROCm files, backs up affected files, and sets
   the Steam launch options. Existing MangoHud, GameMode and game arguments are
   preserved when they use a supported launch-option format.
5. Click **Launch in Steam** and load your save. Your existing Proton choice,
   prefix and saves are retained. Enable DLSS, FSR or XeSS in the game’s
   graphics settings. **Insert** opens OptiScaler; **Home** toggles neural rendering.

The first install needs an internet connection and several gigabytes of free
space. Later operations reuse verified downloads. If no native shader compiler
is found in your games, the app asks you to select `d3dcompiler_47.dll` from a
game you own. Wine's built-in DLL is unsuitable; do not use random DLL websites.
The known-good compiler is Microsoft's x64 version **10.0.14393.795**.

If a Steam game has never been launched, start it once in Steam so its account
settings exist. Complex custom shell launch options may require manual adjustment;
the app refuses ambiguous edits and explains why. Do not run the app as root.

For non-Steam games, use **Add a game** and select the main executable. The same
file installation works, but the app displays a launch option for you to add to
your existing Proton/Wine runner. Automatic runner integration currently covers
Steam only.

## Repair, update and uninstall

Use **Settings → Check for updates** to check for a newer release.
Close the game and Steam before using the selected game’s actions:

- **Repair** restores missing or changed managed files. It preserves your current
  `OptiScaler.ini` and the original backups. Use a launcher matching an installed
  newer package if an older app refuses the repair.
- **Update** checks this fork's releases, verifies the downloaded Linux addon,
  and applies it to the selected game. Original backups and your configuration
  survive updates. Updating the native app itself means downloading and extracting
  its newer portable archive; it does not replace itself while running.
- **Uninstall and restore originals** removes the managed addon and restores
  original game files, owned prefix files and Steam launch options. Your current
  addon configuration is also saved in the manager's data folder. Saves are kept.

A failed or interrupted operation retains recovery information. With the game
and Steam closed, repeat the action to recover it before continuing. If a file,
backup or Steam launch option was edited separately, the app may stop and explain
the conflict instead of erasing that edit. Keep the game's `.amdnr-linux-backup`,
`.amdnr-linux-install.json`, and `amdnr-linux` folders until removal completes.

The manager stores settings, Steam backups, recovery journals, saved
configurations and a small shared Python runtime in
`~/.local/share/nr-linux-manager` (or `$XDG_DATA_HOME/nr-linux-manager`). Downloads
are cached under `~/.cache/nr-linux-manager` (or `$XDG_CACHE_HOME/nr-linux-manager`).
The portable app folder may be moved after installation. **Keep the manager's
data folder while any patched game remains installed**: its shared Python runtime
is used to start those games. To remove the app entirely, uninstall each patched
game first, then remove its portable folder, data folder and cache.

## Compatibility and verification

The bridge still has limited game/hardware coverage; a native launcher does not
change the neural renderer's hardware requirements.

- **Tested GPU:** Radeon RX 9070 XT (RDNA 4).
- **Tested rendering:** Stellar Blade and Dragon’s Dogma 2 with GE-Proton11-9,
  existing saves and normal gameplay images. Dragon’s Dogma 2 was tested with
  FSR 3 Quality and its existing REFramework installation. The Witcher 3 DX12 worked with an earlier bridge and
  Proton-Wineland 11.0; it has not been retested with this exact package. These
  were short gameplay checks, not long-duration stability tests.
- **Required game runtime:** Wine 11-based Proton. GE-Proton11-9 is the tested
  choice. The bridge rejects unsupported Wine Vulkan layouts.
- **Required system:** x86_64 Linux, glibc 2.34+, a working AMD Vulkan driver,
  and access to `/dev/kfd` and `/dev/dri/renderD*`. The game's process also needs
  GCC 12-era libstdc++ (`GLIBCXX_3.4.30`, `CXXABI_1.3.13`) and Vulkan/DRM, zlib,
  zstd libraries. The tested Steam Runtime 4 supplies these.
- **Native interface:** X11 or Wayland and normal desktop libraries. On some
  Debian/Ubuntu X11 installations, Qt also requires `libxcb-cursor0`.
- **Not validated:** other GPUs, Steam Deck, Flatpak Steam, other game runners,
  frame generation, Ray Regeneration, AMDNR Anywhere and the danielblnc backend.
  Flatpak can detect a library while still denying access to the host runtime;
  this app does not alter Flatpak permissions.

Enable a supported upscaler in the game's graphics settings. **Insert** opens
OptiScaler; **Home** toggles the neural effect. The prepared package selects the
**lmxxf** backend. A banner alone does not prove neural inference is running.
Look in `lmxxf_backend.log` beside the executable for `hip=1`, successful network
initialization, and increasing **model** and **fresh composition** counts. The
successful Stellar Blade run recorded 3,599 model frames and 3,594 fresh outputs,
with temporal history and all 71 network blocks active. Rendering has a substantial
GPU cost; the measured neural kernel took about 11–13 ms per frame.

The native launcher has fixture tests for discovery, all four file operations,
Steam rollback, interrupted-operation recovery and GUI buttons. Packaging tests
check the portable runtime and retained rendering binary hashes. This release
reuses the previously game-tested rendering binaries; its new work is installation
and management, not a new neural model.

## Troubleshooting and downloads

| Problem | Action |
| --- | --- |
| Install asks to close Steam | Use Steam → Exit, then retry. Closing its window may leave it running. |
| Game is missing | Rescan; confirm its Steam library is mounted. Or choose Add a game. |
| Duplicate copies across library drives | The manager follows Steam’s current library index. If Steam reports multiple owners, resolve the duplicate in Steam and rescan before installing. |
| Wrong or ambiguous executable folder | Use Choose game executable. Stellar Blade uses `SB/Binaries/Win64`; Witcher 3 DX12 uses `bin/x64_dx12`. |
| Installation reports a conflict | Preserve the named file or settings; resolve the conflict before retrying. Conflicting DLL-injection mods may need their own uninstall process. |
| Wrapper says its Python runtime is missing | Open the complete portable launcher and Repair the game. |
| Saves seem missing | Launch through the original Steam entry and prefix. Do not directly run the `.exe`. |
| Game crashes or no neural frames appear | Check Proton version, GPU access and compiler; compare with `[DlssNr] Enabled=false` in `OptiScaler.ini`. |
| No AMDNR message or Insert menu | Confirm the selected folder matches the copy Steam launches, then Repair. Existing REFramework may use the same Insert key; change its menu key to Delete if both overlays open. |
| Need diagnostic files | Use Save logs. The export includes installation health, wrapper errors and available startup logs, even if the host never loaded. Review personal paths before sharing; nothing is sent automatically. |

The [release page](https://github.com/hassanm0301/AMD-NR---OptiScaler-rocm/releases/tag/linux-v0.2.1)
contains the native portable app, the smaller `AMDNR-Linux-v0.2.1.tar.gz` addon for
[advanced command-line use](INSTALL-CLI.md), corresponding vkd3d source, and
`SHA256SUMS`. GitHub's automatic “Source code” archives do not include the prebuilt
bridge binaries. Optional integrity check: `sha256sum --ignore-missing -c SHA256SUMS`.

The app downloads AMDNR 0.3.5.5R4 and ROCm 7.2.4 directly from their publishers,
checks their pinned hashes, and extracts a private runtime. It does not install
system packages, change drivers, or run package installation scripts. Those
components retain their own terms. The native download includes editable launcher
source, Python/Qt notices, and matching Qt/PySide source archives. See
`RUNTIME-LICENSES.md` in its root and `addon/linux/RELEASE-LICENSES.md` for details.
For architecture and GPU tests, see the [technical guide](README.md).
