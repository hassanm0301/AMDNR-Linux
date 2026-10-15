# SPDX-License-Identifier: GPL-3.0-or-later
"""Application orchestration. No dependency on the upstream Windows launcher."""
from contextlib import nullcontext
from dataclasses import replace
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import subprocess
import tarfile
import tempfile
import urllib.request
import uuid

from . import VERSION
from .backend import LauncherBackend
from . import steam
from .versions import version_key

REPOSITORY = "hassanm0301/AMD-NR---OptiScaler-rocm"
RELEASES_URL = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=30"


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False, encoding="utf-8") as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def version_tuple(text):
    return version_key(text.removeprefix("linux-v")) if isinstance(text, str) else None


def available_release(current=VERSION):
    current_version = version_tuple(current)
    if current_version is None:
        raise ValueError("Invalid current Linux release version")
    request = urllib.request.Request(RELEASES_URL, headers={"Accept": "application/vnd.github+json", "User-Agent": "NR-Linux-Manager"})
    with urllib.request.urlopen(request, timeout=30) as response:
        releases = json.load(response)
    candidates = []
    for release in releases:
        version = version_tuple(release.get("tag_name", ""))
        # Local preview builds can update to a stable release, but prereleases
        # remain opt-in and are never offered by the normal update channel.
        if (release.get("draft") or release.get("prerelease") or version is None or
                not version[3] or version <= current_version):
            continue
        number = release["tag_name"].removeprefix("linux-v")
        name = f"AMDNR-Linux-v{number}.tar.gz"
        for asset in release.get("assets", []):
            expected_url = f"https://github.com/{REPOSITORY}/releases/download/{release['tag_name']}/{name}"
            if (asset.get("name") == name and asset.get("browser_download_url") == expected_url
                    and re.fullmatch(r"sha256:[a-f0-9]{64}", asset.get("digest") or "")):
                candidates.append({"version": number, "url": expected_url,
                                   "sha256": asset["digest"][7:], "size": asset.get("size", 0),
                                   "page": f"https://github.com/{REPOSITORY}/releases/tag/{release['tag_name']}"})
    return max(candidates, key=lambda item: version_tuple(item["version"]), default=None)


def obtain_addon(release, cache, progress):
    """Download only the fork's expected addon, verify, and safely extract it."""
    root_name = f"AMDNR-Linux-v{release['version']}"
    destination = cache / "releases" / (release["version"] + "-" + release["sha256"][:16])
    # Re-extract from the verified archive rather than trusting cached scripts.
    archive = cache / "downloads" / (root_name + ".tar.gz")
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.is_file() or file_hash(archive) != release["sha256"]:
        progress(f"Downloading Linux update {release['version']}…")
        with tempfile.NamedTemporaryFile(dir=archive.parent, delete=False) as target:
            pending = Path(target.name)
            try:
                request = urllib.request.Request(release["url"], headers={"User-Agent": "NR-Linux-Manager"})
                with urllib.request.urlopen(request, timeout=60) as source:
                    shutil.copyfileobj(source, target)
                target.flush()
                if file_hash(pending) != release["sha256"]:
                    raise ValueError("The downloaded update failed verification. Nothing was installed.")
                pending.replace(archive)
            finally:
                pending.unlink(missing_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        extraction = Path(temporary)
        with tarfile.open(archive) as bundle:
            members = bundle.getmembers()
            if len(members) > 50000 or sum(m.size for m in members) > 2 * 1024**3:
                raise ValueError("Update archive exceeds the supported size.")
            for member in members:
                parts = Path(member.name).parts
                if (not parts or parts[0] != root_name or ".." in parts
                        or not (member.isfile() or member.isdir())):
                    raise ValueError("Update archive contains an unsafe entry.")
            bundle.extractall(extraction, filter="data")
        candidate = extraction / root_name
        manifest = json.loads((candidate / "RELEASE-MANIFEST.json").read_text())
        if manifest.get("release") != "linux-v" + release["version"]:
            raise ValueError("Update version does not match the release.")
        # Use the existing trusted verifier; do not execute downloaded Python.
        import importlib.util
        verifier_path = Path(__file__).resolve().parents[1] / "setup.py"
        spec = importlib.util.spec_from_file_location("nrl_update_verify", verifier_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.ROOT = candidate / "linux"
        module.verify_binaries()
        if destination.exists():
            shutil.rmtree(destination)
        candidate.rename(destination)
    return destination


def persistent_python(data_dir):
    """Keep the game's small Python runtime independent of the downloaded app."""
    portable = os.environ.get("NRL_PORTABLE_ROOT")
    if not portable:
        return None  # Source users retain the existing system-Python workflow.
    source = Path(portable).resolve() / "runtime/python"
    executable = source / "bin/python3.12"
    if not executable.is_file():
        raise ValueError("The launcher's bundled Python runtime is missing. Extract the complete download.")
    key = file_hash(executable)[:16]
    target = data_dir / "runtime" / key
    installed = target / "bin/python3.12"
    probe = ["-I", "-c", "import pathlib,datetime,hashlib,json,os,shutil,sys,fcntl,tempfile"]
    healthy = (installed.is_file() and os.access(installed, os.X_OK)
               and file_hash(installed) == file_hash(executable))
    if healthy:
        try:
            healthy = subprocess.run([str(installed), *probe], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, timeout=15).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            healthy = False
    if not healthy:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=target.parent) as temporary:
            copied = Path(temporary) / "python"
            shutil.copytree(source, copied, symlinks=True,
                            ignore=shutil.ignore_patterns("site-packages", "__pycache__"))
            subprocess.run([str(copied / "bin/python3.12"), *probe], check=True, timeout=15)
            previous = Path(temporary) / "previous"
            if target.exists() or target.is_symlink():
                target.rename(previous)
            try:
                copied.rename(target)
            except BaseException:
                if previous.exists() or previous.is_symlink():
                    previous.rename(target)
                raise
    return target / "bin/python3.12"


class Controller:
    def __init__(self, release_root, data_dir=None, cache_dir=None, steam_roots=None, progress=None):
        self.release_root = Path(release_root).resolve()
        self.data_dir = Path(data_dir or Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "nr-linux-manager").expanduser().resolve()
        self.cache_dir = Path(cache_dir or Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "nr-linux-manager").expanduser().resolve()
        self.steam_roots = steam_roots
        self.progress = progress or (lambda message: None)
        self.settings_path = self.data_dir / "settings.json"
        try:
            self.settings = json.loads(self.settings_path.read_text())
            if not isinstance(self.settings, dict):
                raise ValueError("Invalid launcher settings")
        except (OSError, ValueError):
            self.settings = {"manual": [], "compiler": None}
        self.games = []
        self.backend = LauncherBackend(self.release_root, self.cache_dir, self.data_dir, self.report)
        self.new_release = None

    def report(self, message):
        self.progress(str(message))

    def save_settings(self):
        atomic_json(self.settings_path, self.settings)

    def discover(self):
        self.report("Looking for installed Steam games…")
        self.games = steam.discover_games(self.steam_roots)
        for index, game in enumerate(self.games):
            selected = self.settings.get("executables", {}).get(self.game_key(game))
            if selected and not getattr(game, "library_ambiguous", False):
                executable = Path(selected)
                if executable.is_file() and executable.resolve().is_relative_to(game.install_dir.resolve()):
                    self.games[index] = replace(game, exe_dir=executable.resolve().parent)
        existing = {game.install_dir.resolve() for game in self.games}
        for path in self.settings.get("manual", []):
            directory = Path(path)
            if directory.is_dir() and directory.resolve() not in existing:
                self.games.append(steam.SteamGame("", directory.name, directory, Path(), directory, directory))
        return self.games

    @staticmethod
    def game_key(game):
        return hashlib.sha256((str(game.steam_root) + "\0" + game.appid + "\0" + str(game.install_dir)).encode()).hexdigest()

    def set_executable(self, game, executable):
        if getattr(game, "library_ambiguous", False):
            raise ValueError("Steam lists this game in more than one library. Resolve the duplicate in Steam, then rescan.")
        if self.pending_operation(game) or (game.exe_dir and self.backend.inspect(game.exe_dir).get("recovery_needed")):
            raise ValueError("Recover the interrupted installation before changing its executable directory.")
        executable = Path(executable).resolve()
        if (not executable.is_file() or executable.suffix.lower() != ".exe"
                or not steam._x64_executable(executable)):
            raise ValueError("Choose the game's 64-bit Windows executable.")
        if game.appid and not executable.is_relative_to(game.install_dir.resolve()):
            raise ValueError("Choose an executable inside this Steam game's installation.")
        if game.exe_dir and self.backend.inspect(game.exe_dir).get("installed") and executable.parent != game.exe_dir:
            raise ValueError("Uninstall the current patch before changing its executable directory.")
        self.settings.setdefault("executables", {})[self.game_key(game)] = str(executable)
        self.save_settings()
        return replace(game, exe_dir=executable.parent)

    def add_manual(self, executable):
        executable = Path(executable).resolve()
        if (not executable.is_file() or executable.suffix.lower() != ".exe"
                or not steam._x64_executable(executable)):
            raise ValueError("Choose the game's 64-bit Windows .exe file.")
        directory = executable.parent
        manual = self.settings.setdefault("manual", [])
        if str(directory) not in manual:
            manual.append(str(directory))
            self.save_settings()
        return directory

    def choose_compiler(self, path):
        self.backend.setup.validate_compiler(Path(path))
        self.settings["compiler"] = str(Path(path).resolve())
        self.save_settings()

    def prepare(self, game, backend=None):
        backend = backend or self.backend
        roots = [game.install_dir]
        roots += [item.install_dir for item in self.games if item.install_dir != game.install_dir]
        if game.appid:
            roots.insert(1, game.library / "steamapps/compatdata" / game.appid / "pfx/drive_c/windows/system32")
        compiler = self.settings.get("compiler")
        package = backend.prepare_package(Path(compiler) if compiler else None, roots)
        python = persistent_python(self.data_dir)
        if python:
            (package / "amdnr-linux/python-runtime.path").write_text(str(python) + "\n")
        return package

    def operation_path(self, game):
        directory = str(game.exe_dir.resolve()) if game.exe_dir else self.game_key(game)
        key = hashlib.sha256(directory.encode()).hexdigest()
        return self.data_dir / "operations" / (key + ".json")

    def pending_operation(self, game):
        return bool(game and self.operation_path(game).exists())

    def recover_operation(self, game):
        path = self.operation_path(game)
        if not path.exists():
            return
        operation = json.loads(path.read_text())
        if operation.get("game") != str(game.exe_dir.resolve()):
            raise ValueError("The interrupted-operation record does not match this game.")
        self.report("Recovering the previous operation and its Steam settings…")
        self.backend.recover(game.exe_dir)
        receipt = self.backend.operation_receipt(game.exe_dir)
        committed = bool(receipt and receipt.get("operation_id") == operation["operation_id"])
        if game.appid:
            steam.recover_launch_transaction(game, self.data_dir, keep_changes=committed)
        path.unlink()

    def mutate(self, backend, action, game, package=None):
        token = uuid.uuid4().hex
        backend.operation_id = token
        marker = self.operation_path(game)
        atomic_json(marker, {"operation_id": token, "action": action, "game": str(game.exe_dir.resolve())})
        transaction = nullcontext()
        if game.appid:
            transaction = (steam.restore_launch_transaction(game, self.data_dir, defer_recovery=True) if action == "uninstall"
                           else steam.launch_transaction(game, game.exe_dir / "amdnr-linux/run.sh", self.data_dir, defer_recovery=True))
        try:
            with transaction:
                if action == "uninstall":
                    backend.uninstall(game.exe_dir)
                else:
                    getattr(backend, action)(game.exe_dir, package)
        except BaseException:
            # Retain the marker until both layers have resolved their journals.
            self.recover_operation(game)
            raise
        else:
            marker.unlink()
        finally:
            backend.operation_id = None

    def operate(self, action, game):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with (self.data_dir / "operation.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError("Another launcher operation is already running.") from error
            return self._operate(action, game)

    def _operate(self, action, game):
        if action not in {"install", "repair", "update", "uninstall"}:
            raise ValueError("Unknown installation action.")
        if getattr(game, "library_ambiguous", False):
            raise ValueError("Steam lists this game in more than one library. Resolve the duplicate in Steam, then rescan.")
        if game.exe_dir is None:
            raise ValueError("Select the actual game executable before installing.")
        if game.appid and steam.steam_is_running():
            raise ValueError("Close Steam completely before changing this game's installation.")
        self.recover_operation(game)
        directory = game.exe_dir
        if action == "uninstall":
            self.mutate(self.backend, action, game)
            return {"message": "Removed. Original game files and Steam launch options restored."}
        backend = self.backend
        if action == "update":
            self.report("Checking for a newer Linux package…")
            newer = available_release()
            if newer:
                root = obtain_addon(newer, self.cache_dir, self.report)
                backend = LauncherBackend(root, self.cache_dir, self.data_dir, self.report)
        installed = backend.inspect(directory)
        selected_version = json.loads((backend.release_root / "RELEASE-MANIFEST.json").read_text()).get("release", "").removeprefix("linux-v")
        installed_version = installed.get("addon_version")
        if (version_tuple(installed_version or "") and version_tuple(selected_version or "")
                and version_tuple(installed_version) > version_tuple(selected_version)):
            raise ValueError("This game has a newer Linux package. Download the current launcher before repairing or updating it.")
        package = self.prepare(game, backend)
        try:
            self.report("Applying game files and Steam launch options…")
            self.mutate(backend, action, game, package)
        finally:
            # Candidates contain large private runtime files; downloads remain cached.
            if package.parent.parent == self.cache_dir and package.parent.name.startswith("candidate-"):
                shutil.rmtree(package.parent)
        if not game.appid:
            return {"message": 'Installed. Add the launch option shown below to your game runner.',
                    "launch_options": shlex.quote(str(directory / "amdnr-linux/run.sh")) + " %command%"}
        return {"message": "Installed. Open Steam and launch your game normally. Enable DLSS, FSR or XeSS in the game; Insert opens OptiScaler and Home toggles neural rendering."}

    def check_updates(self):
        self.new_release = available_release()
        return self.new_release

    def collect_logs(self, game, destination):
        import zipfile
        if game.exe_dir is None:
            raise ValueError("Choose the game's executable first.")
        destination = Path(destination)
        diagnostics = self.backend.diagnostics(game.exe_dir)
        # The save dialog already confirms replacement. Build the complete ZIP
        # beside its destination, then replace atomically so failure never
        # destroys an earlier log export or follows a destination symlink.
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".nr-logs-", delete=False) as stream:
            temporary = Path(stream.name)
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as output:
                for name in ("OptiScaler.log", "lmxxf_backend.log", "amd_bridge.log",
                             "amdnr-linux/launch.log", "OptiScaler.ini"):
                    path = game.exe_dir / name
                    if (path.is_file() and not path.is_symlink()
                            and not any(p.is_symlink() for p in path.parents)
                            and path.stat().st_size < 50 * 1024**2):
                        output.write(path, name)
                output.writestr("diagnostics.json", json.dumps(diagnostics, indent=2, default=str) + "\n")
                output.writestr("launcher.txt", f"NR Linux Manager {VERSION}\nGame: {game.name}\nApp ID: {game.appid or 'manual'}\n"
                                + diagnostics.get("startup_summary", "Runtime activation has not been verified.") + "\n")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination
