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

[Installation, update and removal guide](https://github.com/hassanm0301/AMD-NR---OptiScaler-rocm/blob/linux-v0.2.1/linux/INSTALL.md).

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
