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
