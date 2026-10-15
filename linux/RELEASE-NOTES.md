# Linux v0.2.0 — Native launcher

**Download `NR-Linux-Manager-v0.2.0-linux-x86_64.tar.gz`, extract it, and open `Start.sh`.**

NR Linux Manager runs natively on Linux and includes Python and Qt. Select a Steam
game, close the game and Steam, and click **Install neural rendering**. The app
finds a shader compiler, downloads verified runtime files, backs up affected files,
and configures Steam's launch options. Then launch through Steam with your existing
Proton version, prefix and saves.

- Steam library discovery across drives, search, and manual executable selection.
- Install, Repair, Update, and Uninstall with original-file restoration.
- Existing MangoHud/GameMode options and game arguments preserved where safely parseable.
- Configuration preserved during repair/update; logs can be exported locally.
- Recovery journals for interruptions, original prefix-file restoration, and
  protection against silently overwriting later Steam settings or downgrading packages.
- Portable shared Python runtime for installed games, independent of the extracted app folder.

This is an independent community launcher with an original library/details interface.
It uses no code or visual assets from the restricted upstream Windows launcher.
AMDNR is by [3zwr1](https://github.com/3zwr1/AMD-NR---OptiScaler).

## Installation and compatibility

[Read the installation guide](https://github.com/hassanm0301/AMD-NR---OptiScaler-rocm/blob/linux-v0.2.0/linux/INSTALL.md).

The rendering binaries are unchanged from the game-tested Linux v0.1.0 release:
RX 9070 XT, Stellar Blade, GE-Proton11-9, existing save and normal gameplay image,
with 3,599 model frames / 3,594 fresh neural outputs. The Witcher 3 DX12 passed an
earlier bridge build; it has not been retested with this exact package.

Game compatibility remains experimental. Other GPUs, Steam Deck, Flatpak Steam,
other distributions and long-duration stability are not validated. The bridge
requires Wine 11-based Proton and usable AMD GPU compute devices. The native app
requires x86_64 Linux, glibc 2.34+, and normal X11/Wayland desktop libraries.

The launcher automatically looks for a native x64 `d3dcompiler_47.dll` in installed
games. If none is found, select one from a game you own. AMDNR 0.3.5.5R4 and ROCm
7.2.4 are downloaded directly from their publishers with pinned checksums. No
system ROCm installation, compiling, binutils, or administrator access is needed.

**Testing:** automated fixture coverage exercises discovery, file and Steam
transactions, hard-interruption recovery, update protection and GUI actions.
The portable app has been rendered under X11, Wayland and offscreen. A real-asset
installation/repair/update/removal test uses an isolated game/prefix and verifies
original files and saves are restored. No new gameplay compatibility is claimed.

## Assets

- **NR-Linux-Manager-v0.2.0-linux-x86_64.tar.gz** — recommended native portable app,
  editable launcher source, Python/Qt runtime and matching Qt/PySide sources.
- **AMDNR-Linux-v0.2.0.tar.gz** — smaller addon for advanced command-line installation.
- **AMDNR-Linux-v0.2.0-vkd3d-source.tar.gz** — corresponding graphics source and pinned submodules.
- **INSTALL.md** — installation, update, removal and troubleshooting guide.
- **SHA256SUMS** — checksums for all release assets.

Upstream model/runtime, Microsoft compiler, game files, saves, personal settings
and logs are not redistributed. Included and downloaded components retain their
respective licenses. See the runtime notices and `linux/RELEASE-LICENSES.md`.
