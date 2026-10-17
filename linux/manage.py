#!/usr/bin/env python3
"""Stage, install, and remove the Linux neural-rendering package with rollback."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import zipfile
from launch import restore_prefixes

ROOT = Path(__file__).resolve().parent
RELEASES = {
    "013081a3a225ef927d96d8996b64910d62f796022b20bef6a6ea102670b0cb63": "0.3.4.2",
    "725cef9da0608621fe3233a7127e6511b9543061fcecaba8348b2c55c608d448": "0.3.5.5R4",
    "e7ffc09696f1a4e06d26dc0c228d15a9c74a0e56e3d24be6a130a0f5e49968d8": "0.3.5.6",
}
RECORD = ".amdnr-linux-install.json"
BACKUP = ".amdnr-linux-backup"

def sha(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()

def safe_path(root, relative):
    p = PurePosixPath(relative)
    if p.is_absolute() or not p.parts or any(x in ("..", "") for x in p.parts) or "\\" in relative:
        raise ValueError(f"Unsafe package path: {relative}")
    result = root.joinpath(*p.parts)
    if result.is_symlink() or any(x.is_symlink() for x in result.parents if x != root and root in x.parents):
        raise ValueError(f"Symlink in installation path: {result}")
    return result

def write_json(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n")
    temp.replace(path)

def stage(archive, compiler, output, build, rocm):
    if output.exists():
        raise ValueError(f"Output already exists: {output}")
    release_sha = sha(archive)
    version = RELEASES.get(release_sha)
    if not version:
        raise ValueError("Expected a pinned, unmodified AMDNR release archive; checksum mismatch.")
    release_url = f"https://github.com/3zwr1/AMD-NR---OptiScaler/releases/download/Alpha{version}/AMDNR-v{version}.zip"
    inputs = [compiler, build / "d3d12.dll", build / "d3d12core.dll",
              build / "x86_64-unix/amdhip64_7.so", build / "x86_64-windows/amdhip64_7.dll",
              build / "x86_64-unix/libamdnr_signal_guard.so"]
    for path in inputs:
        if not path.is_file():
            raise ValueError(f"Missing build input: {path}")
    if compiler.read_bytes()[:2] != b"MZ":
        raise ValueError("The shader compiler must be a Windows DLL.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="amdnr-stage-", dir=output.parent) as temp:
        target = Path(temp) / "package"
        target.mkdir()
        with zipfile.ZipFile(archive) as package:
            for entry in package.infolist():
                if entry.is_dir():
                    continue
                name = entry.filename
                if name not in ("OptiScaler.dll", "OptiScaler.ini", "LmxxfNrRuntime.dll", "LmxxfNrRuntime.pak") and not name.startswith(("OptiScaler/", "Licenses/")):
                    continue
                name = "winmm.dll" if name == "OptiScaler.dll" else name
                destination = safe_path(target, name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with package.open(entry) as source, destination.open("wb") as sink:
                    shutil.copyfileobj(source, sink)
        for name in ("d3d12.dll", "d3d12core.dll"):
            shutil.copy2(build / name, target / name)
        shutil.copy2(compiler, target / "d3dcompiler_47.dll")
        bundle = target / "amdnr-linux"
        for arch, name in (("x86_64-unix", "amdhip64_7.so"), ("x86_64-windows", "amdhip64_7.dll")):
            (bundle / arch).mkdir(parents=True)
            shutil.copy2(build / arch / name, bundle / arch / name)
        shutil.copy2(build / "x86_64-unix/libamdnr_signal_guard.so", bundle / "x86_64-unix/libamdnr_signal_guard.so")
        for name in ("run.sh", "launch.py"):
            shutil.copy2(ROOT / name, bundle / name)
        (bundle / "run.sh").chmod(0o755)
        if rocm:
            if not (rocm / "lib/libamdhip64.so.7").is_file():
                raise ValueError("Private ROCm runtime is incomplete.")
            shutil.copytree(rocm, bundle / "rocm")
        # Preserve upstream comments and all unrelated configuration.
        ini = target / "OptiScaler.ini"
        lines = ini.read_text(encoding="utf-8-sig").splitlines()
        section = ""
        settings = {("DlssNr", "Enabled"): "true", ("DlssNr", "NrBackend"): "lmxxf"}
        seen = set()
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped[1:-1]
            elif "=" in stripped and not stripped.startswith((";", "#")):
                key = (section, stripped.split("=", 1)[0].strip())
                if key in settings:
                    lines[index] = key[1] + "=" + settings[key]
                    seen.add(key)
        for section, key in settings.keys() - seen:
            # Insert at the existing section so no duplicate sections are produced.
            index = next(i for i, line in enumerate(lines) if line.strip() == f"[{section}]")
            lines.insert(index + 1, key + "=" + settings[(section, key)])
        ini.write_text("\n".join(lines) + "\n")
        # Ship the corresponding bridge source and the exact graphics patch
        # alongside our binaries; proprietary model/runtime assets stay separate.
        source = bundle / "source/linux"
        source.mkdir(parents=True)
        for directory in ("bridge", "patches", "tests", "licenses", "native_launcher"):
            if not (ROOT / directory).is_dir():
                continue
            shutil.copytree(ROOT / directory, source / directory,
                            ignore=shutil.ignore_patterns("__pycache__"))
        for path in ROOT.iterdir():
            if path.is_file() and path.suffix in (".sh", ".py", ".md"):
                shutil.copy2(path, source / path.name)
        shutil.copy2(ROOT.parent / "LICENSE", source.parent / "LICENSE")
        for name in ("amd/TemporalStability.h", "amd/SystemCompiler.h",
                     "lmxxf/runtime/LmxxfNrApi.h", "lmxxf/runtime/LICENSE.lmxxf"):
            relative = Path("main/AMDNR-source/optiscaler/dlssnr") / name
            destination = source.parent / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT.parent / relative, destination)
        (bundle / "SOURCE.txt").write_text(
            "Modified Linux fork of AMDNR by 3zwr1 — https://github.com/3zwr1/AMD-NR---OptiScaler\n"
            "Linux fork: https://github.com/hassanm0301/AMDNR-Linux\n"
            f"Host/runtime: AMDNR {version}, {release_url}\n"
            f"Archive SHA-256: {release_sha}\n"
            "Linux bridge: GPL-3.0-or-later; vkd3d-proton patch: LGPL-2.1-or-later.\n"
            "Bridge source and graphics patch: amdnr-linux/source/linux.\n"
            "The shader compiler is supplied from the user's own installation.\n"
            "See Licenses and amdnr-linux/rocm/licenses for third-party terms.\n")
        shutil.copy2(ROOT.parent / "LICENSE", bundle / "LICENSE")
        for name in ("COPYING", "LICENSE", "AUTHORS"):
            notice = build / ("vkd3d-proton-" + name)
            if notice.exists():
                shutil.copy2(notice, bundle / notice.name)
        if (ROOT / "licenses").is_dir():
            shutil.copytree(ROOT / "licenses", bundle / "licenses")
        if (build / "vkd3d-licenses").is_dir():
            shutil.copytree(build / "vkd3d-licenses", bundle / "licenses/vkd3d-proton")
        target.rename(output)
    print(f"Staged {output}")

def install(package, game):
    game = game.resolve()
    if not game.is_dir():
        raise ValueError("Game directory does not exist.")
    if (game / RECORD).exists() or (game / BACKUP).exists():
        raise ValueError("An installation or backup already exists; remove it before installing again.")
    # A manually staged bundle may already own prefix changes even without our
    # game installation record. Its journal/backups must never be inherited by
    # a new installation, whose uninstall would consume the previous state.
    for name in ("prefixes.json", "prefixes.json.tmp", "prefix-backups", "prefixes.lock"):
        state = game / "amdnr-linux" / name
        if state.exists() or state.is_symlink():
            raise ValueError(f"Existing bundle prefix state: {state}. Close the game and restore the previous bundle before installing.")
    files = []
    for source in sorted(package.rglob("*")):
        if source.is_symlink():
            raise ValueError(f"Package contains a symlink: {source}")
        if source.is_file():
            name = source.relative_to(package).as_posix()
            if PurePosixPath(name).parts[0] in (RECORD, BACKUP, RECORD + ".tmp") or name == "amdnr-linux/prefixes.json":
                raise ValueError(f"Package contains a reserved path: {name}")
            destination = safe_path(game, name)
            if destination.exists() and not destination.is_file():
                raise ValueError(f"Destination is not a file: {destination}")
            files.append({"path": name, "sha256": sha(source), "existed": destination.exists()})
    if not files or not (package / "amdnr-linux/run.sh").is_file():
        raise ValueError("Not an AMDNR Linux package.")
    backup = game / BACKUP
    backup.mkdir()
    record = {"version": 1, "files": files, "installed": []}
    write_json(game / RECORD, record)
    try:
        for entry in files:
            target = safe_path(game, entry["path"])
            if entry["existed"]:
                saved = safe_path(backup, entry["path"])
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
            # Journal before replacement: an interrupted copy can be rolled back.
            record["installed"].append(entry["path"])
            write_json(game / RECORD, record)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(package / entry["path"], target)
    except BaseException:
        uninstall(game, force=True)
        raise
    print(f"Installed in {game}\nSteam launch option: \"{game / 'amdnr-linux/run.sh'}\" %command%")

def uninstall(game, force=False):
    game = game.resolve()
    record_path = game / RECORD
    record = json.loads(record_path.read_text())
    if record.get("version") != 1:
        raise ValueError("Unsupported installation record version.")
    entries = {entry["path"]: entry for entry in record["files"]}
    for name in record["installed"]:
        path = safe_path(game, name)
        if path.exists() and sha(path) != entries[name]["sha256"] and not force:
            raise ValueError(f"Modified since install: {path}. Save it, or use --force to restore the backup.")
        if entries[name]["existed"] and not safe_path(game / BACKUP, name).is_file():
            raise ValueError(f"Cannot restore missing backup: {name}")
    restore_prefixes(game / "amdnr-linux", force)
    directories = set()
    for name in reversed(record["installed"]):
        path = safe_path(game, name)
        saved = safe_path(game / BACKUP, name)
        if entries[name]["existed"]:
            shutil.copy2(saved, path)
        else:
            path.unlink(missing_ok=True)
        directories.update(p for p in path.parents if p != game and game in p.parents)
    for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass  # keep user-created logs or other files
    shutil.rmtree(game / BACKUP)
    record_path.unlink()
    print(f"Restored {game}")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("stage")
    s.add_argument("--archive", type=Path, required=True)
    s.add_argument("--compiler", type=Path, required=True)
    s.add_argument("--output", type=Path, required=True)
    s.add_argument("--build", type=Path, default=ROOT / "build")
    s.add_argument("--rocm", type=Path)
    i = sub.add_parser("install")
    i.add_argument("package", type=Path)
    i.add_argument("game", type=Path)
    u = sub.add_parser("uninstall")
    u.add_argument("game", type=Path)
    u.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.command == "stage":
        stage(args.archive, args.compiler, args.output, args.build, args.rocm)
    elif args.command == "install":
        install(args.package.resolve(), args.game)
    else:
        uninstall(args.game, args.force)

if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        raise SystemExit(f"AMDNR Linux: {error}")
