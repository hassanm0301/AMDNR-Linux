# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-game knowledge: notices before installing, and first-install INI seeds.

Facts come from published AMDNR compatibility reports and this fork's own
Linux testing. Nothing here blocks an installation by itself; notices marked
"confirm" ask the user before files change. Reads are bounded and never follow
symlinks out of the game folder.
"""
import os
from pathlib import Path

# Steam app id -> why AMDNR has nothing to work with. Neural rendering needs a
# temporal upscaler's inputs (colour, depth, motion vectors).
NO_TEMPORAL_UPSCALER = {
    "359320": "Elite Dangerous only offers FSR 1, a spatial upscaler without motion vectors or depth.",
    "2208920": "Assassin's Creed Valhalla only offers FSR 1, a spatial upscaler without motion vectors or depth.",
    "271590": "GTA V Legacy only offers FSR 1, a spatial upscaler without motion vectors or depth.",
    "582160": "Assassin's Creed Origins is DirectX 11 without an upscaler, and the host breaks its startup.",
    "289650": "Assassin's Creed Unity is DirectX 11 without an upscaler, and the host breaks its startup.",
    "1222690": "Dragon Age: Inquisition has no upscaler for the host to use.",
    "1259970": "eFootball PES 2021 (and mods built on it) has no upscaler for the host to use.",
    "489830": "Skyrim Special Edition has no upscaler of its own; only an upscaler mod could provide inputs.",
    "377160": "Fallout 4 has no upscaler of its own; only an upscaler mod could provide inputs.",
    "1328670": "Mass Effect Legendary Edition has no upscaler for the host to use.",
    "374320": "Dark Souls III has no upscaler for the host to use.",
    "814380": "Sekiro has no upscaler for the host to use.",
    "1245620": "Elden Ring has no upscaler of its own; only an upscaler mod could provide inputs.",
}

_RE_ENGINE_PLUGIN = ("Needs REFramework with PureDark's upscaler plugin: the game's own FSR/DLSS options do "
                     "not give the host what it needs.")
NOTES = {
    "3240220": ("Select FSR 3.1 in the game; neural rendering runs on its input. Keep the game's own frame "
                "generation. GPU vendor spoofing stays off for this game (it crashes with ERR_GFX_STATE)."),
    "883710": _RE_ENGINE_PLUGIN,
    "952060": _RE_ENGINE_PLUGIN,
    "2050650": _RE_ENGINE_PLUGIN,
    "1196590": _RE_ENGINE_PLUGIN,
}

# First-install settings, by Steam app id or executable file name (lower case).
SEEDS = {
    # NVIDIA spoofing makes GTA V Enhanced stop with ERR_GFX_STATE.
    "3240220": {("Spoofing", "Dxgi"): "false"},
    # Resident Evil Requiem creates a 0x0 overlay swapchain the host never wraps,
    # so the overlay menu and hotkeys have nothing to draw on. With REFramework,
    # the host's adapter-list thread (0.3.5.1+) deadlocks start-up: REFramework
    # suspends every thread for its integrity scan while that thread is creating
    # a Vulkan instance. The list is only written to amd_bridge.log.
    "re9.exe": {("Menu", "OverlayMenu"): "false", ("DlssNr", "AmdAdapterList"): "false"},
}

# Lower-case names found in protected games' folders.
ANTI_CHEAT = {
    "easyanticheat": "Easy Anti-Cheat", "easyanticheat_eos": "Easy Anti-Cheat",
    "easyanticheat_x64.dll": "Easy Anti-Cheat", "start_protected_game.exe": "Easy Anti-Cheat",
    "battleye": "BattlEye", "beservice_x64.exe": "BattlEye", "beclient_x64.dll": "BattlEye",
    "eaanticheat": "EA Javelin anti-cheat", "eaanticheat.gameservicelauncher.exe": "EA Javelin anti-cheat",
    "gameguard": "nProtect GameGuard", "xigncode": "XIGNCODE3", "pbsvc.exe": "PunkBuster",
    "mhyprot2.sys": "mhyprot", "vgk.sys": "Riot Vanguard", "ace-base64.sys": "ACE anti-cheat",
}
_MAX_ENTRIES = 4000


def _scan(root, depth=3):
    """Yield (relative lower-case path parts, entry) without following links."""
    pending, seen = [(Path(root), ())], 0
    while pending:
        directory, parts = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    seen += 1
                    if seen > _MAX_ENTRIES:
                        return
                    name = entry.name.lower()
                    yield (*parts, name), entry
                    if len(parts) + 1 < depth and entry.is_dir(follow_symlinks=False) and name not in (
                            "shadercache", ".git", "__pycache__"):
                        pending.append((Path(entry.path), (*parts, name)))
        except OSError:
            continue


def _names(directory):
    try:
        with os.scandir(directory) as entries:
            return {entry.name.lower() for entry in entries}
    except OSError:
        return set()


def seeds(game, executable=None):
    settings = dict(SEEDS.get(game.appid, {})) if game.appid else {}
    if executable is not None:
        settings.update(SEEDS.get(Path(executable).name.lower(), {}))
    return settings


def notices(game, managed=False, leftovers=()):
    """Return [{"level": "confirm"|"warning"|"info", "text": ...}] for this game.

    managed: this launcher's installation record exists, so OptiScaler files in
    the folder are ours and not a separate installation.
    leftovers: names Remove leftover AMDNR files takes away; OptiScaler files
    among them are an earlier AMDNR installation's, not a separate one.
    """
    found = []
    if game.appid in NO_TEMPORAL_UPSCALER:
        found.append(dict(level="confirm", text=NO_TEMPORAL_UPSCALER[game.appid] +
                          " Installing is unlikely to do anything."))
    if game.appid in NOTES:
        found.append(dict(level="info", text=NOTES[game.appid]))
    protections = sorted({ANTI_CHEAT[parts[-1]] for parts, _ in _scan(game.install_dir) if parts[-1] in ANTI_CHEAT})
    if protections:
        found.append(dict(level="confirm", text=(
            f"This game ships {', '.join(protections)}. Loaded DLLs can trip anti-cheat in online play and may "
            "lead to a ban; many protected games also refuse proxy DLLs. Use offline modes only.")))
    if game.exe_dir is None:
        return found
    names = _names(game.exe_dir)
    other = {"optiscaler.ini", "optiscaler.dll"} & names - {name.lower() for name in leftovers}
    if not managed and other:
        found.append(dict(level="warning", text=(
            "Another OptiScaler setup appears to be in the game folder (OptiScaler.ini or OptiScaler.dll). Remove "
            "its proxy DLL first: two hosts in one game crash. Its INI is backed up and replaced on install.")))
    if not managed and "dlssnr_on_amd.ini" in names:
        found.append(dict(level="warning", text=(
            "danielblnc's standalone DLSS-NR on AMD seems to be installed here. Remove it before installing: "
            "AMDNR hosts his runtime itself, and both hooking the game conflicts.")))
    if "reshade.ini" in names or "reshade64.ini" in names:
        found.append(dict(level="warning", text=(
            "ReShade is installed. The addon replaces d3d12.dll with its patched graphics library (the original "
            "is backed up), so a ReShade d3d12.dll stops loading.")))
    if "dinput8.dll" in names:
        found.append(dict(level="info", text="A dinput8.dll mod loader (such as REFramework) is present; it is left in place."))
    if "red4ext" in names or ("plugins" in names and "cyber_engine_tweaks" in _names(game.exe_dir / "plugins")):
        found.append(dict(level="info", text=(
            "RED4ext / Cyber Engine Tweaks loaders are present; the addon picks a DLL name they do not use.")))
    if "specialk64.dll" in names:
        found.append(dict(level="info", text="Special K is present. OptiScaler skips it when frame generation is active."))
    return found
