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

from . import NAME, VERSION
from .backend import LauncherBackend
from . import compatibility
from . import steam
from . import stores
from .versions import version_key

REPOSITORY = "hassanm0301/AMDNR-Linux"
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
    request = urllib.request.Request(RELEASES_URL, headers={"Accept": "application/vnd.github+json", "User-Agent": "AMDNR-Linux-Manager"})
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
                request = urllib.request.Request(release["url"], headers={"User-Agent": "AMDNR-Linux-Manager"})
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
    def __init__(self, release_root, data_dir=None, cache_dir=None, steam_roots=None, progress=None,
                 heroic_roots=None, lutris_roots=None):
        self.release_root = Path(release_root).resolve()
        # Keep the original storage identity so upgrades retain settings, backups,
        # recovery journals and the shared Python used by installed games.
        self.data_dir = Path(data_dir or Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "nr-linux-manager").expanduser().resolve()
        self.cache_dir = Path(cache_dir or Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "nr-linux-manager").expanduser().resolve()
        self.steam_roots = steam_roots
        self.heroic_roots = heroic_roots
        self.lutris_roots = lutris_roots
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
        self._gpu = None

    def report(self, message):
        self.progress(str(message))

    def save_settings(self):
        atomic_json(self.settings_path, self.settings)

    def discover(self):
        self.report("Looking for installed Steam games…")
        found = steam.discover_games(self.steam_roots)
        self.report("Looking for Heroic and Lutris games…")
        # Steam's own entry manages its launch options; skip a store's copy of that folder.
        taken = {game.install_dir.resolve() for game in found}
        found += [game for game in stores.discover_games(self.heroic_roots, self.lutris_roots)
                  if game.install_dir not in taken]
        self.games = [self.chosen_exe_dir(game) for game in sorted(found, key=lambda game: game.name.casefold())]
        existing = {game.install_dir.resolve() for game in self.games}
        # A folder added by hand before Heroic or Lutris listed it shows once, as theirs.
        existing |= {game.exe_dir.resolve() for game in self.games if stores.store_name(game) and game.exe_dir}
        for path in self.settings.get("manual", []):
            directory = Path(path)
            if directory.is_dir() and directory.resolve() not in existing:
                self.games.append(self.chosen_exe_dir(
                    steam.SteamGame("", directory.name, directory, Path(), directory, directory)))
        return self.games

    def chosen_exe_dir(self, game):
        """Use the folder of the executable chosen for this game while it is still inside the game."""
        selected = self.settings.get("executables", {}).get(self.game_key(game))
        if selected and not getattr(game, "library_ambiguous", False):
            executable = Path(selected)
            if executable.is_file() and executable.resolve().is_relative_to(game.install_dir.resolve()):
                return replace(game, exe_dir=executable.resolve().parent)
        return game

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
        executable = steam.unreal_game_executable(executable)
        if game.appid and not executable.is_relative_to(game.install_dir.resolve()):
            raise ValueError("Choose an executable inside this Steam game's installation.")
        if stores.store_name(game) and not executable.is_relative_to(game.install_dir.resolve()):
            raise ValueError(f"Choose an executable inside this game's {stores.store_name(game)} installation.")
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
        game = steam.SteamGame("", directory.name, directory, Path(), directory, directory)
        self.settings.setdefault("executables", {})[self.game_key(game)] = str(steam.unreal_game_executable(executable))
        self.save_settings()
        return directory

    def executable(self, game):
        """Use the chosen executable only while it still belongs to this game."""
        selected = self.settings.get("executables", {}).get(self.game_key(game))
        if selected and game.exe_dir:
            path = Path(selected).resolve()
            if path.is_file() and path.parent == game.exe_dir.resolve():
                return path
        return None

    def inspect(self, game):
        result = self.backend.inspect(game.exe_dir, executable=self.executable(game))
        if result.get("leftovers") is not None:
            # A wrapper left in Steam's launch options stops the game starting once its files are gone.
            result["leftovers"]["steam_wrapper"] = bool(game.appid) and steam.wrapper_leftover(
                game, game.exe_dir / "amdnr-linux/run.sh", self.data_dir)
        result["notices"] = (compatibility.notices(game, managed=result.get("installed", False))
                             + stores.notices(game, installed=result.get("installed", False)))
        result["component_choices"] = self.component_choices(game, result)
        return result

    def gpu(self):
        """The ROCm-visible GPU used to filter runtime choices (cached)."""
        if self._gpu is None:
            self._gpu = self.backend.runtimes.detect_gpu() or {}
        return self._gpu or None

    def host_version(self, backend=None, host=None):
        """The AMDNR release version, or an alternate host build's HOST_RUNTIMES key."""
        backend = backend or self.backend
        if host not in (None, "amdnr"):
            return backend.runtimes.host_build(host).host_version
        return getattr(backend.setup, "RELEASE_VERSION", None)

    def host_title(self, backend=None, host=None):
        backend = backend or self.backend
        if host not in (None, "amdnr"):
            return backend.runtimes.host_build(host).title
        return f"AMDNR {self.host_version(backend)}"

    def offered_runtimes(self, backend=None, host=None):
        backend = backend or self.backend
        return [runtime.version for runtime in
                backend.runtimes.runtimes_for(self.gpu(), host_version=self.host_version(backend, host))]

    def component_choices(self, game, inspection=None):
        """Optional components this release offers this GPU, and the selection."""
        runtimes = self.backend.runtimes
        gpu = self.gpu()
        supported = self.backend.supports_components()
        selected = self.components(game, inspection)
        hosts = [dict(key="amdnr", title=self.host_title(), builtin_runtime=True)]
        if supported and self.backend.supports_host_builds():
            hosts += [dict(key=build.key, title=build.title, author=build.author, homepage=build.homepage,
                           builtin_runtime=False) for build in runtimes.HOST_BUILDS]
        host_runtimes = {item["key"]: self.offered_runtimes(host=item["key"]) if supported else [] for item in hosts}
        host = selected.get("host") or "amdnr"
        return dict(supported=supported, gpu=gpu, host_version=self.host_version(host=host),
                    hosts=hosts, host_runtimes=host_runtimes, runtimes=host_runtimes.get(host, []),
                    optipatcher=[plugin.version for plugin in runtimes.OPTIPATCHER] if supported else [],
                    model_e=self.model_e_choice() if supported else None, selected=selected)

    def model_e_choice(self, backend=None):
        """What the Model E disclosure needs to say, or None when this release cannot build it."""
        backend = backend or self.backend
        if not backend.supports_model_e():
            return None
        runtimes = backend.runtimes
        gpu = self.gpu()
        # HelixSR needs wave32: RDNA 1 and newer. An undetected GPU is not refused.
        gpu_supported = gpu is None or gpu.get("generation") in ("rdna1", "rdna2", "rdna3", "rdna4")
        return dict(helixsr=runtimes.HELIXSR.version, homepage=runtimes.HELIXSR_HOMEPAGE,
                    dlss=runtimes.DLSS_FOR_HELIXSR.version, dlss_url=runtimes.DLSS_FOR_HELIXSR.url,
                    dlss_license=runtimes.DLSS_LICENSE, accepted=self.model_e_accepted(backend),
                    built=backend.model_e_built(), gpu_supported=gpu_supported)

    def _model_e_terms(self, backend=None):
        runtimes = (backend or self.backend).runtimes
        return {"helixsr": runtimes.HELIXSR.version, "dlss": runtimes.DLSS_FOR_HELIXSR.version}

    def model_e_accepted(self, backend=None):
        """Whether the user accepted the Model E disclosure for the pinned HelixSR and NVIDIA DLL."""
        return self.settings.get("model_e_consent") == self._model_e_terms(backend)

    def accept_model_e(self):
        if not self.backend.supports_model_e():
            raise ValueError("This Linux package cannot add Model E. Update the launcher first.")
        self.settings["model_e_consent"] = self._model_e_terms()
        self.save_settings()

    def components(self, game, inspection=None):
        """The saved selection, else what is installed, else the built-in runtime only.

        "host" is present only when an alternate host build is chosen.
        """
        saved = self.settings.get("components", {}).get(self.game_key(game))
        if isinstance(saved, dict):
            selection = {"daniel": saved.get("daniel"), "optipatcher": saved.get("optipatcher")}
            host, model_e = saved.get("host"), saved.get("model_e") is True
        else:
            installed = (inspection or {}).get("components") or {}
            selection = {"daniel": (installed.get("daniel") or {}).get("version"),
                         "optipatcher": (installed.get("optipatcher") or {}).get("version")}
            host, model_e = (installed.get("host") or {}).get("build"), bool(installed.get("model_e"))
        if host not in (None, "amdnr"):
            selection["host"] = host
        if model_e:
            selection["model_e"] = True
        return selection

    def set_components(self, game, daniel=None, optipatcher=None, host=None, model_e=False):
        runtimes = self.backend.runtimes
        host = None if host == "amdnr" else host
        if model_e:
            if not self.backend.supports_model_e():
                raise ValueError("This Linux package cannot add Model E. Update the launcher first.")
            if host is not None:
                raise ValueError(f"{self.host_title(host=host)} cannot load Model E; it needs the AMDNR host.")
            if not self.model_e_accepted():
                raise ValueError("Read and accept the Model E notice first.")
        if host is not None:
            if not self.backend.supports_host_builds():
                raise ValueError("This Linux package cannot use another host build. Update the launcher first.")
            runtimes.host_build(host)
            if daniel is None:
                raise ValueError(f"{self.host_title(host=host)} runs danielblnc's runtime only. Choose one of its runtimes.")
        if daniel is not None:
            runtimes.daniel_runtime(daniel)
            offered = self.offered_runtimes(host=host)
            if daniel not in offered:
                raise ValueError(f"danielblnc runtime {daniel} is not offered for this GPU and "
                                 f"{self.host_title(host=host)}. Available: {', '.join(offered) or 'none'}.")
        if optipatcher is not None:
            runtimes.optipatcher(optipatcher)
        selection = {"daniel": daniel, "optipatcher": optipatcher}
        if host is not None:
            selection["host"] = host
        if model_e:
            selection["model_e"] = True
        self.settings.setdefault("components", {})[self.game_key(game)] = selection
        self.save_settings()
        return self.components(game)

    def choose_compiler(self, path):
        self.backend.setup.validate_compiler(Path(path))
        self.settings["compiler"] = str(Path(path).resolve())
        self.save_settings()

    def prepare(self, game, backend=None, components=None):
        backend = backend or self.backend
        roots = [game.install_dir]
        # Another game's whole Wine prefix (a Lutris install) would use up the bounded search.
        roots += [item.install_dir for item in self.games
                  if item.install_dir != game.install_dir and not stores.is_prefix(item.install_dir)]
        if game.appid:
            roots.insert(1, game.library / "steamapps/compatdata" / game.appid / "pfx/drive_c/windows/system32")
        elif getattr(game, "prefix", None):
            roots.insert(1, game.prefix / "drive_c/windows/system32")
        compiler = self.settings.get("compiler")
        options = {}
        if (components or {}).get("model_e"):
            options["accept_dlss_license"] = self.model_e_accepted(backend)
        package = backend.prepare_package(Path(compiler) if compiler else None, roots, components=components, **options)
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

    def mutate(self, backend, action, game, package=None, proxy=None, seeds=None):
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
                elif action == "switch_proxy":
                    backend.switch_proxy(game.exe_dir, proxy=proxy or "next", executable=self.executable(game))
                else:
                    options = {}
                    if proxy is not None:
                        options["proxy"] = proxy
                    if self.executable(game) is not None:
                        options["executable"] = self.executable(game)
                    if seeds:
                        options["seeds"] = seeds
                    getattr(backend, action)(game.exe_dir, package, **options)
        except BaseException:
            # Retain the marker until both layers have resolved their journals.
            self.recover_operation(game)
            raise
        else:
            marker.unlink()
        finally:
            backend.operation_id = None

    def operate(self, action, game, proxy=None):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with (self.data_dir / "operation.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError("Another launcher operation is already running.") from error
            return self._operate(action, game, proxy)

    def _operate(self, action, game, proxy=None):
        if action not in {"install", "repair", "update", "uninstall", "switch_proxy", "clean"}:
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
            if stores.store_name(game):
                return {"message": stores.removed_message(game)}
            return {"message": "Removed. Original game files and Steam launch options restored."}
        if action == "clean":
            return self._clean(game)
        if action == "switch_proxy":
            self.report("Changing the game's loading DLL…")
            self.mutate(self.backend, action, game, proxy=proxy)
            selected = self.backend.inspect(directory)["proxy_name"]
            return {"message": f"Loading DLL changed to {selected}. Launch the game to check whether OptiScaler opens."}
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
        components = self.components(game, installed)
        host = components.get("host")
        if host and not backend.supports_host_builds():
            raise ValueError("This Linux package cannot use another host build. Update the launcher first.")
        if host and not components.get("daniel"):
            raise ValueError(f"{self.host_title(backend, host)} runs danielblnc's runtime only. Choose one of its runtimes first.")
        if components.get("daniel"):
            if components["daniel"] not in self.offered_runtimes(backend, host):
                raise ValueError(f"{self.host_title(backend, host)} does not offer danielblnc runtime "
                                 f"{components['daniel']} for this GPU. Choose another neural runtime first.")
        if any(components.values()) and not backend.supports_components():
            raise ValueError("This Linux package cannot add danielblnc's runtime or OptiPatcher. Update the launcher first.")
        if components.get("model_e"):
            if not backend.supports_model_e():
                raise ValueError("This Linux package cannot add Model E. Update the launcher first.")
            if host:
                raise ValueError(f"{self.host_title(backend, host)} cannot load Model E; it needs the AMDNR host.")
            if not self.model_e_accepted(backend) and not backend.model_e_built():
                raise ValueError("Read and accept the Model E notice before installing it.")
        package = self.prepare(game, backend, components)
        # A host build change also starts OptiScaler.ini afresh, so it gets the first-install settings.
        host_changed = (host or "amdnr") != ((installed.get("components") or {}).get("host") or {}).get("build", "amdnr")
        seeds = compatibility.seeds(game, self.executable(game)) if action == "install" or host_changed else None
        try:
            self.report("Applying game files and Steam launch options…")
            self.mutate(backend, action, game, package, proxy=proxy, seeds=seeds)
        finally:
            # Candidates contain large private runtime files; downloads remain cached.
            if package.parent.parent == self.cache_dir and package.parent.name.startswith("candidate-"):
                shutil.rmtree(package.parent)
        if stores.store_name(game):
            return stores.install_result(game)
        if not game.appid:
            return {"message": 'Installed. Add the launch option shown below to your game runner.',
                    "launch_options": shlex.quote(str(directory / "amdnr-linux/run.sh")) + " %command%"}
        return {"message": "Installed. Open Steam and launch your game normally. Enable DLSS, FSR or XeSS in the game; Insert opens OptiScaler and Home toggles neural rendering."}

    def _clean(self, game):
        """Remove what an installation this launcher no longer recognises left behind."""
        directory = game.exe_dir
        wrapper = directory / "amdnr-linux/run.sh"
        self.report("Looking for leftover AMDNR files…")
        plan = self.backend.leftovers(directory)
        if plan["issues"]:
            raise ValueError("\n".join(plan["issues"]))
        files = any(plan[key] for key in ("remove", "restore", "folders", "prefix"))
        options = bool(game.appid) and steam.wrapper_leftover(game, wrapper, self.data_dir)
        if not files and not options:
            raise ValueError("No leftover AMDNR files were found for this game.")
        # Steam first: a failure there leaves every file in place, and both steps can be repeated.
        if options:
            self.report("Taking the AMDNR wrapper out of Steam's launch options…")
            steam.remove_wrapper(game, wrapper, self.data_dir)
        if files:
            self.backend.clean(directory)
        message = "Leftover AMDNR files removed." if files else "No AMDNR files were left in the game folder."
        if plan["restore"]:
            message += " Original game files were put back."
        if options:
            message += " Steam's launch options no longer start the game through the AMDNR wrapper."
        if stores.store_name(game):
            message += (f" Remove the AMDNR wrapper from {stores.PLACES[game.store]} in {stores.store_name(game)}, "
                        "or the game will not start." if stores.wrapper_state(game) else "")
        elif not game.appid:
            message += " If a launch option still runs amdnr-linux/run.sh, remove it, or the game will not start."
        return {"message": message}

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
                for name in ("OptiScaler.log", "re2_framework_log.txt", "lmxxf_backend.log", "amd_bridge.log",
                             "amd_presr.log", "dlssnr_on_amd.log", "dlssnr_on_amd.ini", "amdnr_crash.log",
                             "amdnr-linux/launch.log", "amdnr-linux/components.json", "OptiScaler.ini",
                             "AMDNR-SR-E/helixsr.log"):
                    path = game.exe_dir / name
                    if (path.is_file() and not path.is_symlink()
                            and not any(p.is_symlink() for p in path.parents)
                            and path.stat().st_size < 50 * 1024**2):
                        output.write(path, name)
                output.writestr("diagnostics.json", json.dumps(diagnostics, indent=2, default=str) + "\n")
                output.writestr("launcher.txt", f"{NAME} {VERSION}\nGame: {game.name}\nApp ID: {game.appid or 'manual'}\n"
                                + diagnostics.get("startup_summary", "Runtime activation has not been verified.") + "\n")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination
