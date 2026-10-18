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
import proxies
import runtimes

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

def stage(archive, compiler, output, build, rocm, daniel=None, optipatcher=None, model_e=None):
    """Stage a package. daniel is (Runtime, directory of verified files),
    optipatcher is (Plugin, verified .asi path) and model_e is the (folder,
    details) of a Model E built on this PC; all come from runtimes.
    archive is a pinned AMDNR release or a pinned alternate host build."""
    if output.exists():
        raise ValueError(f"Output already exists: {output}")
    release_sha = sha(archive)
    version = RELEASES.get(release_sha)
    alternate = runtimes.host_build_for(release_sha)
    if not version and not alternate:
        raise ValueError("Expected a pinned, unmodified AMDNR release or alternate host archive; checksum mismatch.")
    if alternate:
        host_name, release_url = alternate.title, alternate.url
        if not daniel:
            raise ValueError(f"{host_name} is supported with danielblnc's runtime only; choose one it drives.")
    else:
        host_name = f"AMDNR {version}"
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
                # Windows-made archives may separate folders with backslashes;
                # safe_path still rejects absolute or parent-relative results.
                name = entry.filename.replace("\\", "/")
                if alternate:
                    wanted = name in alternate.files or name.startswith(alternate.folders)
                else:
                    wanted = (name in ("OptiScaler.dll", "OptiScaler.ini", "LmxxfNrRuntime.dll", "LmxxfNrRuntime.pak")
                              or name.startswith(("OptiScaler/", "Licenses/")))
                if not wanted:
                    continue
                name = "winmm.dll" if name == "OptiScaler.dll" else name
                destination = safe_path(target, name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with package.open(entry) as source, destination.open("wb") as sink:
                    shutil.copyfileobj(source, sink)
        components = {"neural_runtime": "lmxxf", "daniel": None, "optipatcher": None}
        if model_e:
            if alternate or not runtimes.host_loads_model_e(target / "winmm.dll"):
                raise ValueError(f"{host_name} cannot load Model E; it needs AMDNR 0.3.5.6 or newer.")
            folder, details = model_e
            digest = runtimes.stage_model_e(folder, target)
            # Recorded only when present, so other packages keep their existing description.
            components["model_e"] = {"version": runtimes.HELIXSR.version, "url": runtimes.HELIXSR.url,
                                     "sha256": digest, "source_sha256": runtimes.HELIXSR.sha256,
                                     "dlss_version": details.get("dlss_version", runtimes.DLSS_FOR_HELIXSR.version)}
        if daniel:
            runtime, files = daniel
            # The host pins every pass DLL it drives; refuse a pairing it would reject at runtime.
            if not runtimes.host_drives(target / "winmm.dll", runtime):
                raise ValueError(f"{host_name} does not drive danielblnc runtime {runtime.version}. "
                                 + ("Choose a runtime this host lists." if alternate else
                                    "Choose a runtime this host lists, or the built-in lmxxf runtime."))
            for name in (*runtimes.DANIEL_FILES, *runtimes.DANIEL_NOTICES):
                if (files / name).is_file():
                    shutil.copy2(files / name, target / name)
            components.update(neural_runtime="daniel", daniel={"version": runtime.version, "url": runtime.url,
                              "sha256": runtime.sha256, "pass_sha256": runtime.pass_sha256})
        if alternate:
            # Recorded only for alternate hosts, so AMDNR packages keep their existing description.
            components["host"] = {"build": alternate.key, "version": alternate.version,
                                  "url": alternate.url, "sha256": alternate.sha256}
        if optipatcher:
            plugin, path = optipatcher
            destination = safe_path(target, runtimes.OPTIPATCHER_PATH)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            components["optipatcher"] = {"version": plugin.version, "url": plugin.url, "sha256": plugin.sha256}
        for name in ("d3d12.dll", "d3d12core.dll"):
            shutil.copy2(build / name, target / name)
        shutil.copy2(compiler, target / "d3dcompiler_47.dll")
        bundle = target / "amdnr-linux"
        for arch, name in (("x86_64-unix", "amdhip64_7.so"), ("x86_64-windows", "amdhip64_7.dll")):
            (bundle / arch).mkdir(parents=True)
            shutil.copy2(build / arch / name, bundle / arch / name)
        shutil.copy2(build / "x86_64-unix/libamdnr_signal_guard.so", bundle / "x86_64-unix/libamdnr_signal_guard.so")
        for name in ("run.sh", "launch.py", "proxies.py"):
            shutil.copy2(ROOT / name, bundle / name)
        write_json(bundle / "proxy.json", {"proxy": "winmm.dll"})
        (bundle / "run.sh").chmod(0o755)
        if rocm:
            if not (rocm / "lib/libamdhip64.so.7").is_file():
                raise ValueError("Private ROCm runtime is incomplete.")
            shutil.copytree(rocm, bundle / "rocm")
        # Preserve upstream comments and all unrelated configuration.
        ini = target / "OptiScaler.ini"
        lines = ini.read_text(encoding="utf-8-sig").splitlines()
        section = ""
        settings = {("DlssNr", "Enabled"): "true", ("DlssNr", "NrBackend"): components["neural_runtime"]}
        if optipatcher:
            settings[("Plugins", "LoadAsiPlugins")] = "true"
        if model_e:
            settings[("Upscalers", "FfxSRProvider")] = runtimes.MODEL_E_PROVIDER
            settings[("Libraries", "HelixSRPath")] = runtimes.MODEL_E_FOLDER
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
        for section, key in sorted(settings.keys() - seen):
            # Insert at the existing section so no duplicate sections are produced.
            index = next((i for i, line in enumerate(lines) if line.strip() == f"[{section}]"), None)
            if index is None:
                lines.append(f"[{section}]")
                index = len(lines) - 1
            lines.insert(index + 1, key + "=" + settings[(section, key)])
        ini.write_text("\n".join(lines) + "\n")
        write_json(bundle / "components.json", components)
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
            + (f"Host/runtime: {host_name} (independent OptiScaler fork by {alternate.author}, GPL-3.0, "
               f"{alternate.homepage}), {release_url}\n" if alternate else
               f"Host/runtime: {host_name}, {release_url}\n")
            + f"Archive SHA-256: {release_sha}\n"
            "Linux bridge: GPL-3.0-or-later; vkd3d-proton patch: LGPL-2.1-or-later.\n"
            "Bridge source and graphics patch: amdnr-linux/source/linux.\n"
            "The shader compiler is supplied from the user's own installation.\n"
            + (f"Neural runtime: danielblnc {components['daniel']['version']} (DLSS-NR on AMD by Daniel Blanco, "
               f"unmodified, {components['daniel']['url']}, SHA-256 {components['daniel']['sha256']}); "
               + ("see danielblnc_ATTRIBUTION.txt.\n" if (target / "danielblnc_ATTRIBUTION.txt").is_file()
                  else f"all rights reserved by its author, {runtimes.DANIEL_HOMEPAGE}.\n")
               if daniel else "Neural runtime: lmxxf, built into the AMDNR release.\n")
            + (f"Plugin: OptiPatcher {components['optipatcher']['version']} (OptiScaler project, unmodified, "
               f"{components['optipatcher']['url']}).\n" if optipatcher else "")
            + (f"Upscaler: Model E, HelixSR {runtimes.HELIXSR.version} by lonewolf0622 ({runtimes.HELIXSR_HOMEPAGE}, "
               f"HelixSR Freeware License), in {runtimes.MODEL_E_FOLDER}. Its network was built on this PC from "
               f"NVIDIA's DLSS {components['model_e']['dlss_version']} DLL and remains NVIDIA's property: "
               "do not share or upload these files.\n" if model_e else "")
            + "See Licenses and amdnr-linux/rocm/licenses for third-party terms.\n")
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

def install(package, game, proxy="auto", executable=None):
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
    selected = proxies.choose(game, requested=proxy, executable=executable)
    files = []
    sources = {}
    for source in sorted(package.rglob("*")):
        if source.is_symlink():
            raise ValueError(f"Package contains a symlink: {source}")
        if source.is_file():
            name = source.relative_to(package).as_posix()
            if name.casefold() in proxies.SUPPORTED and name != "winmm.dll":
                raise ValueError(f"Package contains an extra proxy DLL: {name}")
            if name == "amdnr-linux/proxy.json":
                continue  # This setting belongs to the installation, not the package.
            if name == "winmm.dll":
                name = selected
            if name in sources:
                raise ValueError(f"Package contains a duplicate destination: {name}")
            if PurePosixPath(name).parts[0] in (RECORD, BACKUP, RECORD + ".tmp") or name == "amdnr-linux/prefixes.json":
                raise ValueError(f"Package contains a reserved path: {name}")
            destination = safe_path(game, name)
            if destination.exists() and not destination.is_file():
                raise ValueError(f"Destination is not a file: {destination}")
            files.append({"path": name, "sha256": sha(source), "existed": destination.exists()})
            sources[name] = source
    if not files or not (package / "winmm.dll").is_file() or not (package / "amdnr-linux/run.sh").is_file():
        raise ValueError("Not an AMDNR Linux package.")
    if selected != "winmm.dll" and not (package / "amdnr-linux/proxies.py").is_file():
        raise ValueError("Prepare a current package before choosing another proxy DLL.")
    config = (json.dumps({"proxy": selected}, indent=2) + "\n").encode()
    config_name = "amdnr-linux/proxy.json"
    config_path = safe_path(game, config_name)
    if config_path.exists() and not config_path.is_file():
        raise ValueError(f"Destination is not a file: {config_path}")
    files.append({"path": config_name, "sha256": hashlib.sha256(config).hexdigest(), "existed": config_path.exists()})
    backup = game / BACKUP
    backup.mkdir()
    record = {"version": 1, "files": files, "installed": [],
              "launcher": {"proxy_name": selected, "proxy_history": [selected]}}
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
            if entry["path"] == config_name:
                target.write_bytes(config)
            else:
                shutil.copy2(sources[entry["path"]], target)
    except BaseException:
        uninstall(game, force=True)
        raise
    print(f"Installed in {game} using {selected}\nSteam launch option: \"{game / 'amdnr-linux/run.sh'}\" %command%")

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
    s.add_argument("--daniel-runtime", type=Path, metavar="ZIP",
                   help="Pinned, unmodified danielblnc vX.Y.Z-Runtime.zip; selects NrBackend=daniel")
    s.add_argument("--optipatcher", type=Path, metavar="ASI", help="Pinned, unmodified OptiPatcher_vX.YY.asi")
    s.add_argument("--model-e", type=Path, metavar="DIR",
                   help="Model E built on this PC by setup.py prepare --model-e (its HelixSR folder); "
                        "selects FfxSRProvider=helix_e")
    i = sub.add_parser("install")
    i.add_argument("package", type=Path)
    i.add_argument("game", type=Path)
    i.add_argument("--proxy", choices=("auto", *proxies.SUPPORTED), default="auto")
    i.add_argument("--exe", type=Path, help="Main game executable, used to select an imported proxy")
    p = sub.add_parser("switch-proxy", help="Change an installed game's proxy without downloading again")
    p.add_argument("game", type=Path)
    p.add_argument("--proxy", choices=("next", *proxies.SUPPORTED), default="next")
    p.add_argument("--exe", type=Path)
    u = sub.add_parser("uninstall")
    u.add_argument("game", type=Path)
    u.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.command == "stage":
        with tempfile.TemporaryDirectory(prefix="amdnr-components-") as temporary:
            daniel = optipatcher = None
            if args.daniel_runtime:
                digest = sha(args.daniel_runtime)
                runtime = next((item for item in runtimes.DANIEL_RUNTIMES if item.sha256 == digest), None)
                if runtime is None:
                    raise ValueError("Expected a pinned, unmodified danielblnc runtime archive; checksum mismatch.")
                daniel = (runtime, runtimes.extract_daniel(args.daniel_runtime, runtime, Path(temporary) / "daniel"))
            if args.optipatcher:
                digest = sha(args.optipatcher)
                plugin = next((item for item in runtimes.OPTIPATCHER if item.sha256 == digest), None)
                if plugin is None:
                    raise ValueError("Expected a pinned, unmodified OptiPatcher plugin; checksum mismatch.")
                optipatcher = (plugin, args.optipatcher)
            model_e = runtimes.load_model_e(args.model_e) if args.model_e else None
            if args.model_e and model_e is None:
                raise ValueError(f"Not a Model E built by setup.py for HelixSR {runtimes.HELIXSR.version}: {args.model_e}")
            stage(args.archive, args.compiler, args.output, args.build, args.rocm, daniel, optipatcher, model_e)
    elif args.command == "install":
        install(args.package.resolve(), args.game, args.proxy, args.exe)
    elif args.command == "switch-proxy":
        from native_launcher.controller import Controller
        from native_launcher.steam import SteamGame
        game = args.game.resolve()
        controller = Controller(ROOT.parent)
        entry = SteamGame("", game.name, game, Path(), game, game)
        if controller.pending_operation(entry):
            raise ValueError("Recover the interrupted operation in the graphical launcher before changing its proxy.")
        if args.exe:
            executable = args.exe.resolve()
            if executable.parent != game:
                raise ValueError("The executable must be inside the selected game directory.")
            entry = controller.set_executable(entry, executable)
        result = controller.operate("switch_proxy", entry, proxy=args.proxy)
        print(result["message"])
    else:
        uninstall(args.game, args.force)

if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        raise SystemExit(f"AMDNR Linux: {error}")
