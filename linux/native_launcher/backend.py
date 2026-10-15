"""Local package operations for the Linux launcher.

This module uses only this project's Linux setup/installation code. Operations
are synchronous: run them on the launcher's worker thread, after preparing the
candidate and any Steam configuration change. No game is ever launched here.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
from typing import Callable


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
_backup_marker = b"NR Linux Manager original backups\n"


def _sha(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _load_modules(root):
    # The existing CLI scripts use absolute sibling imports. Install temporary
    # bindings only while loading; each backend retains its own module objects
    # and ROOT constants. Do not modify another backend's modules or sys.path.
    with _module_lock:
        saved = {name: sys.modules.get(name, _missing) for name in ("launch", "manage")}
        loaded = {}
        try:
            for name in ("launch", "manage", "setup"):
                spec = importlib.util.spec_from_file_location(f"amdnr_launcher_{id(root)}_{name}", root / "linux" / (name + ".py"))
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


def _state(path):
    if path.is_symlink():
        return {"kind": "link", "target": str(path.readlink())}
    if path.is_file():
        return {"kind": "file", "sha256": _sha(path)}
    if path.exists():
        raise ConflictError(f"Expected a file, found a directory: {path}")
    return {"kind": "missing"}


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
        manifest = self.release_root / "RELEASE-MANIFEST.json"
        self.addon_version = None
        if manifest.is_file():
            release = json.loads(manifest.read_text()).get("release", "")
            if not isinstance(release, str) or not re.fullmatch(r"linux-v\d+\.\d+\.\d+", release):
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
                receipt.get("action") not in ("install", "repair", "update", "uninstall") or
                not isinstance(receipt.get("operation_id"), str)):
            raise ConflictError("Invalid launcher operation receipt.")
        return receipt

    def _receipt_operation(self, game, action, package_id=None):
        token = self.operation_id if self.operation_id is not None else uuid.uuid4().hex
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", token):
            raise BackendError("Invalid launcher operation identifier.")
        self.operation_id = None
        receipt = dict(schema=1, game=str(game), action=action, operation_id=token,
                       addon_version=self.addon_version, package_id=package_id, committed_at=int(time.time()))
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

    def _running(self, game):
        found = []
        for process in Path("/proc").iterdir():
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
        path = self._safe(game, self.manage.RECORD)
        if not path.exists():
            return None
        try:
            record = json.loads(path.read_text())
            if record.get("version") != 1 or not isinstance(record["files"], list) or not isinstance(record["installed"], list):
                raise ValueError("unsupported record")
            metadata = record.get("launcher", {})
            if not isinstance(metadata, dict) or (metadata.get("addon_version") is not None and
                    not re.fullmatch(r"\d+\.\d+\.\d+", metadata["addon_version"])):
                raise ValueError("invalid launcher version metadata")
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
            return record
        except (ValueError, KeyError, TypeError) as error:
            raise ConflictError(f"Cannot trust the existing installation record: {error}") from error

    @staticmethod
    def _version(directory):
        source = directory / "amdnr-linux/SOURCE.txt"
        if source.is_file() and not source.is_symlink():
            match = re.search(r"Host/runtime: AMDNR ([^,\s]+)", source.read_text(errors="replace"))
            if match:
                return match[1]
        return "unknown"

    def inspect(self, game_dir):
        game = self._game(game_dir)
        result = dict(game_dir=str(game), installed=False, status="not_installed", version=None, addon_version=None,
                      host_version=None, release_root=None,
                      modified=[], missing=[], conflicts=[], config_modified=False,
                      running=self._running(game), recovery_needed=(self._transaction_dir(game) / "journal.json").exists())
        try:
            record = self._record(game)
            if record:
                metadata = record.get("launcher", {})
                result.update(installed=True, version=metadata.get("addon_version") or metadata.get("version", self._version(game)),
                              addon_version=metadata.get("addon_version"), host_version=metadata.get("host_version", self._version(game)),
                              release_root=metadata.get("release_root"),
                              package_id=record.get("launcher", {}).get("package_id"), status="installed")
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
                if result["missing"] or result["modified"]:
                    result["status"] = "needs_repair"
            elif (game / self.manage.BACKUP).exists():
                result["conflicts"].append("Original backups exist without an installation record.")
        except (ValueError, OSError, ConflictError) as error:
            result["conflicts"].append(str(error))
        if result["conflicts"]:
            result["status"] = "conflict"
        if result["recovery_needed"]:
            result["status"] = "interrupted"
        return result

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

    def prepare_package(self, compiler=None, search_roots=None, archive=None):
        self.progress("Finding a native shader compiler…")
        compiler = self._find_compiler(compiler, search_roots or [])
        self.progress("Verifying the release and preparing pinned runtime files…")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        candidate = Path(tempfile.mkdtemp(prefix="candidate-", dir=self.cache_dir))
        try:
            package = self.setup.prepare(compiler, archive, candidate / "package", self.cache_dir / "downloads")
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
            files[name] = dict(source=path, sha256=_sha(path))
        required = {"winmm.dll", "OptiScaler.ini", "d3d12.dll", "d3d12core.dll", "d3dcompiler_47.dll",
                    "amdnr-linux/run.sh", "amdnr-linux/x86_64-unix/amdhip64_7.so",
                    "amdnr-linux/x86_64-windows/amdhip64_7.dll", "amdnr-linux/x86_64-unix/libamdnr_signal_guard.so"}
        if not required <= files.keys() or "amdnr-linux/x86_64-unix/amdhip64_7.dll.so" in files:
            raise BackendError("Not a current prepared PE/Unixlib Linux addon package.")
        return package, files

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
                        path.name not in ("amdhip64_7.dll", "d3dcompiler_47.dll") or str(path) in seen):
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
                entry = dict(path=str(path), before=before, after=operation["after"])
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
            prefix = path.parts[-4:-1] == ("drive_c", "windows", "system32") and path.name in ("amdhip64_7.dll", "d3dcompiler_47.dll")
            saved_config = path == self.state_dir / "configs" / (self._key(game) + ".ini")
            receipt = path == self.state_dir / "game-receipts" / (self._key(game) + ".json")
            if not path.is_absolute() or not (in_game or prefix or saved_config or receipt) or any(parent.is_symlink() for parent in path.parents):
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

    def _change(self, game_dir, package, mode):
        game = self._game(game_dir)
        package, candidate = self._package(package)
        if game == package or game in package.parents or package in game.parents:
            raise ConflictError("Prepared package and game directories must be separate.")
        with self._lock(game):
            old = self._record(game)
            if mode == "install" and (old or (game / self.manage.BACKUP).exists()):
                raise ConflictError("This game already has an installation or backup. Use Repair or Update.")
            if mode != "install" and not old:
                raise ConflictError("No managed installation was found. Use Install first.")
            installed_version = old.get("launcher", {}).get("addon_version") if old else None
            if installed_version:
                if self.addon_version is None or tuple(map(int, self.addon_version.split("."))) < tuple(map(int, installed_version.split("."))):
                    raise ConflictError(f"Linux addon {installed_version} is installed. This package is older or has no release version; a downgrade was refused.")
                if mode == "repair" and self.addon_version != installed_version:
                    raise ConflictError(f"Repair requires Linux addon {installed_version}. Use Update to install {self.addon_version}.")
            if not old:
                for name in ("prefixes.json", "prefixes.json.tmp", "prefix-backups", "prefixes.lock"):
                    if (game / "amdnr-linux" / name).exists():
                        raise ConflictError("Existing Proton prefix ownership must be restored before installing.")
            previous = {entry["path"]: entry for entry in old["files"]} if old else {}
            installed = set(old["installed"]) if old else set()
            operations, files = [], []
            marker = self._safe(game, self.manage.BACKUP + "/.launcher-backup-v1")
            if not marker.exists():
                operations.append(self._operation(marker, data=_backup_marker))
            elif marker.read_bytes() != _backup_marker:
                raise ConflictError("The original-backup folder contains an unrecognized launcher marker.")
            for name, info in candidate.items():
                target = self._safe(game, name)
                prior = previous.get(name)
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
                preserve = old is not None and name == "OptiScaler.ini" and target.is_file()
                checksum = _sha(target) if preserve else info["sha256"]
                entry = dict(path=name, sha256=checksum, existed=existed)
                if existed:
                    entry["backup_sha256"] = _sha(backup if prior and name in installed else target)
                files.append(entry)
                if not preserve and (not target.is_file() or _sha(target) != checksum):
                    operations.append(self._operation(target, source=info["source"]))
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
            prefix_entries = self._prefix_entries(game)
            for entry in prefix_entries:
                path = Path(entry["path"])
                source_name = "amdnr-linux/x86_64-windows/amdhip64_7.dll" if path.name == "amdhip64_7.dll" else "d3dcompiler_47.dll"
                info = candidate[source_name]
                if not path.exists() or _sha(path) != info["sha256"]:
                    operations.append(self._operation(path, source=info["source"]))
                entry["sha256"] = info["sha256"]
            if prefix_entries:
                operations.append(self._operation(self._safe(game, "amdnr-linux/prefixes.json"), data=_json_bytes(prefix_entries)))
            identity = hashlib.sha256(_json_bytes({name: info["sha256"] for name, info in candidate.items()})).hexdigest()
            record = dict(version=1, files=files, installed=[entry["path"] for entry in files],
                          launcher=dict(version=self.addon_version or self._version(package), addon_version=self.addon_version,
                                        host_version=self._version(package), release_root=str(self.release_root),
                                        package_id=identity, updated_at=int(time.time())))
            operations.append(self._operation(self._safe(game, self.manage.RECORD), data=_json_bytes(record)))
            receipt = self._receipt_operation(game, mode, identity)
            record["launcher"]["operation_id"] = json.loads(receipt["data"])["operation_id"]
            operations[-1] = self._operation(self._safe(game, self.manage.RECORD), data=_json_bytes(record))
            operations.append(receipt)
            self.progress({"install": "Installing", "repair": "Repairing", "update": "Updating"}[mode] + " the Linux addon…")
            self._commit(game, operations)
            self.progress("Installation ready. Your original backups and settings have been preserved.")
        return self.inspect(game)

    def install(self, game_dir, package):
        return self._change(game_dir, package, "install")

    def repair(self, game_dir, package):
        return self._change(game_dir, package, "repair")

    def update(self, game_dir, package):
        return self._change(game_dir, package, "update")

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
            self.progress("Restoring original game and Proton prefix files…")
            self._commit(game, operations)
            self._prune_uninstalled(game)
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

    def diagnostics(self, game_dir):
        game = self._game(game_dir)
        result = self.inspect(game)
        result.update(release_root=str(self.release_root), cache_dir=str(self.cache_dir),
                      state_dir=str(self.state_dir), saved_settings=str(self.state_dir / "configs" / (self._key(game) + ".ini")))
        try:
            result["prefixes"] = [entry["path"] for entry in self._prefix_entries(game)]
        except (OSError, ConflictError) as error:
            result["prefix_error"] = str(error)
        result["logs"] = [str(game / name) for name in ("OptiScaler.log", "lmxxf_backend.log", "amd_bridge.log") if (game / name).is_file()]
        return result
