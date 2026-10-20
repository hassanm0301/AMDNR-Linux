#!/usr/bin/env python3
"""Launch a game with the private HIP bridge and ROCm libraries."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import shutil
import sys
import fcntl
import tempfile
from proxies import read_config

# Proton-CachyOS deletes system32/amdhip64_7.dll on every start unless its ZLUDA
# mode is on. Wine finds DLL files whatever their case, and Proton checks only
# the lowercase name, so the bridge goes into System32 under this name.
PREFIX_HIP = "AMDHIP64_7.DLL"
# Earlier wrappers used the lowercase name; their journal entries are retired.
LEGACY_PREFIX_HIP = "amdhip64_7.dll"
PREFIX_NAMES = (PREFIX_HIP, LEGACY_PREFIX_HIP, "d3dcompiler_47.dll")
# The bridges published releases installed in System32, by SHA-256. A game
# folder removed without Uninstall takes its journal with it and leaves its
# bridge in the prefix: such a file is an earlier copy of ours, not a foreign
# HIP runtime. Add each release's bridge here before publishing it.
RELEASED_HIP = {
    "f25e4bb523accceef3b77c809159f61cc1139f4b2d2f69fadb2290bfa99b3287": "v0.1.0 to v0.2.1",
    "be0a5019356bce110202f2d611178709f8d7f43ae0637836400a85e97dae165f": "v0.2.2-preview",
    "418e79e67704322e1ca73c17d82f473e02b23bbb8c72594365484123d0877e7c": "v0.3.0-preview to v0.3.4-preview",
}

def launch_status(bundle, message):
    """Keep the last wrapper result when Steam hides its terminal output."""
    try:
        destination = bundle / "launch.log"
        with tempfile.NamedTemporaryFile(mode="w", dir=bundle, delete=False, encoding="utf-8") as output:
            pending = Path(output.name)
            output.write(f"{datetime.now(timezone.utc).isoformat()}\n{message}\n")
        try:
            pending.replace(destination)
        finally:
            pending.unlink(missing_ok=True)
    except OSError:
        pass  # A diagnostic write must not prevent the game from launching.

def sha(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()

def prepare_prefix(bundle, prefix):
    # A game may launch more than one process through its launcher. Serialize
    # placeholder creation and ownership recording across those processes.
    with (bundle / "prefixes.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _prepare_prefix(bundle, Path(prefix).expanduser().resolve())

def _prefix_entries(record):
    if record.is_symlink():
        raise ValueError(f"Prefix ownership record must not be a symlink: {record}")
    if not record.exists():
        return []
    try:
        entries = json.loads(record.read_text())
        if not isinstance(entries, list):
            raise ValueError()
        seen = set()
        for entry in entries:
            path = Path(entry["path"])
            if (not path.is_absolute() or ".." in path.parts or "\0" in entry["path"] or str(path) != entry["path"] or
                    path.parts[-4:-1] != ("drive_c", "windows", "system32") or
                    path.name not in PREFIX_NAMES or str(path) in seen or
                    not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])):
                raise ValueError()
            seen.add(str(path))
            if "backup" in entry and (entry["backup"] != hashlib.sha256(str(path).encode()).hexdigest() + ".dll" or
                                      not re.fullmatch(r"[0-9a-f]{64}", entry["backup_sha256"])):
                raise ValueError()
            if "original_link" in entry and (not isinstance(entry["original_link"], str) or
                                              "\0" in entry["original_link"] or "backup" in entry):
                raise ValueError()
        return entries
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Invalid prefix ownership record: {record}. Preserve it for recovery.") from error

def _prefix_backup(bundle, entry):
    backup = bundle / "prefix-backups" / entry["backup"]
    if (backup.parent.is_symlink() or backup.is_symlink() or not backup.is_file() or
            sha(backup) != entry["backup_sha256"]):
        raise ValueError(f"Missing or changed prefix backup: {backup}")
    return backup

def _check_prefix_parents(path):
    if any(parent.is_symlink() for parent in path.parents):
        raise ValueError(f"Prefix file parent became a symlink: {path}")

def _set_aside_released_hip(path, notes):
    """Move a bridge an earlier release left, without its journal, out of Wine's way.

    It stays beside the bridge under a name Wine never loads, so it can be
    renamed back. Setting the same bridge aside again keeps the first copy.
    Anything else is a HIP DLL that is not ours, and is refused.
    """
    digest = sha(path) if path.is_file() and not path.is_symlink() else None
    if digest not in RELEASED_HIP:
        raise ValueError(f"A different HIP DLL is already installed: {path}")
    aside = path.with_name(f"{path.name}.{digest[:16]}.bak")
    if aside.exists() or aside.is_symlink():
        if aside.is_symlink() or not aside.is_file() or sha(aside) != digest:
            raise ValueError(f"Cannot set aside the HIP bridge of an earlier AMDNR Linux release: {aside} exists")
        path.unlink()
    else:
        path.rename(aside)
    notes.append(f"Set aside the HIP bridge an earlier AMDNR Linux release ({RELEASED_HIP[digest]}) "
                 f"left without its install record: {aside}")

def _retire_legacy_hip(record, entries, legacy, current, expected, notes):
    """Remove the lowercase bridge an earlier wrapper installed, and its entry.

    Wine loads the exact-case name first, so nothing else may stay under the
    lowercase name: an earlier release's bridge whose journal is lost is set
    aside, and a different HIP DLL is refused. A copy identical to this bridge
    belongs to another installation sharing the prefix and stays.
    """
    _check_prefix_parents(legacy)
    entry = next((entry for entry in entries if entry["path"] == str(legacy)), None)
    if entry is not None and ("backup" in entry or "original_link" in entry):
        raise ValueError(f"Invalid prefix ownership record: {record}. Preserve it for recovery.")
    regular = legacy.is_file() and not legacy.is_symlink()
    # A case-insensitive folder shows one file under both names.
    same = regular and current.is_file() and not current.is_symlink() and legacy.samefile(current)
    present = (legacy.exists() or legacy.is_symlink()) and not same
    digest = sha(legacy) if present and regular else None
    ours = digest is not None and entry is not None and digest == entry["sha256"]
    if ours:
        legacy.unlink()
    elif present and digest != expected:
        _set_aside_released_hip(legacy, notes)
    if entry is not None:
        entries = [item for item in entries if item is not entry]
        if same and not any(item["path"] == str(current) for item in entries):
            entries.append(dict(entry, path=str(current)))  # still this installation's file
        _write_record(record, entries)
    return entries

def _prepare_prefix(bundle, prefix):
    """Install the prefix support files and return what was done to files not ours."""
    notes = []
    record = bundle / "prefixes.json"
    entries = _prefix_entries(record)
    sources = [(bundle / "x86_64-windows/amdhip64_7.dll", PREFIX_HIP),
               (bundle.parent / "d3dcompiler_47.dll", "d3dcompiler_47.dll")]
    for source, _ in sources:
        if not source.is_file():
            raise ValueError(f"Missing prefix support file: {source}")
    system32 = prefix / "drive_c/windows/system32"
    entries = _retire_legacy_hip(record, entries, system32 / LEGACY_PREFIX_HIP, system32 / PREFIX_HIP,
                                 sha(sources[0][0]), notes)
    for source, name in sources:
        destination = system32 / name
        _check_prefix_parents(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        expected = sha(source)
        entry = next((entry for entry in entries if entry["path"] == str(destination)), None)
        if entry is not None:
            if entry["sha256"] != expected:
                raise ValueError(f"A managed prefix support file changed: {source}. Repair this install before retrying.")
            if "backup" in entry:
                _prefix_backup(bundle, entry)
            if destination.is_file() and not destination.is_symlink() and sha(destination) == expected:
                continue
            # The journal is saved before replacement. Resume an interrupted
            # copy only if the original remains intact, or the target is absent.
            original = (destination.is_symlink() and "original_link" in entry and
                        str(destination.readlink()) == entry["original_link"]) or (
                        "backup" in entry and destination.is_file() and not destination.is_symlink() and
                        sha(destination) == entry["backup_sha256"])
            if (destination.exists() or destination.is_symlink()) and not original:
                raise ValueError(f"A managed prefix file changed: {destination}. Restore this install before retrying.")
        else:
            if destination.is_file() and sha(destination) == expected:
                continue
            if name == PREFIX_HIP and (destination.exists() or destination.is_symlink()):
                _set_aside_released_hip(destination, notes)
            entry = {"path": str(destination), "sha256": expected}
            # The host compiles its shaders through an absolute System32 path.
            # Preserve its compiler before installing the supplied native one.
            if destination.is_symlink():
                entry["original_link"] = str(destination.readlink())
            elif destination.exists():
                backup = bundle / "prefix-backups" / (hashlib.sha256(str(destination).encode()).hexdigest() + ".dll")
                if backup.parent.is_symlink() or backup.is_symlink():
                    raise ValueError(f"Prefix backup must not be a symlink: {backup}")
                backup.parent.mkdir(exist_ok=True)
                shutil.copy2(destination, backup)
                entry.update(backup=backup.name, backup_sha256=sha(backup))
            entries.append(entry)
            _write_record(record, entries)
        # Atomic replacement never follows a destination symlink and leaves the
        # original intact if copying fails. The journal precedes the replacement.
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temp:
            pending = Path(temp.name)
        try:
            shutil.copy2(source, pending)
            pending.replace(destination)
        finally:
            pending.unlink(missing_ok=True)
    return notes

def _write_record(record, entries):
    with tempfile.NamedTemporaryFile(mode="w", prefix=".prefixes-", dir=record.parent,
                                     encoding="utf-8", delete=False) as output:
        pending = Path(output.name)
        try:
            output.write(json.dumps(entries, indent=2) + "\n")
            output.flush()
            os.fsync(output.fileno())
            pending.replace(record)
            directory = os.open(record.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            pending.unlink(missing_ok=True)

def restore_prefixes(bundle, force=False):
    record = bundle / "prefixes.json"
    entries = _prefix_entries(record)
    if record.is_file():
        for entry in entries:
            path = Path(entry["path"])
            _check_prefix_parents(path)
            backup = None
            if "backup" in entry:
                backup = _prefix_backup(bundle, entry)
            already_restored = (backup and path.is_file() and not path.is_symlink() and sha(path) == entry["backup_sha256"]) or (
                "original_link" in entry and path.is_symlink() and str(path.readlink()) == entry["original_link"])
            if already_restored:
                continue
            if (path.exists() or path.is_symlink()) and (path.is_symlink() or not path.is_file() or sha(path) != entry["sha256"]) and not force:
                raise ValueError(f"Prefix file modified since install: {path}. Save it before using --force.")
        for entry in entries:
            path = Path(entry["path"])
            if "original_link" in entry:
                path.unlink(missing_ok=True)
                path.symlink_to(entry["original_link"])
            elif "backup" in entry:
                if path.is_symlink():
                    path.unlink()
                shutil.copy2(bundle / "prefix-backups" / entry["backup"], path)
            else:
                path.unlink(missing_ok=True)
        record.unlink()
        backups = bundle / "prefix-backups"
        if backups.exists():
            shutil.rmtree(backups)
    (bundle / "prefixes.lock").unlink(missing_ok=True)

def environment(bundle, original):
    env = original.copy()
    root = bundle.resolve()
    proxy = read_config(root).removesuffix(".dll")
    legacy = root / "x86_64-unix/amdhip64_7.dll.so"
    if legacy.exists() or legacy.is_symlink():
        raise ValueError("Legacy ELF HIP bridge found. Reinstall the current PE/Unixlib package before launching.")
    rocm = root / "rocm/lib"
    if not rocm.is_dir():
        rocm = Path(env.get("ROCM_PATH", "/opt/rocm")) / "lib"
    if not (rocm / "libamdhip64.so.7").is_file():
        raise ValueError(f"ROCm 7 runtime missing: {rocm}")
    guard = root / "x86_64-unix/libamdnr_signal_guard.so"
    if not guard.is_file():
        raise ValueError(f"Wine signal compatibility library missing: {guard}")
    env["AMDNR_HIP_LIBRARY"] = str(rocm / "libamdhip64.so.7")
    env["LD_LIBRARY_PATH"] = str(rocm) + ":" + str(guard.parent) + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
    # Use a library name: LD_PRELOAD splits absolute paths on spaces, which
    # would break games installed in e.g. "The Witcher 3". LD_LIBRARY_PATH
    # preserves spaces. Keep existing overlay/instrumentation preloads.
    env["LD_PRELOAD"] = guard.name + (":" + env["LD_PRELOAD"] if env.get("LD_PRELOAD") else "")
    env["WINEDLLPATH"] = str(root) + (":" + env["WINEDLLPATH"] if env.get("WINEDLLPATH") else "")
    # vkd3d-proton devices require DXVK's DXGI. Wine's built-in DXGI uses
    # Wine vkd3d's different device ABI and crashes when creating a swapchain.
    overrides = f"amdhip64_7=b;dxgi=n;{proxy},d3d12,d3d12core,d3dcompiler_47=n,b"
    env["WINEDLLOVERRIDES"] = (env["WINEDLLOVERRIDES"] + ";" if env.get("WINEDLLOVERRIDES") else "") + overrides
    return env

def main():
    bundle = Path(__file__).resolve().parent
    if len(sys.argv) < 2:
        raise ValueError("Use amdnr-linux/run.sh %command% in Steam, or pass a Wine/Proton command.")
    prefix = os.environ.get("WINEPREFIX")
    if os.environ.get("STEAM_COMPAT_DATA_PATH"):
        prefix = str(Path(os.environ["STEAM_COMPAT_DATA_PATH"]) / "pfx")
    if not prefix:
        raise ValueError("STEAM_COMPAT_DATA_PATH or WINEPREFIX must identify this game's prefix.")
    env = environment(bundle, os.environ)
    notes = prepare_prefix(bundle, Path(prefix).resolve())
    launch_status(bundle, f"Wrapper prepared. Handing control to the game runner.\n"
                         f"Game directory: {bundle.parent}\nPrefix: {Path(prefix).resolve()}\n"
                         + "".join(note + "\n" for note in notes) +
                         "This confirms wrapper setup only; neural rendering is not yet verified.")
    os.execvpe(sys.argv[1], sys.argv[1:], env)

if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        launch_status(Path(__file__).resolve().parent, f"Launch setup failed: {error}")
        sys.exit(f"AMDNR Linux: {error}")
