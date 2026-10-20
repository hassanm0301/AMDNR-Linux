"""Local package operations for the Linux launcher.

This module uses only this project's Linux setup/installation code. Operations
are synchronous: run them on the launcher's worker thread, after preparing the
candidate and any Steam configuration change. No game is ever launched here.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import inspect as signature_inspect
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
import threading
import time
import uuid
from typing import Callable

from .versions import version_key


class BackendError(RuntimeError):
    pass


class CompilerNeeded(BackendError):
    """The UI should offer a native d3dcompiler_47.dll file picker."""


class GameRunning(BackendError):
    pass


class ConflictError(BackendError):
    pass


_module_lock = threading.Lock()
_missing = object()
# Persistent ownership marker: retain its original bytes across branding changes.
_backup_marker = b"NR Linux Manager original backups\n"
_proxy_config = "amdnr-linux/proxy.json"
# System32 files the wrapper journals (launch.py PREFIX_NAMES): the HIP bridge,
# its lowercase name from earlier wrappers, and the shader compiler.
_PREFIX_FILES = ("AMDHIP64_7.DLL", "amdhip64_7.dll", "d3dcompiler_47.dll")

# Leftovers: what an installation leaves once no trusted record describes it.
# Names follow what staging places and what the GPL host writes while a game
# runs (Logger.cpp, CrashReport.cpp, AmdnrReport.cpp, LmxxfBackend.cpp).
# Finished "Save report" zips belong to the player and are never listed.
_AMDNR_OUTPUTS = re.compile(r"amdnr_crash\.log|amdnr-crash-trace\.log|AMDNR-crash-[^/]+\.dmp|AMDNR-report-[^/]+\.zip\.tmp")
# Any OptiScaler build writes these; AMDNR's own copies name it in their header.
_OPTISCALER_OUTPUTS = re.compile(r"OptiScaler\.log|OptiScaler\.previous(?:-\d+)?(?:\.[^/]+)?\.log|optiscaler_running_[^/]+\.marker")
# AMDNR's runtimes, logs and danielblnc's files, removed only beside other evidence.
_RUNTIME_FILES = ("LmxxfNrRuntime.dll", "LmxxfNrRuntime.pak", "lmxxf_backend.log", "lmxxf_vk_launch.pending",
                  "amd_bridge.log", "amd_presr.log", "dlssnr_on_amd.log", "dlssnr_amd_pass1.dll", "dlssnr_amd_pass2.dll",
                  "dlssnr_amd_pass3.dll", "dlssnr_on_amd_weights.bin", "danielblnc_ATTRIBUTION.txt", "danielblnc_LICENSE.txt")
# Names an OptiScaler host may use: this addon's proxies and those of a hand-copied Windows install.
_HOST_NAMES = ("winmm.dll", "version.dll", "dbghelp.dll", "wininet.dll", "winhttp.dll", "dxgi.dll", "d3d11.dll",
               "d3d12.dll", "dinput8.dll", "optiscaler.dll", "optiscaler.asi")
_GRAPHICS_NAMES = ("d3d12.dll", "d3d12core.dll")
_OPTISCALER_INI = "OptiScaler.ini".encode("utf-16-le")
_AMDNR_NAME = "AMDNR".encode("utf-16-le")
_VKD3D = b"vkd3d-proton"
# Library files AMDNR and the pre-SR host keep in OptiScaler/, and OptiPatcher.
_OPTISCALER_LIBRARIES = re.compile(r"OptiScaler/(?:(?:amd_fidelityfx_[^/]+|libxe(?:ss|ll)[^/]*|amdnr_[^/]+)\.dll"
                                   r"|D3D12_OptiScaler/D3D12Core\.dll|plugins/OptiPatcher\.asi)", re.IGNORECASE)
_AMDNR_NOTICES = ("AMDNR_NOTICE.txt", "AMDNR_RDNA3_BACKEND.txt", "NOTICE-AMDNR-RDNA3.txt", "RenoDX_ATTRIBUTION.txt",
                  "lmxxf_LICENSE.txt", "danielblnc_ATTRIBUTION.txt", "dlssg-to-fsr3_ATTRIBUTION.txt")
# Also shipped by games; removed only from a Licenses folder that holds AMDNR's notice.
_SHARED_NOTICES = ("DirectX_LICENSE.txt", "FidelityFX_v2_LICENSE.md", "XeSS_LICENSE.txt")
# Folders only AMDNR or this addon create; removed once nothing else is left in them.
_LEFTOVER_FOLDERS = ("amdnr-linux", "AMDNR-SR-E", "OptiScaler", "experimental_lighting")


def _sha(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _load_modules(root):
    # The existing CLI scripts use absolute sibling imports. Install temporary
    # bindings only while loading; each backend retains its own module objects
    # and ROOT constants. Do not modify another backend's modules or sys.path.
    with _module_lock:
        saved = {name: sys.modules.get(name, _missing) for name in ("proxies", "runtimes", "launch", "manage")}
        loaded = {}
        try:
            for name in ("proxies", "runtimes", "launch", "manage", "setup"):
                source = root / "linux" / (name + ".py")
                if name in ("proxies", "runtimes") and not source.is_file():
                    # Old release scripts have no helper. Use this launcher's
                    # trusted copy; alternate DLLs and optional components still
                    # require support in the actual game package before installation.
                    source = Path(__file__).resolve().parents[1] / (name + ".py")
                spec = importlib.util.spec_from_file_location(f"amdnr_launcher_{id(root)}_{name}", source)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                loaded[name] = module
                if name in saved:
                    sys.modules[name] = module
        finally:
            for name, previous in saved.items():
                if previous is _missing:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = previous
    return loaded


def _sync_dir(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic(path, *, source=None, data=None, link=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=".amdnr-write-", dir=path.parent, delete=False) as temporary:
        pending = Path(temporary.name)
        try:
            if source is not None:
                with Path(source).open("rb") as stream:
                    shutil.copyfileobj(stream, temporary)
            elif data is not None:
                temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
            if source is not None:
                shutil.copystat(source, pending)
            if link is not None:
                pending.unlink()
                pending.symlink_to(link)
            pending.replace(path)
            _sync_dir(path.parent)
        finally:
            pending.unlink(missing_ok=True)


def _json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


_default_components = {"neural_runtime": "lmxxf", "daniel": None, "optipatcher": None}


def _components(value):
    """Validate a package or record component description; old ones mean lmxxf only.

    An optional "host" entry names an alternate host build; without it the
    package uses the AMDNR host, so AMDNR descriptions stay unchanged. An
    optional "model_e" entry describes a Model E built on this PC.
    """
    if value is None:
        return dict(_default_components)
    if (not isinstance(value, dict) or set(value) - {"host", "model_e"} != set(_default_components) or
            value["neural_runtime"] not in ("lmxxf", "daniel") or
            (value["neural_runtime"] == "daniel") != isinstance(value["daniel"], dict)):
        raise ValueError("invalid optional component metadata")
    for name in ("daniel", "optipatcher", "host", "model_e"):
        item = value.get(name)
        if item is not None and (not isinstance(item, dict) or not isinstance(item.get("version"), str) or
                                 not re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", ""))):
            raise ValueError(f"invalid {name} component metadata")
    if "host" in value and (value["host"] is None or value["neural_runtime"] != "daniel" or
                            not re.fullmatch(r"[a-z][a-z0-9]{0,15}", value["host"].get("build", "")) or
                            value["host"]["build"] == "amdnr"):
        raise ValueError("invalid host component metadata")
    if "model_e" in value and (value["model_e"] is None or "host" in value):
        raise ValueError("invalid model_e component metadata")
    return value


def host_key(components):
    """The host build a component description uses; "amdnr" when none is named."""
    return ((components or {}).get("host") or {}).get("build", "amdnr")


def ini_value(data, section, key):
    """Read one effective key from INI bytes without interpreting anything else."""
    current, value = None, None
    for line in data.decode("utf-8", "surrogateescape").splitlines():
        stripped = line.strip().lstrip("\ufeff")
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1].strip()
        elif current == section and "=" in stripped and not stripped.startswith((";", "#")):
            name, _, text = stripped.partition("=")
            if name.strip() == key:
                value = text.strip()
    return value


def ini_with(data, settings):
    """Set (section, key) values in INI bytes, keeping every other byte.

    Existing keys keep their line ending; missing keys go at the end of their
    section, and a missing section is appended. Comments are never edited.
    """
    lines = data.decode("utf-8", "surrogateescape").splitlines(keepends=True)
    newline = "\r\n" if any(line.endswith("\r\n") for line in lines) else "\n"
    pending = dict(settings)
    section, ends = None, {}
    for index, line in enumerate(lines):
        stripped = line.strip().lstrip("\ufeff")
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip()
            ends[section] = index + 1
        elif section is not None and "=" in stripped and not stripped.startswith((";", "#")):
            key = (section, stripped.partition("=")[0].strip())
            ends[section] = index + 1
            if key in settings:
                ending = line[len(line.rstrip("\r\n")):] or newline
                lines[index] = f"{key[1]}={settings[key]}{ending}"
                pending.pop(key, None)
    missing = {}
    for (section_name, key), value in pending.items():
        missing.setdefault(section_name, []).append(f"{key}={value}{newline}")
    # Insert from the last section upwards so earlier positions stay valid.
    for section_name in sorted((name for name in missing if name in ends), key=ends.get, reverse=True):
        end = ends[section_name]
        if not lines[end - 1].endswith(("\n", "\r")):
            lines[end - 1] += newline
        lines[end:end] = missing[section_name]
    for section_name in (name for name in missing if name not in ends):
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += newline
        lines += [f"[{section_name}]{newline}", *missing[section_name]]
    return "".join(lines).encode("utf-8", "surrogateescape")


def _state(path):
    if path.is_symlink():
        return {"kind": "link", "target": str(path.readlink())}
    if path.is_file():
        return {"kind": "file", "sha256": _sha(path)}
    if path.exists():
        raise ConflictError(f"Expected a file, found a directory: {path}")
    return {"kind": "missing"}


def _contains(path, needles, limit=256 * 1024**2):
    """The needles found in the first `limit` bytes of a regular file; never follows a link."""
    found = set()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            return found
        overlap, tail, read = max(map(len, needles)) - 1, b"", 0
        while read < limit and (chunk := source.read(min(1024 * 1024, limit - read))):
            read += len(chunk)
            window = tail + chunk
            found.update(needle for needle in needles if needle in window)
            if len(found) == len(needles):
                break
            tail = window[-overlap:] if overlap else b""
    return found


class LauncherBackend:
    def __init__(self, release_root: Path, cache_dir: Path, state_dir: Path,
                 progress: Callable[[str], None] = lambda message: None):
        self.release_root = Path(release_root).expanduser().resolve()
        self.cache_dir = Path(cache_dir).expanduser().resolve()
        self.state_dir = Path(state_dir).expanduser().resolve()
        def report(message):
            try:
                progress(message)
            except Exception:
                pass  # UI teardown must never turn a committed action into failure
        self.progress = report
        self.operation_id = None
        modules = _load_modules(self.release_root)
        self.manage, self.setup, self.launch = modules["manage"], modules["setup"], modules["launch"]
        self.proxies, self.runtimes = modules["proxies"], modules["runtimes"]
        manifest = self.release_root / "RELEASE-MANIFEST.json"
        self.addon_version = None
        if manifest.is_file():
            release = json.loads(manifest.read_text()).get("release", "")
            if (not isinstance(release, str) or not release.startswith("linux-v") or
                    version_key(release.removeprefix("linux-v")) is None):
                raise BackendError("The Linux release manifest has an invalid version.")
            self.addon_version = release.removeprefix("linux-v")

    @staticmethod
    def _key(game):
        return hashlib.sha256(str(game).encode()).hexdigest()

    def _game(self, directory):
        game = Path(directory).expanduser().resolve()
        if not game.is_dir():
            raise BackendError(f"Game directory does not exist: {game}")
        return game

    def _safe(self, game, relative):
        if not isinstance(relative, str) or not relative or PurePosixPath(relative).as_posix() != relative:
            raise ConflictError(f"Invalid managed path: {relative!r}")
        return self.manage.safe_path(game, relative)

    def _transaction_dir(self, game):
        return self._safe(self.state_dir, "transactions/" + self._key(game))

    def _receipt_path(self, game):
        return self._safe(self.state_dir, "game-receipts/" + self._key(game) + ".json")

    def operation_receipt(self, game_dir):
        """Read the last committed action; does not recover or modify anything."""
        game = self._game(game_dir)
        path = self._receipt_path(game)
        if not path.exists():
            return None
        receipt = json.loads(path.read_text())
        if (receipt.get("schema") != 1 or receipt.get("game") != str(game) or
                receipt.get("action") not in ("install", "repair", "update", "uninstall", "switch_proxy") or
                not isinstance(receipt.get("operation_id"), str)):
            raise ConflictError("Invalid launcher operation receipt.")
        return receipt

    def _receipt_operation(self, game, action, package_id=None, *, addon_version=_missing):
        token = self.operation_id if self.operation_id is not None else uuid.uuid4().hex
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", token):
            raise BackendError("Invalid launcher operation identifier.")
        self.operation_id = None
        receipt = dict(schema=1, game=str(game), action=action, operation_id=token,
                       addon_version=self.addon_version if addon_version is _missing else addon_version,
                       package_id=package_id, committed_at=int(time.time()))
        return self._operation(self._receipt_path(game), data=_json_bytes(receipt))

    def recover(self, game_dir):
        """Resolve our interrupted transaction before reconciling Steam state."""
        game = self._game(game_dir)
        with self._lock(game):
            pass
        return self.inspect(game)

    @contextmanager
    def _lock(self, game):
        directory = self._safe(self.state_dir, "locks")
        directory.mkdir(parents=True, exist_ok=True)
        lock_path = self._safe(self.state_dir, "locks/" + self._key(game) + ".lock")
        with lock_path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise BackendError("Another launcher operation is already changing this game.") from error
            try:
                self._ensure_closed(game)
                self._recover(game)
                receipt = self.operation_receipt(game)
                if receipt and receipt["action"] == "uninstall" and not (game / self.manage.RECORD).exists():
                    self._prune_uninstalled(game)
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _running(self, game, proc_root=Path("/proc")):
        found = []
        for process in proc_root.iterdir():
            if not process.name.isdecimal() or int(process.name) == os.getpid():
                continue
            try:
                executable = (process / "exe").resolve(strict=True)
                active = executable == game or game in executable.parents
                if not active:
                    arguments = (process / "cmdline").read_bytes().split(b"\0")
                    for argument in arguments:
                        text = argument.decode(errors="replace").strip('"').replace("\\", "/")
                        if not text.lower().endswith(".exe"):
                            continue
                        if text[:3].lower() == "z:/":
                            text = text[2:]
                        candidate = Path(text)
                        if candidate.is_absolute() and game in candidate.resolve().parents:
                            active = True
                            break
                        if not candidate.is_absolute():
                            # Wine's Unix executable is outside the game tree,
                            # and its command line can use a relative or DOS
                            # .exe name. The working directory still identifies
                            # the installation that must remain untouched.
                            working_directory = (process / "cwd").resolve(strict=True)
                            if (working_directory == game or game in working_directory.parents or
                                    game in (working_directory / candidate).resolve().parents):
                                active = True
                                break
                if active:
                    found.append(int(process.name))
            except (OSError, ValueError):
                continue  # process exited or belongs to a different user
        return found

    def _ensure_closed(self, game):
        processes = self._running(game)
        if processes:
            raise GameRunning(f"Close this game before changing its installation (processes: {processes}).")

    def _record(self, game):
        if (game / self.manage.RECORD).is_symlink():
            raise ConflictError("The installation record is a link, so this launcher cannot manage the installation. "
                                "A mod manager may have moved AMDNR's files into its own folder.")
        path = self._safe(game, self.manage.RECORD)
        if not path.exists():
            return None
        try:
            record = json.loads(path.read_text())
            if record.get("version") != 1 or not isinstance(record["files"], list) or not isinstance(record["installed"], list):
                raise ValueError("unsupported record")
            metadata = record.get("launcher", {})
            if not isinstance(metadata, dict) or (metadata.get("addon_version") is not None and
                    version_key(metadata["addon_version"]) is None):
                raise ValueError("invalid launcher version metadata")
            _components(metadata.get("components"))
            created = metadata.get("created_directories", [])
            if not isinstance(created, list):
                raise ValueError("invalid created directory list")
            for directory in created:
                self._safe(game, directory)
            names = []
            for entry in record["files"]:
                self._safe(game, entry["path"])
                if entry["path"].split("/")[0] in (self.manage.RECORD, self.manage.BACKUP):
                    raise ValueError("reserved managed path")
                if not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) or not isinstance(entry["existed"], bool):
                    raise ValueError("invalid file metadata")
                if "backup_sha256" in entry and not re.fullmatch(r"[0-9a-f]{64}", entry["backup_sha256"]):
                    raise ValueError("invalid backup checksum")
                names.append(entry["path"])
            if len(set(names)) != len(names) or len(set(record["installed"])) != len(record["installed"]) or not set(record["installed"]) <= set(names):
                raise ValueError("duplicate or unknown installed paths")
            proxy, _ = self._proxy_metadata(record)
            proxy_paths = [name for name in names if name.casefold() in self.proxies.SUPPORTED]
            if proxy_paths != [proxy]:
                raise ValueError("proxy metadata does not match the managed host DLL")
            return record
        except (ValueError, KeyError, TypeError) as error:
            raise ConflictError(f"Cannot trust the existing installation record: {error}") from error

    @staticmethod
    def _version(directory):
        source = directory / "amdnr-linux/SOURCE.txt"
        if source.is_file() and not source.is_symlink():
            match = re.search(r"Host/runtime: (?:AMDNR ([^,\s]+)|OptiScaler AMD pre-SR ([^,\s]+))",
                              source.read_text(errors="replace"))
            if match:
                return match[1] or "presr-" + match[2]
        return "unknown"

    def _proxy_metadata(self, record):
        metadata = record.get("launcher", {})
        name = metadata.get("proxy_name", "winmm.dll")
        if self.proxies.normalize(name) != name:
            raise ValueError("invalid proxy name in installation record")
        history = metadata.get("proxy_history", [name])
        if (not isinstance(history, list) or not history or len(history) > len(self.proxies.SUPPORTED) or
                any(not isinstance(item, str) or self.proxies.normalize(item) != item for item in history) or
                len(set(history)) != len(history) or name not in history):
            raise ValueError("invalid proxy history in installation record")
        return name, list(history)

    def _owned_file(self, game, record, name, purpose="proxy"):
        entry = next((entry for entry in record["files"] if entry["path"] == name), None)
        path = self._safe(game, name)
        if (entry is None or name not in record["installed"] or not path.is_file() or
                _sha(path) != entry["sha256"]):
            raise ConflictError(f"The managed {purpose} file is missing or modified: {name}. Repair the installation first.")
        return entry, path

    def _switch_support(self, game, record, current):
        self._owned_file(game, record, current, "host")
        if current not in self.proxies.candidates(game, current=current):
            raise ConflictError("Another file occupies the current proxy name with different casing. Preserve it before continuing.")
        for name in ("amdnr-linux/proxies.py", "amdnr-linux/launch.py", "amdnr-linux/run.sh"):
            self._owned_file(game, record, name, "launch support")
        if any(entry["path"] == _proxy_config for entry in record["files"]):
            self._owned_file(game, record, _proxy_config, "proxy configuration")
            if self.proxies.read_config(game / "amdnr-linux") != current:
                raise ConflictError("Proxy configuration disagrees with the installation record. Repair the installation first.")
        elif (game / _proxy_config).exists() or (game / _proxy_config).is_symlink():
            raise ConflictError("An unowned proxy configuration already exists. Preserve it before switching.")

    def inspect(self, game_dir, executable=None):
        game = self._game(game_dir)
        result = dict(game_dir=str(game), installed=False, status="not_installed", version=None, addon_version=None,
                      host_version=None, release_root=None,
                      modified=[], missing=[], conflicts=[], health_issues=[], config_modified=False,
                      runtime_status="unverified", components=None, ini_runtime=None,
                      proxy_name=None, proxy_history=[], proxy_options=[], proxy_next=None,
                      proxy_switch_supported=False, proxy_switch_issue=None,
                      running=self._running(game), recovery_needed=(self._transaction_dir(game) / "journal.json").exists())
        try:
            record = self._record(game)
            if record:
                metadata = record.get("launcher", {})
                proxy, history = self._proxy_metadata(record)
                result.update(installed=True, version=metadata.get("addon_version") or metadata.get("version", self._version(game)),
                              addon_version=metadata.get("addon_version"), host_version=metadata.get("host_version", self._version(game)),
                              release_root=metadata.get("release_root"),
                              package_id=record.get("launcher", {}).get("package_id"), status="installed",
                              proxy_name=proxy, proxy_history=history, components=_components(metadata.get("components")))
                ini = self._safe(game, "OptiScaler.ini")
                if ini.is_file() and not ini.is_symlink() and ini.stat().st_size <= 4 * 1024**2:
                    result["ini_runtime"] = (ini_value(ini.read_bytes(), "DlssNr", "NrBackend") or "").lower() or None
                for entry in record["files"]:
                    path = self._safe(game, entry["path"])
                    if not path.is_file():
                        result["missing"].append(entry["path"])
                    elif _sha(path) != entry["sha256"]:
                        if entry["path"] == "OptiScaler.ini":
                            result["config_modified"] = True
                        else:
                            result["modified"].append(entry["path"])
                    if entry["existed"] and entry["path"] in record["installed"]:
                        backup = self._safe(game, self.manage.BACKUP + "/" + entry["path"])
                        if not backup.is_file():
                            result["conflicts"].append(f"Missing original backup: {entry['path']}")
                        elif entry.get("backup_sha256") and _sha(backup) != entry["backup_sha256"]:
                            result["conflicts"].append(f"Original backup was modified: {entry['path']}")
                self._inspect_launch_support(game, result)
                if result["missing"] or result["modified"] or result["health_issues"]:
                    result["status"] = "needs_repair"
            elif (game / self.manage.BACKUP).exists():
                result["conflicts"].append("Original backups exist without an installation record.")
            available = self.proxies.candidates(game, executable=executable, current=result["proxy_name"])
            result["proxy_options"] = [dict(name=name, available=name in available, current=name == result["proxy_name"])
                                       for name in (*available, *(name for name in self.proxies.SUPPORTED if name not in available))]
            if record:
                try:
                    self._switch_support(game, record, result["proxy_name"])
                except (ValueError, OSError, ConflictError) as error:
                    result["proxy_switch_issue"] = str(error)
                else:
                    result["proxy_switch_supported"] = True
                    try:
                        result["proxy_next"] = self.proxies.choose(game, requested="next", executable=executable,
                                                                  current=result["proxy_name"], tried=result["proxy_history"])
                    except (ValueError, OSError):
                        pass  # Exhaustion still permits an explicit manual retry.
        except (ValueError, OSError, ConflictError) as error:
            result["conflicts"].append(str(error))
        if not result["installed"]:
            result["leftovers"] = self.leftovers(game)
        if result["conflicts"]:
            result["status"] = "conflict"
        if result["recovery_needed"]:
            result["status"] = "interrupted"
        result["installation_health"] = "healthy" if result["status"] == "installed" else result["status"]
        return result

    def _inspect_launch_support(self, game, result):
        """Read launch prerequisites without executing code or inferring runtime success."""
        wrapper = self._safe(game, "amdnr-linux/run.sh")
        if wrapper.is_file() and not os.access(wrapper, os.X_OK):
            result["health_issues"].append("The game launch script is not executable. Repair the installation.")
        runtime_path = self._safe(game, "amdnr-linux/python-runtime.path")
        # Source/legacy installations intentionally use the system Python and
        # have no durable-runtime file. Do not impose the portable layout on them.
        if runtime_path.is_file():
            with runtime_path.open("rb") as source:
                raw = source.read(4097)
            try:
                value = raw.decode("utf-8").rstrip("\r\n")
                if (len(raw) > 4096 or not value or any(ord(c) < 32 for c in value)
                        or not Path(value).is_absolute()):
                    raise ValueError("invalid runtime path")
                runtime = Path(value)
                if not runtime.is_file() or not os.access(runtime, os.X_OK):
                    result["health_issues"].append(
                        "The shared Python runtime is missing or cannot run. Repair using the complete launcher download.")
            except (UnicodeError, ValueError):
                result["health_issues"].append("The shared Python runtime path is invalid. Repair the installation.")
        for entry in self._prefix_entries(game):
            if not Path(entry["path"]).is_file():
                result["health_issues"].append(f"A managed Proton prefix file is missing: {entry['path']}. Repair the installation.")

    def _find_compiler(self, supplied, roots):
        if supplied is not None:
            candidate = Path(supplied).expanduser().resolve()
            try:
                self.setup.validate_compiler(candidate)
                return candidate
            except (OSError, ValueError) as error:
                raise CompilerNeeded(str(error)) from error
        seen, examined = set(), 0
        for root in roots:
            root = Path(root).expanduser().resolve()
            if not root.is_dir() or root in seen:
                continue
            seen.add(root)
            for directory, children, files in os.walk(root, followlinks=False):
                examined += 1
                path = Path(directory)
                children[:] = sorted(name for name in children if name not in
                                     (".git", "node_modules", "shadercache", "workshop", ".cache")
                                     and not (path.name == "Launcher" and name == "OpenSource")
                                     and not (path / name).is_symlink())
                if len(path.relative_to(root).parts) >= 7:
                    children[:] = []
                for name in sorted(files):
                    if name.lower() == "d3dcompiler_47.dll":
                        candidate = path / name
                        try:
                            self.setup.validate_compiler(candidate)
                            return candidate.resolve()
                        except (OSError, ValueError):
                            pass
                if examined >= 5000:
                    break
            if examined >= 5000:
                break
        raise CompilerNeeded("No native 64-bit d3dcompiler_47.dll was found. Select one from a game installation; Wine's builtin DLL cannot be used.")

    def supports_components(self):
        """Whether this release can stage danielblnc's runtime and OptiPatcher."""
        return {"daniel", "optipatcher"} <= signature_inspect.signature(self.setup.prepare).parameters.keys()

    def supports_host_builds(self):
        """Whether this release can stage an alternate host build instead of AMDNR."""
        return "host" in signature_inspect.signature(self.setup.prepare).parameters and hasattr(self.runtimes, "HOST_BUILDS")

    def supports_model_e(self):
        """Whether this release can build and stage Model E (HelixSR)."""
        return ("model_e" in signature_inspect.signature(self.setup.prepare).parameters
                and hasattr(self.runtimes, "build_model_e"))

    def model_e_built(self):
        """Whether a Model E built earlier is ready in this launcher's download cache."""
        return self.supports_model_e() and self.runtimes.model_e_build(self.cache_dir / "downloads") is not None

    def prepare_package(self, compiler=None, search_roots=None, archive=None, components=None, accept_dlss_license=False):
        """components: {"daniel": version or None, "optipatcher": version or None,
        "host": an alternate host build key, or None/"amdnr" for AMDNR,
        "model_e": True to build and stage Model E}. accept_dlss_license
        records that the user accepted NVIDIA's licence for Model E's build."""
        options = {key: value for key, value in (components or {}).items() if value and value != "amdnr"}
        if set(options) - {"daniel", "optipatcher", "host", "model_e"}:
            raise BackendError("Unknown optional component.")
        if options and not self.supports_components():
            raise BackendError("This Linux package cannot add danielblnc's runtime or OptiPatcher. Update the launcher first.")
        if "host" in options and not self.supports_host_builds():
            raise BackendError("This Linux package cannot use another host build. Update the launcher first.")
        if "model_e" in options:
            if not self.supports_model_e():
                raise BackendError("This Linux package cannot add Model E. Update the launcher first.")
            options.update(model_e=True, accept_dlss_license=bool(accept_dlss_license), progress=self.progress)
        self.progress("Finding a native shader compiler…")
        compiler = self._find_compiler(compiler, search_roots or [])
        self.progress("Verifying the release and preparing pinned runtime files…")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        candidate = Path(tempfile.mkdtemp(prefix="candidate-", dir=self.cache_dir))
        try:
            package = self.setup.prepare(compiler, archive, candidate / "package", self.cache_dir / "downloads", **options)
        except BaseException:
            shutil.rmtree(candidate)
            raise
        self.progress("Package ready. No game files have been changed.")
        return package

    def _package(self, package):
        package = Path(package).expanduser().resolve()
        if not package.is_dir():
            raise BackendError(f"Prepared package does not exist: {package}")
        files = {}
        for path in sorted(package.rglob("*")):
            if path.is_symlink():
                raise ConflictError(f"Package contains a symlink: {path}")
            if not path.is_file():
                continue
            name = path.relative_to(package).as_posix()
            if name.split("/")[0] in (self.manage.RECORD, self.manage.BACKUP, self.manage.RECORD + ".tmp") or name.startswith("amdnr-linux/prefix"):
                raise ConflictError(f"Package contains reserved installation state: {name}")
            if name.casefold() in self.proxies.SUPPORTED and name != "winmm.dll":
                raise ConflictError("Prepared packages must contain only the canonical winmm.dll host proxy.")
            files[name] = dict(source=path, sha256=_sha(path))
        required = {"winmm.dll", "OptiScaler.ini", "d3d12.dll", "d3d12core.dll", "d3dcompiler_47.dll",
                    "amdnr-linux/run.sh", "amdnr-linux/x86_64-unix/amdhip64_7.so",
                    "amdnr-linux/x86_64-windows/amdhip64_7.dll", "amdnr-linux/x86_64-unix/libamdnr_signal_guard.so"}
        if not required <= files.keys() or "amdnr-linux/x86_64-unix/amdhip64_7.dll.so" in files:
            raise BackendError("Not a current prepared PE/Unixlib Linux addon package.")
        return package, files

    def _package_components(self, package, files):
        """What the package stages; the files must agree with its description."""
        try:
            path = files.get("amdnr-linux/components.json")
            components = _components(json.loads(path["source"].read_text()) if path else None)
        except (OSError, ValueError) as error:
            raise ConflictError(f"The prepared package's component list is invalid: {error}") from error
        daniel = [name for name in (*self.runtimes.DANIEL_FILES,) if name in files]
        if components["daniel"]:
            if len(daniel) != len(self.runtimes.DANIEL_FILES) or any(
                    files[name]["sha256"] != components["daniel"]["pass_sha256"] for name in self.runtimes.DANIEL_PASSES):
                raise ConflictError("The prepared package's danielblnc runtime files do not match its description.")
        elif daniel:
            raise ConflictError("The prepared package has undeclared danielblnc runtime files.")
        plugin = files.get(self.runtimes.OPTIPATCHER_PATH)
        if bool(plugin) != bool(components["optipatcher"]) or (
                plugin and plugin["sha256"] != components["optipatcher"]["sha256"]):
            raise ConflictError("The prepared package's OptiPatcher plugin does not match its description.")
        folder = getattr(self.runtimes, "MODEL_E_FOLDER", "AMDNR-SR-E")
        provider = [name for name in files if name.split("/")[0] == folder]
        if components.get("model_e"):
            dlls = getattr(self.runtimes, "MODEL_E_DLLS", ())
            receipt = files.get(f"{folder}/{getattr(self.runtimes, 'MODEL_E_RECEIPT', '')}")
            if (not dlls or not receipt or any(files.get(f"{folder}/{name}", {}).get("sha256") != components["model_e"]["sha256"]
                                               for name in dlls)):
                raise ConflictError("The prepared package's Model E files do not match its description.")
        elif provider:
            raise ConflictError("The prepared package has undeclared Model E files.")
        return components

    def _prefix_entries(self, game):
        journal = self._safe(game, "amdnr-linux/prefixes.json")
        if not journal.exists():
            return []
        try:
            entries = json.loads(journal.read_text())
            if not isinstance(entries, list):
                raise ValueError("expected a list")
            seen = set()
            for entry in entries:
                path = Path(entry["path"])
                if (not path.is_absolute() or path.parts[-4:-1] != ("drive_c", "windows", "system32") or
                        path.name not in _PREFIX_FILES or str(path) in seen):
                    raise ValueError("invalid prefix path")
                seen.add(str(path))
                if any(parent.is_symlink() for parent in path.parents) or path.is_symlink():
                    raise ValueError(f"prefix ownership changed: {path}")
                if not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
                    raise ValueError("invalid prefix checksum")
                if path.exists() and (not path.is_file() or _sha(path) != entry["sha256"]):
                    raise ValueError(f"prefix file modified outside this installation: {path}")
                if "backup" in entry:
                    expected = hashlib.sha256(str(path).encode()).hexdigest() + ".dll"
                    if entry["backup"] != expected:
                        raise ValueError("invalid prefix backup name")
                    backup = self._safe(game, "amdnr-linux/prefix-backups/" + expected)
                    if not backup.is_file() or _sha(backup) != entry["backup_sha256"]:
                        raise ValueError(f"missing or changed prefix backup: {backup}")
                if "original_link" in entry and (not isinstance(entry["original_link"], str) or "backup" in entry):
                    raise ValueError("invalid original prefix link")
            return entries
        except (KeyError, ValueError, TypeError) as error:
            raise ConflictError(f"Cannot update owned Proton prefix files: {error}") from error

    def _operation(self, path, *, source=None, data=None, link=None):
        operation = {"path": path}
        if source is not None:
            operation.update(source=Path(source), after={"kind": "file", "sha256": _sha(source)})
        elif data is not None:
            operation.update(data=data, after={"kind": "file", "sha256": hashlib.sha256(data).hexdigest()})
        elif link is not None:
            operation.update(link=link, after={"kind": "link", "target": link})
        else:
            operation["after"] = {"kind": "missing"}
        return operation

    def _commit(self, game, operations):
        self._ensure_closed(game)
        directory = self._transaction_dir(game)
        directory.mkdir(parents=True, exist_ok=False)
        journal = {"schema": 1, "game": str(game), "status": "prepared", "entries": []}
        try:
            for index, operation in enumerate(operations):
                path = operation["path"]
                before = _state(path)
                if operation.get("require_missing") and (before["kind"] != "missing" or
                        self.proxies.occupied(game, path.name)):
                    raise ConflictError(f"A new proxy target became occupied before installation: {path}")
                entry = dict(path=str(path), before=before, after=operation["after"])
                if "via" in operation:
                    entry["via"] = str(operation["via"])
                if before["kind"] == "file":
                    backup = directory / str(index)
                    shutil.copy2(path, backup)
                    with backup.open("rb") as stream:
                        os.fsync(stream.fileno())
                    entry["backup"] = str(index)
                entry["created_dirs"] = [str(parent) for parent in path.parents if not parent.exists()]
                journal["entries"].append(entry)
            _atomic(directory / "journal.json", data=_json_bytes(journal))
            for operation in operations:
                self._apply(operation)
            journal["status"] = "committed"
            _atomic(directory / "journal.json", data=_json_bytes(journal))
        except BaseException:
            if (directory / "journal.json").exists():
                self._recover(game, force_rollback=True)
            else:
                shutil.rmtree(directory)
            raise
        try:
            shutil.rmtree(directory)
        except OSError:
            pass  # committed journal is safe to clean up on the next operation

    def _apply(self, operation):
        path = operation["path"]
        if operation["after"]["kind"] == "missing":
            path.unlink(missing_ok=True)
            if path.parent.exists():
                _sync_dir(path.parent)
        else:
            if "source" in operation and _sha(operation["source"]) != operation["after"]["sha256"]:
                raise ConflictError("Prepared package changed during installation.")
            _atomic(path, **{key: operation[key] for key in ("source", "data", "link") if key in operation})

    def _recover(self, game, force_rollback=False):
        directory = self._transaction_dir(game)
        journal_path = directory / "journal.json"
        if journal_path.is_symlink():
            raise ConflictError("Interrupted installation journal must not be a symlink.")
        if not journal_path.exists():
            if directory.exists():
                shutil.rmtree(directory)  # interrupted snapshot, before mutations
            return
        journal = json.loads(journal_path.read_text())
        if journal.get("schema") != 1 or journal.get("game") != str(game) or journal.get("status") not in ("prepared", "committed"):
            raise ConflictError("Unrecognized interrupted installation journal.")
        if journal.get("status") == "committed" and not force_rollback:
            shutil.rmtree(directory)
            return
        # Every snapshot is private launcher state. Validate path scope and
        # current content before restoring, so later user edits are not erased.
        for entry in journal["entries"]:
            path = Path(entry["path"])
            in_game = game in path.parents
            prefix = path.parts[-4:-1] == ("drive_c", "windows", "system32") and path.name in _PREFIX_FILES
            saved_config = path == self.state_dir / "configs" / (self._key(game) + ".ini")
            receipt = path == self.state_dir / "game-receipts" / (self._key(game) + ".json")
            # A mod manager's file, removed with the link to it that the same journal puts back.
            via = entry.get("via")
            linked = isinstance(via, str) and game in Path(via).parents and any(
                other["path"] == via and other["before"].get("kind") == "link"
                and os.path.realpath(Path(via).parent / other["before"]["target"]) == str(path)
                for other in journal["entries"])
            if not path.is_absolute() or not (in_game or prefix or saved_config or receipt or linked) or any(parent.is_symlink() for parent in path.parents):
                raise ConflictError(f"Unsafe recovery path: {path}")
            if _state(path) not in (entry["before"], entry["after"]):
                raise ConflictError(f"A file changed after an interrupted operation; recovery stopped: {path}")
            if entry["before"]["kind"] == "file":
                backup = directory / entry.get("backup", "")
                if backup.parent != directory or backup.is_symlink() or not backup.is_file() or _sha(backup) != entry["before"]["sha256"]:
                    raise ConflictError("An interrupted operation's recovery copy is missing or changed.")
            if any(Path(value) not in path.parents for value in entry.get("created_dirs", [])):
                raise ConflictError("Invalid recovery directory list.")
        self.progress("Recovering an interrupted installation…")
        for entry in reversed(journal["entries"]):
            path, before = Path(entry["path"]), entry["before"]
            if before["kind"] == "file":
                _atomic(path, source=directory / entry["backup"])
            elif before["kind"] == "link":
                _atomic(path, link=before["target"])
            else:
                path.unlink(missing_ok=True)
        for parent in sorted({Path(value) for entry in journal["entries"] for value in entry.get("created_dirs", [])}, key=lambda p: len(p.parts), reverse=True):
            try:
                parent.rmdir()
            except OSError:
                pass
        shutil.rmtree(directory)

    def _change(self, game_dir, package, mode, proxy="auto", executable=None, seeds=None):
        game = self._game(game_dir)
        package, candidate = self._package(package)
        components = self._package_components(package, candidate)
        identity = hashlib.sha256(_json_bytes({name: info["sha256"] for name, info in candidate.items()})).hexdigest()
        if game == package or game in package.parents or package in game.parents:
            raise ConflictError("Prepared package and game directories must be separate.")
        with self._lock(game):
            old = self._record(game)
            if mode == "install" and (old or (game / self.manage.BACKUP).exists()):
                raise ConflictError("This game already has an installation or backup. Use Repair or Update.")
            if mode != "install" and not old:
                raise ConflictError("No managed installation was found. Use Install first.")
            current, history = self._proxy_metadata(old) if old else (None, [])
            if (current is not None and isinstance(proxy, str) and proxy.strip().lower() == "auto" and
                    self.proxies.occupied(game, current, current=current)):
                raise ConflictError("The current proxy name has a foreign casing collision. Preserve the conflicting file before repairing or updating.")
            try:
                selected = self.proxies.choose(game, requested=proxy, executable=executable, current=current,
                                               tried=history if isinstance(proxy, str) and proxy.strip().lower() == "next" else ())
            except ValueError as error:
                raise ConflictError(str(error)) from error
            if selected != "winmm.dll" and "amdnr-linux/proxies.py" not in candidate:
                raise ConflictError("This package does not support alternate DLL proxies. Prepare a current package first.")
            if current is not None and selected != current:
                self._owned_file(game, old, current, "host")
            candidate[selected] = candidate.pop("winmm.dll")
            config_data = _json_bytes({"proxy": selected})
            candidate[_proxy_config] = {"data": config_data, "sha256": hashlib.sha256(config_data).hexdigest()}
            if selected not in history:
                history.append(selected)
            installed_version = old.get("launcher", {}).get("addon_version") if old else None
            if installed_version:
                candidate_version = version_key(self.addon_version)
                if candidate_version is None or candidate_version < version_key(installed_version):
                    raise ConflictError(f"Linux addon {installed_version} is installed. This package is older or has no release version; a downgrade was refused.")
                if mode == "repair" and self.addon_version != installed_version:
                    raise ConflictError(f"Repair requires Linux addon {installed_version}. Use Update to install {self.addon_version}.")
            if not old:
                for name in ("prefixes.json", "prefixes.json.tmp", "prefix-backups", "prefixes.lock"):
                    if (game / "amdnr-linux" / name).exists():
                        raise ConflictError("Existing Proton prefix ownership must be restored before installing.")
            previous = {entry["path"]: entry for entry in old["files"]} if old else {}
            installed = set(old["installed"]) if old else set()
            self._component_settings(game, old, candidate, components, seeds)
            previous_ini = candidate["OptiScaler.ini"].pop("previous", None)
            metadata = old.get("launcher", {}) if old else {}
            created = set(metadata.get("created_directories", []))
            for name in candidate:
                for parent in PurePosixPath(name).parents:
                    if parent.parts and not (game / parent).exists():
                        created.add(parent.as_posix())
            operations, files = [], []
            marker = self._safe(game, self.manage.BACKUP + "/.launcher-backup-v1")
            if not marker.exists():
                operations.append(self._operation(marker, data=_backup_marker))
            elif marker.read_bytes() != _backup_marker:
                raise ConflictError("The original-backup folder contains an unrecognized launcher marker.")
            if previous_ini is not None:
                operations.append(self._operation(self._safe(self.state_dir, "configs/" + self._key(game) + ".ini"),
                                                  data=previous_ini))
            for name, info in candidate.items():
                target = self._safe(game, name)
                prior = previous.get(name)
                if name == selected and prior is None and (target.exists() or target.is_symlink()):
                    raise ConflictError(f"The selected proxy slot became occupied: {name}")
                existed = prior["existed"] if prior else target.exists()
                backup = self._safe(game, self.manage.BACKUP + "/" + name)
                if prior and name in installed and existed and not backup.is_file():
                    raise ConflictError(f"Original backup is missing: {name}")
                if prior and existed and prior.get("backup_sha256") and _sha(backup) != prior["backup_sha256"]:
                    raise ConflictError(f"Original backup was modified: {name}")
                if old and prior is None and target.exists():
                    raise ConflictError(f"New package file conflicts with an unrelated existing file: {name}")
                if existed and (prior is None or name not in installed):
                    if backup.exists():
                        raise ConflictError(f"Unexpected original backup: {backup}")
                    operations.append(self._operation(backup, source=target))
                preserve = (old is not None and name == "OptiScaler.ini" and target.is_file()
                            and not info.get("rewritten"))
                checksum = _sha(target) if preserve else info["sha256"]
                entry = dict(path=name, sha256=checksum, existed=existed)
                if existed:
                    entry["backup_sha256"] = _sha(backup if prior and name in installed else target)
                files.append(entry)
                restore_wrapper_mode = name == "amdnr-linux/run.sh" and target.is_file() and not os.access(target, os.X_OK)
                if not preserve and (not target.is_file() or _sha(target) != checksum or restore_wrapper_mode):
                    operation = self._operation(target, **{key: info[key] for key in ("source", "data") if key in info})
                    if name == selected and prior is None:
                        operation["require_missing"] = True
                    operations.append(operation)
            for name, prior in previous.items():
                if name in candidate or name not in installed:
                    continue
                target = self._safe(game, name)
                if target.exists() and _sha(target) != prior["sha256"]:
                    raise ConflictError(f"Obsolete managed file was modified; preserve or restore it before updating: {name}")
                if prior["existed"]:
                    backup = self._safe(game, self.manage.BACKUP + "/" + name)
                    if not backup.is_file():
                        raise ConflictError(f"Original backup is missing: {name}")
                    if prior.get("backup_sha256") and _sha(backup) != prior["backup_sha256"]:
                        raise ConflictError(f"Original backup was modified: {name}")
                    operations.append(self._operation(target, source=backup))
                    operations.append(self._operation(backup))
                else:
                    operations.append(self._operation(target))
            journal = self._prefix_entries(game)
            prefix_entries = []
            for entry in journal:
                path = Path(entry["path"])
                if path.name == "amdhip64_7.dll":
                    # An earlier wrapper's lowercase bridge, which Proton-CachyOS deletes:
                    # the wrapper installs AMDHIP64_7.DLL at the next launch instead.
                    if path.exists():
                        operations.append(self._operation(path))
                    continue
                source_name = "amdnr-linux/x86_64-windows/amdhip64_7.dll" if path.name == "AMDHIP64_7.DLL" else "d3dcompiler_47.dll"
                info = candidate[source_name]
                if not path.exists() or _sha(path) != info["sha256"]:
                    operations.append(self._operation(path, source=info["source"]))
                entry["sha256"] = info["sha256"]
                prefix_entries.append(entry)
            if journal:
                operations.append(self._operation(self._safe(game, "amdnr-linux/prefixes.json"), data=_json_bytes(prefix_entries)))
            record = dict(version=1, files=files, installed=[entry["path"] for entry in files],
                          launcher=dict(version=self.addon_version or self._version(package), addon_version=self.addon_version,
                                        host_version=self._version(package), release_root=str(self.release_root),
                                        package_id=identity, updated_at=int(time.time()),
                                        proxy_name=selected, proxy_history=history, components=components,
                                        created_directories=sorted(created)))
            operations.append(self._operation(self._safe(game, self.manage.RECORD), data=_json_bytes(record)))
            receipt = self._receipt_operation(game, mode, identity)
            record["launcher"]["operation_id"] = json.loads(receipt["data"])["operation_id"]
            operations[-1] = self._operation(self._safe(game, self.manage.RECORD), data=_json_bytes(record))
            operations.append(receipt)
            self.progress({"install": "Installing", "repair": "Repairing", "update": "Updating"}[mode] + " the Linux addon…")
            self._commit(game, operations)
            self.progress("Installation ready. Your original backups and settings have been preserved.")
        return self.inspect(game, executable=executable)

    def _component_settings(self, game, old, candidate, components, seeds):
        """Keep OptiScaler.ini consistent with the files being installed.

        A fresh install starts from the package's INI plus per-game seeds. An
        existing INI is preserved, except where the chosen components changed
        or the selected runtime's files would no longer exist; then only those
        keys are rewritten, keeping the user's other settings and comments.
        """
        source = candidate.get("OptiScaler.ini")
        target = self._safe(game, "OptiScaler.ini")
        if old is None or not target.is_file():
            if not seeds:
                return
            data = ini_with(Path(source["source"]).read_bytes(), seeds)
        else:
            before = _components(old.get("launcher", {}).get("components"))
            current = target.read_bytes()
            if host_key(components) != host_key(before):
                # Host builds read different settings: start from the new host's
                # own INI; _change keeps the previous one in the state folder.
                data = ini_with(Path(source["source"]).read_bytes(), seeds or {})
                candidate["OptiScaler.ini"] = dict(data=data, sha256=hashlib.sha256(data).hexdigest(),
                                                   rewritten=True, previous=current)
                return
            settings = {}
            if components["neural_runtime"] != before["neural_runtime"] or (
                    components["neural_runtime"] == "lmxxf" and
                    (ini_value(current, "DlssNr", "NrBackend") or "").lower() == "daniel"):
                settings[("DlssNr", "NrBackend")] = components["neural_runtime"]
            if bool(components["optipatcher"]) != bool(before["optipatcher"]):
                default = ini_value(Path(source["source"]).read_bytes(), "Plugins", "LoadAsiPlugins") or "auto"
                settings[("Plugins", "LoadAsiPlugins")] = "true" if components["optipatcher"] else default
            if bool(components.get("model_e")) != bool(before.get("model_e")):
                # Model E is AMDNR's FFX provider choice; without it the game's own FSR provider runs.
                settings[("Upscalers", "FfxSRProvider")] = "helix_e" if components.get("model_e") else "auto"
                settings[("Libraries", "HelixSRPath")] = (getattr(self.runtimes, "MODEL_E_FOLDER", "AMDNR-SR-E")
                                                          if components.get("model_e") else "")
            if not settings:
                return
            data = ini_with(current, settings)
        candidate["OptiScaler.ini"] = dict(data=data, sha256=hashlib.sha256(data).hexdigest(), rewritten=True)

    def install(self, game_dir, package, proxy="auto", executable=None, seeds=None):
        return self._change(game_dir, package, "install", proxy, executable, seeds)

    # seeds apply only where OptiScaler.ini starts fresh: a missing INI or a host build change.
    def repair(self, game_dir, package, proxy="auto", executable=None, seeds=None):
        return self._change(game_dir, package, "repair", proxy, executable, seeds)

    def update(self, game_dir, package, proxy="auto", executable=None, seeds=None):
        return self._change(game_dir, package, "update", proxy, executable, seeds)

    def switch_proxy(self, game_dir, proxy="next", executable=None):
        """Move the verified installed host to a safe alias without downloads."""
        game = self._game(game_dir)
        with self._lock(game):
            record = self._record(game)
            if record is None:
                raise ConflictError("No managed installation was found. Install the addon first.")
            current, history = self._proxy_metadata(record)
            self._switch_support(game, record, current)
            try:
                selected = self.proxies.choose(game, requested=proxy, executable=executable,
                                               current=current, tried=history if isinstance(proxy, str) and proxy.strip().lower() == "next" else ())
            except ValueError as error:
                raise ConflictError(str(error)) from error
            entries = {entry["path"]: entry for entry in record["files"]}
            host_entry, host = self._owned_file(game, record, current, "host")
            operations = []
            if selected != current:
                # Copy first: the old host remains the verified source until its
                # new alias has been committed. Recovery snapshots cover both.
                operation = self._operation(self._safe(game, selected), source=host)
                operation["require_missing"] = True
                operations.append(operation)
                if host_entry["existed"]:
                    backup = self._safe(game, self.manage.BACKUP + "/" + current)
                    if not backup.is_file() or (host_entry.get("backup_sha256") and _sha(backup) != host_entry["backup_sha256"]):
                        raise ConflictError(f"Original proxy backup is missing or modified: {current}")
                    operations.append(self._operation(host, source=backup))
                    operations.append(self._operation(backup))
                else:
                    operations.append(self._operation(host))
                entries.pop(current)
                entries[selected] = dict(path=selected, sha256=host_entry["sha256"], existed=False)
            config = _json_bytes({"proxy": selected})
            prior_config = entries.get(_proxy_config)
            if prior_config and prior_config["existed"]:
                backup = self._safe(game, self.manage.BACKUP + "/" + _proxy_config)
                if not backup.is_file() or (prior_config.get("backup_sha256") and _sha(backup) != prior_config["backup_sha256"]):
                    raise ConflictError("Original proxy configuration backup is missing or modified.")
            entries[_proxy_config] = dict(prior_config or {"path": _proxy_config, "existed": False},
                                          sha256=hashlib.sha256(config).hexdigest())
            operations.append(self._operation(self._safe(game, _proxy_config), data=config))
            if selected not in history:
                history.append(selected)
            metadata = record.setdefault("launcher", {})
            metadata.update(proxy_name=selected, proxy_history=history, updated_at=int(time.time()))
            record["files"] = list(entries.values())
            record["installed"] = [name for name in record["installed"] if name not in (current, _proxy_config)]
            record["installed"].extend([selected, _proxy_config])
            receipt = self._receipt_operation(game, "switch_proxy", metadata.get("package_id"),
                                              addon_version=metadata.get("addon_version"))
            metadata["operation_id"] = json.loads(receipt["data"])["operation_id"]
            operations.extend([self._operation(self._safe(game, self.manage.RECORD), data=_json_bytes(record)), receipt])
            self.progress(f"Switching the game proxy to {selected}…")
            self._commit(game, operations)
            self.progress("Proxy changed. Launch the game to check whether its host loads correctly.")
        return self.inspect(game, executable=executable)

    def uninstall(self, game_dir):
        game = self._game(game_dir)
        with self._lock(game):
            record = self._record(game)
            if not record:
                raise ConflictError("No managed installation was found.")
            operations = []
            installed = set(record["installed"])
            for entry in record["files"]:
                name = entry["path"]
                if name not in installed:
                    continue
                target = self._safe(game, name)
                if target.exists() and _sha(target) != entry["sha256"] and name != "OptiScaler.ini":
                    raise ConflictError(f"Managed file was modified: {name}. Repair it before uninstalling.")
                if name == "OptiScaler.ini" and target.exists():
                    saved = self._safe(self.state_dir, "configs/" + self._key(game) + ".ini")
                    operations.append(self._operation(saved, source=target))
                if entry["existed"]:
                    backup = self._safe(game, self.manage.BACKUP + "/" + name)
                    if not backup.is_file():
                        raise ConflictError(f"Original backup is missing: {name}")
                    if entry.get("backup_sha256") and _sha(backup) != entry["backup_sha256"]:
                        raise ConflictError(f"Original backup was modified: {name}")
                    operations.append(self._operation(target, source=backup))
                    operations.append(self._operation(backup))
                else:
                    operations.append(self._operation(target))
            for entry in self._prefix_entries(game):
                path = Path(entry["path"])
                if "original_link" in entry:
                    operations.append(self._operation(path, link=entry["original_link"]))
                elif "backup" in entry:
                    backup = self._safe(game, "amdnr-linux/prefix-backups/" + entry["backup"])
                    operations.append(self._operation(path, source=backup))
                    operations.append(self._operation(backup))
                else:
                    operations.append(self._operation(path))
            for name in ("amdnr-linux/prefixes.json", "amdnr-linux/prefixes.lock", self.manage.RECORD):
                operations.append(self._operation(self._safe(game, name)))
            marker = self._safe(game, self.manage.BACKUP + "/.launcher-backup-v1")
            if marker.exists():
                if marker.read_bytes() != _backup_marker:
                    raise ConflictError("The original-backup folder contains an unrecognized launcher marker.")
                operations.append(self._operation(marker))
            operations.append(self._receipt_operation(game, "uninstall"))
            created = record.get("launcher", {}).get("created_directories", [])
            self.progress("Restoring original game and Proton prefix files…")
            self._commit(game, operations)
            self._prune_uninstalled(game)
            # Directories this launcher created (plugins/) go only when empty.
            for directory in sorted(created, key=lambda name: len(PurePosixPath(name).parts), reverse=True):
                path = self._safe(game, directory)
                if path.is_dir() and not path.is_symlink():
                    try:
                        path.rmdir()
                    except OSError:
                        pass
            self.progress("Original files restored. Your addon settings are saved in the launcher's state folder.")
        return self.inspect(game)

    def _prune_uninstalled(self, game):
        # Repeatable after a crash following the committed uninstall receipt.
        # Only empty directories are removed; logs or other user files remain.
        for root in (game / self.manage.BACKUP, game / "amdnr-linux"):
            if root.is_dir() and not root.is_symlink():
                for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                    if path.is_dir() and not path.is_symlink():
                        try:
                            path.rmdir()
                        except OSError:
                            pass
                try:
                    root.rmdir()
                except OSError:
                    pass

    def leftovers(self, game_dir):
        """List what an installation this launcher no longer recognises left in a game folder.

        Read-only. "remove" and "restore" are paths relative to the game folder;
        a restored file gets back the original kept in the backup folder.
        "folders" are AMDNR's folders with nothing left in them, "prefix" the
        Proton prefix files the wrapper replaced, "kept" what may be AMDNR's but
        cannot be shown to be (with the reason), and "issues" why cleaning cannot
        run. A file is listed only on evidence: this addon's own folders, or
        AMDNR named inside a host DLL, OptiScaler.ini or the host's logs.
        "linked" maps listed paths that are a mod manager's links to the files
        they point to (see _linked_target), which are removed with them.
        """
        game = self._game(game_dir)
        plan = dict(remove=[], restore=[], folders=[], prefix=[], kept=[], issues=[], linked={}, save_config=False)
        try:
            self._scan_leftovers(game, plan)
        except (OSError, ValueError, ConflictError) as error:
            plan["issues"].append(str(error))
            plan["linked"] = {}
        return plan

    def _scan_leftovers(self, game, plan):
        manage = self.manage
        try:
            if self._record(game):
                plan["issues"].append("AMDNR is installed here. Use Uninstall and restore originals instead.")
                return
        except ConflictError:
            # An unreadable or linked record is a leftover too, unless a newer launcher wrote it.
            try:
                version = json.loads((game / manage.RECORD).read_text()).get("version")
            except (OSError, ValueError, AttributeError):
                version = None
            if isinstance(version, int) and version > 1:
                plan["issues"].append("A newer AMDNR Linux Manager installed AMDNR here. Use it to uninstall.")
                return
        remove, candidates, graphics, hosts = [], [], [], {}
        traces = amdnr = False
        ini = compiler = None
        for name in (manage.RECORD, manage.RECORD + ".tmp"):
            if self._leftover_file(game, name, plan):
                remove.append(name)
                traces = True
        backup = game / manage.BACKUP
        if backup.is_symlink():
            raise ConflictError(f"The original-backup folder is a link: {backup}")
        if backup.is_dir():
            traces = True
            for relative in self._tree(game, manage.BACKUP, plan):
                path = self._leftover_file(game, relative, plan)
                if path is None:
                    continue
                original = relative[len(manage.BACKUP) + 1:]
                if original == ".launcher-backup-v1":
                    if path.read_bytes() != _backup_marker:
                        raise ConflictError("The original-backup folder contains an unrecognized launcher marker.")
                else:
                    target = self._leftover_path(game, original)
                    if target.exists() and not target.is_file():
                        raise ConflictError(f"Expected a file, found a directory: {target}")
                    plan["restore"].append(original)
                remove.append(relative)
        bundle = game / "amdnr-linux"
        if bundle.is_symlink():
            raise ConflictError(f"The amdnr-linux folder is a link: {bundle}")
        if bundle.is_dir():
            traces = True
            self._leftover_prefix(game, plan)
            remove += [relative for relative in self._tree(game, "amdnr-linux", plan)
                       if self._leftover_file(game, relative, plan)]
        for relative in self._tree(game, "AMDNR-SR-E", plan):
            if self._leftover_file(game, relative, plan):
                remove.append(relative)
                amdnr = True
        with os.scandir(game) as listing:
            names = sorted(entry.name for entry in listing)
        for name in names:
            folded = name.casefold()
            if not (folded in _HOST_NAMES or folded in _GRAPHICS_NAMES or name in _RUNTIME_FILES
                    or name in ("OptiScaler.ini", "d3dcompiler_47.dll") or name.startswith(".amdnr-write-")
                    or _AMDNR_OUTPUTS.fullmatch(name) or _OPTISCALER_OUTPUTS.fullmatch(name)):
                continue
            dll = folded in _HOST_NAMES or folded in _GRAPHICS_NAMES
            # A linked DLL cannot be read safely, and its name alone does not make it AMDNR's.
            path = self._leftover_file(game, name, plan, note=not dll)
            if path is None:
                continue
            if dll:
                found = _contains(path, (_OPTISCALER_INI, _AMDNR_NAME, _VKD3D))
                if _OPTISCALER_INI in found:
                    hosts[name] = _AMDNR_NAME in found
                    amdnr |= hosts[name]
                elif _VKD3D in found and folded in _GRAPHICS_NAMES:
                    graphics.append(name)
            elif name == "OptiScaler.ini":
                ini = b"AMDNR" in _contains(path, (b"AMDNR",), 4 * 1024**2)
                amdnr |= ini
            elif name == "d3dcompiler_47.dll":
                compiler = name
            elif name.startswith(".amdnr-write-"):
                remove.append(name)  # an interrupted write of this launcher
                traces = True
            elif _AMDNR_OUTPUTS.fullmatch(name):
                remove.append(name)
                amdnr = True
            else:
                if _OPTISCALER_OUTPUTS.fullmatch(name):
                    amdnr |= b"AMDNR" in _contains(path, (b"AMDNR",), 64 * 1024)
                candidates.append(name)
        if not (traces or amdnr):
            plan["kept"].clear()  # nothing here is AMDNR's, so nothing was held back either
            plan["linked"] = {}
            return
        remove += candidates
        # Another OptiScaler build is this addon's only under a name it installs hosts as.
        foreign = [name for name, ours in hosts.items() if not (ours or (traces and name in self.proxies.SUPPORTED))]
        remove += [name for name in hosts if name not in foreign]
        plan["kept"] += [dict(path=name, reason="an OptiScaler build that does not name AMDNR") for name in foreign]
        if ini is not None and (ini or len(foreign) < len(hosts)):
            remove.append("OptiScaler.ini")
        if traces:
            remove += graphics
            if compiler and compiler not in plan["restore"]:
                if backup.is_dir():
                    remove.append(compiler)  # originals are backed up, so the installation added it
                else:
                    plan["kept"].append(dict(path=compiler, reason="it may be the game's own shader compiler"))
        for relative in self._tree(game, "OptiScaler", plan):
            if self._leftover_file(game, relative, plan):
                if PurePosixPath(relative).name.startswith(".amdnr-write-") or (
                        _OPTISCALER_LIBRARIES.fullmatch(relative) and not foreign):
                    remove.append(relative)
                else:
                    plan["kept"].append(dict(path=relative, reason="AMDNR does not install it" if not foreign
                                             else "the OptiScaler build that stays uses it"))
        notices = [relative for relative in self._tree(game, "Licenses", plan) if relative.count("/") == 1]
        shared = "Licenses/AMDNR_NOTICE.txt" in notices
        remove += [relative for relative in notices
                   if (relative[9:] in _AMDNR_NOTICES or (shared and relative[9:] in _SHARED_NOTICES))
                   and self._leftover_file(game, relative, plan)]
        if traces:
            # The pre-SR host's own folder; only this addon installs it.
            remove += [relative for relative in self._tree(game, "experimental_lighting", plan)
                       if self._leftover_file(game, relative, plan)]
        for folder in (manage.BACKUP, *_LEFTOVER_FOLDERS):
            path = game / folder
            if ((traces or folder != "experimental_lighting") and path.is_dir() and not path.is_symlink()
                    and not self._tree(game, folder, plan)):
                plan["folders"].append(folder)
        restored = set(plan["restore"])
        plan["remove"] = sorted(dict.fromkeys(name for name in remove if name not in restored))
        # A linked file goes only where the link itself was listed on evidence. A restored
        # original replaces the link, and the AMDNR file it pointed to goes with it.
        plan["linked"] = {name: target for name, target in sorted(plan["linked"].items()) if name in remove}
        current = game / "OptiScaler.ini"
        plan["save_config"] = ("OptiScaler.ini" in plan["remove"] or "OptiScaler.ini" in restored) and (
            (current.is_file() and not current.is_symlink()) or "OptiScaler.ini" in plan["linked"])

    def _leftover_file(self, game, relative, plan, note=True):
        """The regular file at relative, reached without links in its folders.

        A mod manager's link (see _linked_target) is read through: the file it
        points to is returned and noted in plan["linked"]. Any other link is
        kept (and noted).
        """
        path = game.joinpath(*PurePosixPath(relative).parts)
        linked_folder = any(parent.is_symlink() for parent in path.parents if game in parent.parents)
        target = self._linked_target(game, relative) if path.is_symlink() and not linked_folder else None
        if target is not None:
            plan["linked"][relative] = str(target)
            return target
        if path.is_symlink() or linked_folder:
            if note:
                plan["kept"].append(dict(path=relative, reason="a link, left for whatever made it"))
            return None
        return path if path.is_file() else None

    @staticmethod
    def _linked_target(game, relative):
        """The file a mod manager's link at relative stands for, or None.

        Managers that deploy with links (Amethyst, Vortex) keep the real file in
        their own folder under the same relative path, and an installation they
        took over is moved there. Only a direct link to a regular file outside
        the game folder whose path ends like the link's, in a folder this user
        can change, is followed.
        """
        path = game.joinpath(*PurePosixPath(relative).parts)
        try:
            named = path.parent / path.readlink()
            if named.is_symlink():
                return None  # a chain of links
            target = named.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        parts = [part.casefold() for part in PurePosixPath(relative).parts]
        if (not target.is_file() or target == game or game in target.parents or len(target.parts) <= len(parts)
                or [part.casefold() for part in target.parts[-len(parts):]] != parts
                or not os.access(target.parent, os.W_OK | os.X_OK)):
            return None
        return target

    def _leftover_path(self, game, relative):
        """The path of a listed leftover: it may be a link itself, but none of its folders may be."""
        folder, _, name = relative.rpartition("/")
        if name in ("", ".", "..") or "\\" in name:
            raise ConflictError(f"Invalid managed path: {relative!r}")
        return (self._safe(game, folder) if folder else game) / name

    def _leftover_source(self, game, plan, relative):
        """Where a listed file's bytes are: the file a mod manager's link points to, or the file itself."""
        return Path(plan["linked"][relative]) if relative in plan["linked"] else self._safe(game, relative)

    def _tree(self, game, folder, plan):
        """Paths below a game folder, relative to the game; links are listed, never followed."""
        root = game / folder
        if root.is_symlink():
            plan["kept"].append(dict(path=folder, reason="a link, left for whatever made it"))
            return []
        found = []
        for directory, children, files in os.walk(root, followlinks=False):
            base = Path(directory)
            found += [(base / name).relative_to(game).as_posix()
                      for name in sorted(files) + sorted(name for name in children if (base / name).is_symlink())]
        return found

    def _leftover_prefix(self, game, plan):
        """Plan giving back the Proton prefix files a lost installation's wrapper replaced.

        Unlike Uninstall, a prefix file someone else changed since stays as it
        is, and a deleted prefix is not created again.
        """
        journal = self._leftover_file(game, "amdnr-linux/prefixes.json", plan, note=False) or game / "amdnr-linux/prefixes.json"
        if journal.is_symlink() or (journal.exists() and not journal.is_file()):
            raise ConflictError(f"The Proton prefix journal is not a regular file: {journal}")
        if not journal.exists():
            return
        try:
            entries = json.loads(journal.read_text())
            if not isinstance(entries, list):
                raise ValueError("expected a list")
            for entry in entries:
                path = Path(entry["path"])
                if (not path.is_absolute() or ".." in path.parts or str(path) != entry["path"] or
                        path.parts[-4:-1] != ("drive_c", "windows", "system32") or
                        path.name not in _PREFIX_FILES or
                        not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])):
                    raise ValueError("invalid prefix path")
                if any(parent.is_symlink() for parent in path.parents):
                    raise ValueError(f"prefix ownership changed: {path}")
                if not path.parent.is_dir():
                    continue  # the prefix was deleted; there is nothing to give back
                if "original_link" in entry:
                    if not isinstance(entry["original_link"], str) or "backup" in entry:
                        raise ValueError("invalid original prefix link")
                    original = {"kind": "link", "target": entry["original_link"]}
                    item = dict(path=str(path), link=entry["original_link"])
                elif "backup" in entry:
                    if entry["backup"] != hashlib.sha256(str(path).encode()).hexdigest() + ".dll":
                        raise ValueError("invalid prefix backup name")
                    original = {"kind": "file", "sha256": entry["backup_sha256"]}
                    item = dict(path=str(path), backup=entry["backup"])
                else:
                    original, item = {"kind": "missing"}, dict(path=str(path))
                current = _state(path)
                if current == original:
                    continue
                if current["kind"] != "missing" and current != {"kind": "file", "sha256": entry["sha256"]}:
                    plan["kept"].append(dict(path=str(path), reason="changed since AMDNR replaced it; left as it is"))
                    continue
                if "backup" in item:
                    relative = "amdnr-linux/prefix-backups/" + item["backup"]
                    backup = self._leftover_file(game, relative, plan, note=False) or game / relative
                    if backup.is_symlink() or not backup.is_file() or _sha(backup) != entry["backup_sha256"]:
                        raise ValueError(f"missing or changed prefix backup: {backup}")
                plan["prefix"].append(item)
        except (KeyError, TypeError, ValueError) as error:
            raise ConflictError(f"Cannot give back the Proton prefix files AMDNR replaced: {error}") from error

    def clean(self, game_dir):
        """Remove what leftovers() lists, as one recoverable transaction."""
        game = self._game(game_dir)
        with self._lock(game):
            plan = self.leftovers(game)
            if plan["issues"]:
                raise ConflictError("\n".join(plan["issues"]))
            if not any(plan[key] for key in ("remove", "restore", "folders", "prefix")):
                raise ConflictError("No leftover AMDNR files were found in this folder.")
            operations = []
            if plan["save_config"]:
                # As with Uninstall, the last AMDNR settings stay in the state folder.
                operations.append(self._operation(self._safe(self.state_dir, "configs/" + self._key(game) + ".ini"),
                                                  source=self._leftover_source(game, plan, "OptiScaler.ini")))
            # Originals go back before the backups holding them are removed.
            for item in plan["prefix"]:
                path = Path(item["path"])
                if "backup" in item:
                    backup = self._leftover_source(game, plan, "amdnr-linux/prefix-backups/" + item["backup"])
                    operations.append(self._operation(path, source=backup))
                elif "link" in item:
                    operations.append(self._operation(path, link=item["link"]))
                else:
                    operations.append(self._operation(path))
            for relative in plan["restore"]:
                operations.append(self._operation(self._leftover_path(game, relative),
                                                  source=self._leftover_source(game, plan, self.manage.BACKUP + "/" + relative)))
            operations += [self._operation(self._leftover_path(game, relative)) for relative in plan["remove"]]
            for relative, target in plan["linked"].items():
                # The file a mod manager's link stood for; recovery may put it back only beside that link.
                operations.append(dict(self._operation(Path(target)), via=self._leftover_path(game, relative)))
            # The folder ends up as after an uninstall, an action every launcher version reads.
            operations.append(self._receipt_operation(game, "uninstall"))
            self.progress("Removing leftover AMDNR files…")
            self._commit(game, operations)
            self._prune_leftovers(game, plan)
            self.progress("Leftover AMDNR files removed." + (" Original game files were put back." if plan["restore"] else ""))
        return self.inspect(game)

    def _prune_leftovers(self, game, plan):
        """Remove AMDNR's folders and those of removed files once nothing is left in them.

        In a mod manager's folder, only the folders below its copy of the game
        folder that held removed files go.
        """
        linked = set()
        for relative, target in plan["linked"].items():
            linked.update(Path(target).parents[:len(PurePosixPath(relative).parts) - 1])
        for path in sorted(linked, key=lambda item: len(item.parts), reverse=True):
            try:
                path.rmdir()
            except OSError:
                pass  # something else is still in it
        folders = {PurePosixPath(name) for name in plan["folders"]}
        for relative in plan["remove"]:
            folders.update(parent for parent in PurePosixPath(relative).parents if parent.parts)
        for name in (self.manage.BACKUP, *_LEFTOVER_FOLDERS):
            root = game / name
            if root.is_dir() and not root.is_symlink():
                folders.update(PurePosixPath(Path(directory).relative_to(game).as_posix())
                               for directory, _, _ in os.walk(root, followlinks=False))
        for folder in sorted(folders, key=lambda item: len(item.parts), reverse=True):
            path = game.joinpath(*folder.parts)
            if (path.is_dir() and not path.is_symlink()
                    and not any(parent.is_symlink() for parent in path.parents if game in parent.parents)):
                try:
                    path.rmdir()
                except OSError:
                    pass  # something else is still in it

    def diagnostics(self, game_dir):
        game = self._game(game_dir)
        result = self.inspect(game)
        result.update(release_root=str(self.release_root), cache_dir=str(self.cache_dir),
                      state_dir=str(self.state_dir), saved_settings=str(self.state_dir / "configs" / (self._key(game) + ".ini")))
        try:
            result["prefixes"] = [entry["path"] for entry in self._prefix_entries(game)]
        except (OSError, ValueError, ConflictError) as error:
            result["prefix_error"] = str(error)
        evidence, errors = self._startup_evidence(game)
        result["startup_evidence"] = evidence
        result["startup_errors"] = errors
        result["logs"] = [entry["path"] for entry in evidence]
        result["startup_summary"] = (
            "Stored startup logs are historical evidence. Current host loading and neural rendering have not been verified."
            if evidence else
            "No supported startup logs were found. Host loading and neural rendering have not been verified.")
        return result

    def _startup_evidence(self, game):
        """Collect bounded tails of known logs, never following links or executing them."""
        names = ["OptiScaler.log", "re2_framework_log.txt", "lmxxf_backend.log", "amd_bridge.log", "amdnr_crash.log",
                 "amdnr-linux/launch.log", "amdnr-linux/startup.log", "amd_presr.log", "dlssnr_on_amd.log",
                 "amdnr-crash-trace.log"]
        # Previous host sessions help with early failures that truncate the main
        # log. Cap both discovery and the files read in a mod-heavy game folder.
        try:
            with os.scandir(game) as directory:
                for index, entry in enumerate(directory):
                    if index >= 512:
                        break
                    if re.fullmatch(r"OptiScaler\.previous(?:-\d+)?\.[^/\\]{1,160}\.log", entry.name):
                        names.append(entry.name)
        except OSError as error:
            return [], [str(error)]
        evidence, errors = [], []
        for name in names:
            if len(evidence) == 8:
                break
            try:
                path = self._safe(game, name)
                if not path.exists():
                    continue
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(descriptor, "rb") as source:
                    info = os.fstat(source.fileno())
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    source.seek(max(0, info.st_size - 16384))
                    tail = source.read(16384).decode("utf-8", errors="replace")
                evidence.append(dict(name=name, path=str(path), size=info.st_size,
                                     modified_ns=info.st_mtime_ns, tail=tail, truncated=info.st_size > 16384))
            except (OSError, ValueError, ConflictError) as error:
                errors.append(f"{name}: {error}")
        return evidence, errors
