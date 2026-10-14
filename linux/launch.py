#!/usr/bin/env python3
"""Launch a game with the private HIP bridge and ROCm libraries."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import sys
import fcntl
import tempfile

def sha(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()

def prepare_prefix(bundle, prefix):
    # A game may launch more than one process through its launcher. Serialize
    # placeholder creation and ownership recording across those processes.
    with (bundle / "prefixes.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _prepare_prefix(bundle, prefix)

def _prepare_prefix(bundle, prefix):
    record = bundle / "prefixes.json"
    entries = json.loads(record.read_text()) if record.exists() else []
    sources = [bundle / "x86_64-windows/amdhip64_7.dll", bundle.parent / "d3dcompiler_47.dll"]
    for source in sources:
        if not source.is_file():
            raise ValueError(f"Missing prefix support file: {source}")
    for source in sources:
        destination = prefix / "drive_c/windows/system32" / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        expected = sha(source)
        if destination.is_file() and sha(destination) == expected:
            continue
        if any(entry["path"] == str(destination) for entry in entries):
            raise ValueError(f"A managed prefix file changed: {destination}. Restore this install before retrying.")
        if source.name == "amdhip64_7.dll" and (destination.exists() or destination.is_symlink()):
            raise ValueError(f"A different HIP DLL is already installed: {destination}")
        entry = {"path": str(destination), "sha256": expected}
        # The host compiles its shaders through an absolute System32 path.
        # Preserve the prefix's compiler before installing the supplied native one.
        if destination.is_symlink():
            entry["original_link"] = str(destination.readlink())
        elif destination.exists():
            backup = bundle / "prefix-backups" / (hashlib.sha256(str(destination).encode()).hexdigest() + ".dll")
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

def _write_record(record, entries):
    temp = record.with_suffix(".tmp")
    temp.write_text(json.dumps(entries, indent=2) + "\n")
    temp.replace(record)

def restore_prefixes(bundle, force=False):
    record = bundle / "prefixes.json"
    if record.is_file():
        entries = json.loads(record.read_text())
        for entry in entries:
            path = Path(entry["path"])
            if not path.is_absolute() or path.parts[-4:-1] != ("drive_c", "windows", "system32") or path.name not in ("amdhip64_7.dll", "d3dcompiler_47.dll"):
                raise ValueError(f"Invalid prefix file record: {path}")
            backup = None
            if "backup" in entry:
                expected_name = hashlib.sha256(str(path).encode()).hexdigest() + ".dll"
                if entry["backup"] != expected_name:
                    raise ValueError(f"Invalid prefix backup record: {path}")
                backup = bundle / "prefix-backups" / expected_name
                if not backup.is_file() or backup.is_symlink() or sha(backup) != entry["backup_sha256"]:
                    raise ValueError(f"Missing or changed prefix backup: {backup}")
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
    overrides = "amdhip64_7=b;dxgi=n;winmm,d3d12,d3d12core,d3dcompiler_47=n,b"
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
    prepare_prefix(bundle, Path(prefix).resolve())
    os.execvpe(sys.argv[1], sys.argv[1:], env)

if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        sys.exit(f"AMDNR Linux: {error}")
