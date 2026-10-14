# Linux Neural Rendering v0.1.0 (experimental)

The first prebuilt Linux release of this fork runs AMDNR's **lmxxf neural
rendering on Linux through Proton and native ROCm**. It includes the PE/Unixlib
bridge that fixes Stellar Blade's startup crash, shared D3D12/ROCm buffer and
fence support, and the Wine signal compatibility helper.

**Validated:** Radeon RX 9070 XT, Arch Linux, RADV/Mesa 26.2.4,
GE-Proton11-9 and Steam Runtime 4. Stellar Blade loaded an existing Steam save
and showed normal gameplay with real neural rendering: 3,599 model frames and
3,594 fresh composed outputs, temporal history and all 71 network blocks active.
Automated GPU inference, shared-buffer/fence, exception, and direct-Windows-call
regression checks passed. The Witcher 3 DX12 also passed an earlier bridge build
with Proton-Wineland; that game has not been retested with this release.

These are short tests. Other GPUs, games, distributions, older Proton, Steam Deck,
Flatpak, and long-duration stability remain unvalidated. Frame generation, Ray
Regeneration, AMDNR Anywhere and the danielblnc backend are outside this release's
tested scope.

## Install

**Read the [complete installation guide](https://github.com/hassanm0301/AMD-NR---OptiScaler-rocm/blob/linux-v0.1.0/linux/INSTALL.md)**
for requirements, exact game directories, troubleshooting and removal.

1. Confirm your DX12 game already works with **GE-Proton11-9**, then close it.
   Remove conflicting graphics-injection mods and keep your configuration backups.
2. Download **AMDNR-Linux-v0.1.0.tar.gz** and **SHA256SUMS** below. Check and extract:

   ```sh
   sha256sum --ignore-missing -c SHA256SUMS
   tar -xzf AMDNR-Linux-v0.1.0.tar.gz
   cd AMDNR-Linux-v0.1.0
   ```

3. Prepare the package using a **native x64 Microsoft `d3dcompiler_47.dll`** from
   your own game (tested version: 10.0.14393.795):

   ```sh
   python3 linux/setup.py prepare --compiler "/path/to/d3dcompiler_47.dll"
   ```

   Requires Python 3.12+, binutils (`ar`), internet and about 3 GB free space.
   Downloads the checksum-pinned AMDNR **0.3.5.5R4** and private **ROCm 7.2.4**
   from their original publishers. No compiling, root, or system ROCm install.

4. Install beside the actual game executable. For **Stellar Blade**, this is
   `SB/Binaries/Win64` inside its Steam installation:

   ```sh
   python3 linux/manage.py install linux/build/package "/path/to/StellarBlade/SB/Binaries/Win64"
   ```

5. Paste the printed wrapper path into Steam's launch options:

   ```text
   "/path/to/StellarBlade/SB/Binaries/Win64/amdnr-linux/run.sh" %command%
   ```

   Existing working wrappers can stay, for example `MANGOHUD=1 gamemoderun`
   before the quoted path. **Launch from Steam**, using the original prefix
   and saves. Enable a supported in-game upscaler. Insert opens OptiScaler;
   Home is the default neural toggle. Check increasing model/fresh composition
   counts in `lmxxf_backend.log` to verify actual neural rendering.

## Remove or upgrade

Close the game, remove the wrapper from Steam's launch options, then:

```sh
python3 linux/manage.py uninstall "/path/to/game/executable/directory"
```

Uninstall restores the backed-up game and prefix files. Save any changed managed
files before using `--force` if removal reports modifications. Always uninstall
the old package before installing an update; keep its backup and prefix journal.

## Assets and credits

- **AMDNR-Linux-v0.1.0.tar.gz:** tested Linux binaries, preparation/installation
  tools, bridge source, instructions and notices. Start here.
- **AMDNR-Linux-v0.1.0-vkd3d-source.tar.gz:** complete corresponding patched
  vkd3d-proton source, recursive dependencies, licenses and rebuild instructions.
  Optional for users, provided for rebuilding and license compliance.
- **INSTALL.md:** standalone copy of the guide.
- **SHA256SUMS:** checksums of the release downloads.

The release add-on does **not** redistribute the upstream host/model/runtime,
Microsoft compiler, ROCm binaries, game files, or user data. The setup helper
retrieves pinned upstream components directly and preserves their notices.
GitHub's automatic “Source code” downloads do not contain prebuilt binaries.

AMDNR is by **3zwr1**, based on **OptiScaler**; the neural implementation is by
**lmxxf/Kien**. This is an independent modified Linux fork using **Wine/Proton**,
**vkd3d-proton**, and **AMD ROCm**. See
[release notices](https://github.com/hassanm0301/AMD-NR---OptiScaler-rocm/blob/linux-v0.1.0/linux/RELEASE-LICENSES.md)
for source and license details.
