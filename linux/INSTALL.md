# AMDNR Linux Manager — native Linux installation

**Download [NR-Linux-Manager-v0.2.1-linux-x86_64.tar.gz](https://github.com/hassanm0301/AMDNR-Linux/releases/tag/linux-v0.2.1), extract it, and open `Start.sh`.**
Python and the native Qt interface are included. No Proton setup is needed for
the launcher itself, and no compiling or system ROCm installation is needed.
If your file manager opens the script as text, choose “Run as program”, or open a
terminal in the extracted folder and run `./Start.sh`.

Development builds can also be packaged as a single `.AppImage` (see
[the build instructions](README.md)). If you have an AppImage, enable **Allow
executing file as program** in its properties and open it. From a terminal:

```sh
chmod +x AMDNR-Linux-Manager-*.AppImage
./AMDNR-Linux-Manager-v0.3.6-x86_64.AppImage
```

If the system cannot mount AppImages through FUSE, add
`--appimage-extract-and-run` to that command. The AppImage uses the same settings,
downloads and installed-game runtime as the portable folder. Updating the
launcher means downloading and opening the newer AppImage.

This independent community launcher installs the tested AMDNR Linux bridge.
AMDNR is by [3zwr1](https://github.com/3zwr1/AMD-NR---OptiScaler). It provides a
familiar game library and installation workflow with an original interface;
it does not include or port the restricted upstream Windows launcher.

## Install a Steam game

1. First check that the game launches and loads your save normally with Proton.
2. Open **AMDNR Linux Manager**. It detects installed Steam games across library
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

Games installed through Heroic or Lutris are listed automatically (see below).
For other non-Steam games, use **Add game** and select the main executable. The
same file installation works, but the app displays a launch option for you to add
to your existing Proton/Wine runner. Only Steam's launch options are set
automatically. Choosing an Unreal game's root launcher (`Game.exe`) selects the
`Game-Win64-Shipping.exe` under `Binaries/Win64` that it starts; point your
runner at that executable too, so it finds `amdnr-linux/run.sh` beside it.

The library loads Steam thumbnails and a banner for the selected game in the
background. Downloaded artwork is cached under the manager's cache directory
in `artwork/` and remains available offline. Heroic, Lutris and manually added
games use the icon embedded in the game's `.exe`, extracted without running the
game. If artwork is missing or cannot be read, a placeholder is shown and
installation still works.

## Install a Heroic or Lutris game (current development builds)

The library also lists Windows games installed through **Heroic** (Epic Games,
GOG, Amazon and sideloaded apps) and **Lutris** (Wine runner), from both native
and Flatpak installs. Their panel is labelled with the launcher and store, for
example **HEROIC / GOG** or **LUTRIS / WINE**. The manager only reads Heroic's and
Lutris' settings and never changes them, so you add the wrapper yourself:

1. Launch the game once from Heroic or Lutris so its prefix exists, then close it.
2. Select the game in the manager. If asked, choose its actual 64-bit game
   executable, then click **Install neural rendering**.
3. Add the wrapper line that the manager shows:
   - **Heroic:** open the game's **Settings → Advanced → Wrapper command**. Put the
     path in **Wrapper**, leave **Arguments** empty, and add it.
   - **Lutris:** right-click the game, choose **Configure → System options**, turn
     on **Advanced**, and add the quoted path at the start of **Command prefix**.
4. Click **Rescan library**. The panel confirms that the launcher starts the game
   through the wrapper. Then launch the game from Heroic or Lutris as usual, or
   click **Launch in Heroic** or **Launch in Lutris** in the manager.

The launch button opens the launcher's own launch link, the handler its
desktop shortcuts also go through:
`heroic://launch/<store>/<app name>` (store `legendary`, `gog`, `nile` or
`sideload`) or `lutris:rungameid/<id>`. Heroic or Lutris then starts the game
with all of its own settings. Lutris, Heroic's Flatpak and Heroic's distribution
packages register these links; a Heroic AppImage does once it is integrated into
your desktop. If clicking the button does nothing, start the game from the
launcher. The button stays disabled, and its tooltip says why, until the
launcher runs this install's wrapper. It is also disabled while Repair is
recommended or an operation needs recovery, just as for Steam games.

The wrapper prepares the prefix the launcher passes to it, just as it uses
Steam's prefix for Steam games: Heroic passes `WINEPREFIX` (for Proton,
`STEAM_COMPAT_DATA_PATH`, whose `pfx` folder is the prefix), and Lutris passes
`WINEPREFIX` from **Game options → Wine prefix**, or the prefix that contains the
game. The panel shows that prefix, and warns when the wrapper would not get one.
In that case set a Wine prefix in Lutris. For a Heroic game run through
CrossOver, choose a Wine or Proton version, or set `WINEPREFIX` under **Settings →
Advanced → Environment variables**.

Lutris entries for store clients such as the EA app or Ubisoft Connect cover a
whole Wine prefix: choose the game's executable inside it. After uninstalling,
or after choosing a different executable, remove or replace the wrapper in
Heroic or Lutris. They cannot start a game through a wrapper that no longer
exists, and the panel warns while they still point to one.

## Choose the neural runtime and plugins (current development builds)

The selected game's panel has a **Neural runtime and plugins** section:

- **lmxxf, built into AMDNR** is the default and the runtime tested in games on
  Linux.
- **danielblnc X.Y.Z** installs DLSS-NR on AMD by Daniel Blanco (danielblnc)
  beside it and selects it. The list shows only versions your GPU and the
  bundled AMDNR host support: RDNA 4 and RDNA 3 cards get 0.4.0 to 0.6.0, RDNA 2
  cards get 0.6.0, and integrated GPUs and handhelds get none. The manager
  downloads the runtime from AMDNR's GitHub release only when you choose it, and
  checks every file against pinned SHA-256 digests. It is not redistributed by
  this fork; all rights stay with its author. On Linux it passes automated GPU
  tests (every listed version, RX 9070 XT) but has no gameplay report yet.
- **OptiPatcher plugin** (OptiScaler project) patches the games it supports so
  they offer upscaler inputs on AMD. It is placed in `OptiScaler/plugins/` beside the game's host DLL
  and enabled with `[Plugins] LoadAsiPlugins=true`.
- The **host build** list (the first list in the section) chooses which
  OptiScaler host is installed. **AMDNR** is the default. **OptiScaler AMD pre-SR 1.9.1-alpha by TheAutomatic** is an
  independent OptiScaler fork (GPL-3.0) that upstream's launcher also offers.
  It runs danielblnc's runtime only, and only danielblnc's own 0.3.1 build
  (RDNA 4 and RDNA 3), so the runtime list changes to that version when you pick
  it. Its own lmxxf backend is not offered because its weights are not published
  anywhere that can be verified. The manager downloads the pinned release from
  TheAutomatic's GitHub, installs the host, its `OptiScaler/` folder, its
  shaders and its licences, and leaves out its Windows Setup/Uninstall scripts.
  It passes the same automated host test on Linux; no game report yet.
- **Model E upscaler** is HelixSR by lonewolf0622, new with AMDNR 0.3.5.6. It
  is a separate upscaler that runs NVIDIA's DLSS network as Direct3D 12 shaders
  and replaces FSR 3.1 / FSR 4 in D3D12 games; it works with either neural
  runtime. Ticking it first shows a notice to accept. The manager then downloads
  HelixSR 1.8.1 from its author's GitHub release, Microsoft's DirectX shader
  compiler from Microsoft's GitHub, and NVIDIA's DLSS 310.7.0 DLL from NVIDIA's
  GitHub (under [NVIDIA's licence](https://github.com/NVIDIA/DLSS/blob/v310.7.0/LICENSE.txt)),
  and runs HelixSR's own setup scripts to build HelixSR's network on this PC.
  This needs **Python 3 with numpy** from your distribution (`python-numpy` or
  `python3-numpy`) and Proton or Wine for the shader compiler; it takes about
  a minute and is done once, then reused for every game. NVIDIA's DLL is deleted
  after the build. The built files contain NVIDIA's network: keep them on your
  PC and do not share or upload them. Model E needs the AMDNR host and an
  RDNA 1 or newer GPU. It is installed as `AMDNR-SR-E/` beside the host and
  selected with `[Upscalers] FfxSRProvider=helix_e`; in the game, choose an FSR
  3.1 / FSR 4 upscaler (or DLSS, which OptiScaler hands to FSR) as usual. On
  Linux it passes the automated host test only; no game report yet.

Choose before **Install**. For an installed game, change the selection and
click **Apply runtime, plugin and upscaler**; this repairs (or updates) the installation
with the new files. Only the keys that belong to the change are rewritten in
`OptiScaler.ini` (`[DlssNr] NrBackend`, `[Plugins] LoadAsiPlugins`, and for
Model E `[Upscalers] FfxSRProvider` and `[Libraries] HelixSRPath`); your other
settings and comments stay. Both runtimes remain installed side by side, so the
in-game **Neural** tab can still switch runtimes; a later Repair keeps that
in-game choice. Uninstall removes the runtime, the plugin and the
`OptiScaler/plugins` folder if it is empty. Changing the host build is the
exception: the two hosts read different settings, so `OptiScaler.ini` starts
again from the new host's own file (plus the game's first-install settings),
and your previous file is kept in the manager's state folder under `configs/`.

The panel also shows notes about the game before you install: games without a
temporal upscaler (nothing for neural rendering to use), anti-cheat software
(use offline modes only), another OptiScaler, a standalone danielblnc install or
ReShade in the folder, and per-game setup notes. Installing a game with a
blocking note asks for confirmation. A few games get settings on their first
install: GTA V Enhanced keeps GPU vendor spoofing off (it crashes with
`ERR_GFX_STATE`), and Resident Evil Requiem uses the classic menu
(`[Menu] OverlayMenu=false`) because its overlay swapchain is never wrapped.

## Choose a loading DLL (current development builds)

Expand **Advanced compatibility** in the selected game's panel. Leave the choice
on **Automatic** for a new installation: it prefers a name the game is known to
load (Cyberpunk 2077: `wininet.dll`, whose mod loaders hold `winmm.dll` and
`version.dll`; Marvel's Spider-Man: `dbghelp.dll`), then a supported DLL imported
by the selected executable, and skips names occupied by other files. With multiple
executables in one folder, use **Choose executable** to identify the main game.

You can select **winmm.dll**, **version.dll**, **dbghelp.dll**, **wininet.dll**, or
**winhttp.dll** before installing. On an installed game, **Use selected DLL**
changes the choice; **Try next DLL** selects an untried, unoccupied name. Close
the game and Steam first, then launch the game again to check whether OptiScaler
opens. After all free names have been tried, select an earlier name manually to
retry it. The manager does not automatically launch games to test a choice.

Switching uses the installed host without downloading it again. It updates the
DLL, Wine configuration and installation record together, with rollback on
failure. Repair and Update keep the chosen name and trial history. Existing
installations using the old wrapper need Repair with a current package first.
These controls are not included in the older v0.2.1 download linked above.

`dxgi.dll`, `d3d11.dll`, `d3d12.dll` and `d3d12core.dll` remain reserved for DXVK
and vkd3d; they are not OptiScaler choices. All five offered names passed isolated
loading and representative API forwarding checks with the pinned AMDNR 0.3.5.5R4
host under GE-Proton11-9 and Proton-Wineland 11.0. A successful DLL probe does not
establish that a particular game loads that DLL or runs neural rendering.

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
- **Remove leftover AMDNR files** takes the place of Uninstall when a game has
  no installation record but AMDNR files are still there: logs, crash dumps and
  session markers from earlier game sessions, or a whole installation whose
  `.amdnr-linux-install.json` was lost. It lists every change and asks first.
  It puts originals back from `.amdnr-linux-backup` and the Proton prefix, takes
  the AMDNR wrapper out of Steam's launch options, and removes only files it can
  show are AMDNR's: this addon's own folders, and files that name AMDNR (the host
  DLL, `OptiScaler.ini`, the host's logs). Finished `AMDNR-report-*.zip` reports,
  other mods (including another OptiScaler build) and files it cannot attribute
  stay where they are; the list names them and says why.
  A mod manager that deploys with links (such as Amethyst) can take over an
  installation: it moves AMDNR's files into its own folder (Amethyst's
  `overwrite`) and links them back, and the manager then reports the record as a
  link instead of an installation. Remove leftover AMDNR files removes such a
  link together with the file it points to, so the mod manager has nothing to
  put back. It does this only for a link to a file with the same relative path in
  the other folder that passes the same checks; the list names that folder. Other
  links stay. That folder can also hold a copy of a file that is still an
  ordinary file in the game folder, which the mod manager would link back on its
  next deploy. Such a copy is deleted in the same pass when it has the same
  relative path and passes the same checks as the game file; one cleanup is
  enough. Copies in a folder no AMDNR link points into stay.

A failed or interrupted operation retains recovery information. With the game
and Steam closed, repeat the action to recover it before continuing. If a file,
backup or Steam launch option was edited separately, the app may stop and explain
the conflict instead of erasing that edit. Keep the game's `.amdnr-linux-backup`,
`.amdnr-linux-install.json`, and `amdnr-linux` folders until removal completes.

The manager stores settings, Steam backups, recovery journals, saved
configurations and a small shared Python runtime in
`~/.local/share/nr-linux-manager` (or `$XDG_DATA_HOME/nr-linux-manager`). Downloads
are cached under `~/.cache/nr-linux-manager` (or `$XDG_CACHE_HOME/nr-linux-manager`).
These storage paths retain their original names for compatibility with existing
installations. Older release archives also retain their published filenames;
new launcher builds are branded AMDNR Linux Manager.

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
- **Automated only:** danielblnc's runtime (0.4.0–0.6.0) passes the released
  host's NGX/FSR2 pipeline test on RX 9070 XT, with every GPU job completed and a
  material change from the NR-off image. The same test passes with TheAutomatic's
  pre-SR 1.9.1-alpha host and danielblnc 0.3.1. Model E (HelixSR) passes the
  host's FSR 3.1 pipeline test, alone and with lmxxf, with its network running
  on every frame. No game has been reported yet for any of these.
- **Not validated:** other GPUs, Steam Deck, Flatpak Steam, other game runners,
  frame generation and Ray Regeneration. AMDNR Anywhere is not supported on Linux:
  its capture host relies on Windows window capture that Wine does not provide.
  Flatpak can detect a library while still denying access to the host runtime;
  this app does not alter Flatpak permissions.

Enable a supported upscaler in the game's graphics settings. **Insert** opens
OptiScaler; **Home** toggles the neural effect. The prepared package selects the
**lmxxf** backend unless you chose danielblnc's runtime. A banner alone does not
prove neural inference is running. With danielblnc's runtime, look instead for
`Completed AMD pre-SR passes=` in `amd_presr.log` and `network job … done` lines
in `dlssnr_on_amd.log`. With Model E, `OptiScaler.log` shows
`[SR_PROVIDER] requested=helix_e ready=true actual=HelixSR` and
`AMDNR-SR-E/helixsr.log` shows `network running`; Save logs includes both.
Look in `lmxxf_backend.log` beside the executable for `hip=1`, successful network
initialization, and increasing **model** and **fresh composition** counts. The
successful Stellar Blade run (AMDNR 0.3.5.5R4) recorded 3,599 model frames and
3,594 fresh outputs, with temporal history and all 71 network blocks active.
Rendering has a substantial GPU cost; the measured neural kernel took about
11–13 ms per frame.

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
| Game does not start at all after installing | The wrapper stopped before the game. The manager shows its reason above the game's status, and `amdnr-linux/launch.log` keeps it. "A different HIP DLL is already installed" names a file in the prefix's `system32` that is not AMDNR's; move it away and launch again. A bridge an earlier AMDNR release left there is renamed to `….bak` instead. |
| Wrapper says its Python runtime is missing | Open the complete portable launcher and Repair the game. |
| Saves seem missing | Launch through the original Steam entry and prefix. Do not directly run the `.exe`. |
| Game crashes or no neural frames appear | Check Proton version, GPU access and compiler; compare with `[DlssNr] Enabled=false` in `OptiScaler.ini`. |
| Resident Evil Requiem stays black at startup | Check `re2_framework_log.txt`. If it ends at `Scanning DD2...` after suspending threads, see the REFramework note below. |
| No AMDNR message or Insert menu | Confirm the selected folder matches the copy Steam launches, then Repair. Existing REFramework may use the same Insert key; change its menu key to Delete if both overlays open. |
| Neural rendering toggles but the picture never changes; `amd_bridge.log` says `amdhip64_7.dll could not be loaded (Win32 error 126)` | The HIP bridge is missing from the prefix's `system32`. Proton-CachyOS deletes the name earlier releases used; Update or Repair the game so the wrapper stores it as `AMDHIP64_7.DLL`. |
| Unreal Engine 4 game shows no AMDNR menu | Many UE4 games start in DX11. Add `-dx12` to the launch options, or add `[D3DRHIPreference]` with `bUseD3D12InGame=True` to the game's `Saved/Config/WindowsNoEditor/GameUserSettings.ini`. |
| Need diagnostic files | Use Save logs. The export includes installation health, wrapper errors and available startup logs, even if the host never loaded. Review personal paths before sharing; nothing is sent automatically. |

Resident Evil Requiem requires REFramework with OptiScaler. Older REFramework
builds can suspend a Wine thread while it holds a lock, leaving startup stuck
at `[IntegrityCheckBypass]: Scanning DD2...`. That signature was observed locally
with the April 26, 2026 REFramework build. Upstream fixed the matching Proton
deadlock in **nightly 01422** (September 12, 2026); see
[REFramework issue #1827](https://github.com/praydog/REFramework/issues/1827).
With the game closed, back up `dinput8.dll` and replace it with the one from the
[official REFramework nightly](https://github.com/praydog/REFramework-nightly/releases).
Keep your existing settings, mods and Proton prefix, then retry through Steam.
The upstream fix was validated in Onimusha; getting past this startup hang does
not by itself establish RE9 neural rendering compatibility. Current development
builds include `re2_framework_log.txt` in Save logs; older launchers require
collecting that file separately.

The [release page](https://github.com/hassanm0301/AMDNR-Linux/releases/tag/linux-v0.2.1)
contains the native portable app, the smaller `AMDNR-Linux-v0.2.1.tar.gz` addon for
[advanced command-line use](INSTALL-CLI.md), corresponding vkd3d source, and
`SHA256SUMS`. GitHub's automatic “Source code” archives do not include the prebuilt
bridge binaries. Optional integrity check: `sha256sum --ignore-missing -c SHA256SUMS`.

The app downloads AMDNR 0.3.5.6 (0.3.5.5R4 before v0.3.1-preview) and ROCm 7.2.4 directly from their publishers,
checks their pinned hashes, and extracts a private runtime. It does not install
system packages, change drivers, or run package installation scripts. Those
components retain their own terms. The native download includes editable launcher
source, Python/Qt notices, and matching Qt/PySide source archives. See
`RUNTIME-LICENSES.md` in its root and `addon/linux/RELEASE-LICENSES.md` for details.
For architecture and GPU tests, see the [technical guide](README.md).
