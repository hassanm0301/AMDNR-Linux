"""Independent Steam discovery and narrowly scoped, reversible VDF edits.

No Steam API credentials or account details are returned. Only launch-option
tokens are edited; full private backups remain in the launcher's state folder.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import base64
import fcntl
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import tempfile


@dataclass(frozen=True)
class SteamGame:
    appid: str
    name: str
    install_dir: Path
    steam_root: Path
    library: Path
    exe_dir: Path | None = None
    library_ambiguous: bool = False


@dataclass(frozen=True)
class Token:
    value: str
    start: int
    end: int
    structural: bool = False


@dataclass
class Node:
    key: Token
    value: Token | None = None
    children: list[Node] | None = None
    close: Token | None = None


def _tokens(text):
    i, n = 0, len(text)
    escapes = {'n': '\n', 'r': '\r', 't': '\t', '"': '"', '\\': '\\'}
    while i < n:
        if text[i].isspace() or (i == 0 and text[i] == '\ufeff'):
            i += 1
        elif text.startswith('//', i):
            end = text.find('\n', i)
            i = n if end < 0 else end + 1
        elif text.startswith('/*', i):
            end = text.find('*/', i + 2)
            if end < 0:
                raise ValueError('Unterminated VDF comment')
            i = end + 2
        elif text[i] in '{}':
            yield Token(text[i], i, i + 1, True)
            i += 1
        elif text[i] == '"':
            start, value = i, []
            i += 1
            while i < n and text[i] != '"':
                if text[i] == '\\':
                    if i + 1 >= n:
                        raise ValueError('Unterminated VDF escape')
                    char = text[i + 1]
                    value.append(escapes.get(char, '\\' + char))
                    i += 2
                else:
                    value.append(text[i])
                    i += 1
            if i == n:
                raise ValueError('Unterminated VDF string')
            i += 1
            yield Token(''.join(value), start, i)
        else:
            start = i
            while i < n and not text[i].isspace() and text[i] not in '{}"':
                i += 1
            if i == start:
                raise ValueError('Malformed VDF token')
            yield Token(text[start:i], start, i)


def parse_vdf(text: str) -> list[Node]:
    """Parse text while retaining original token offsets and all whitespace."""
    tokens, cursor = list(_tokens(text)), 0

    def block(depth, nested):
        nonlocal cursor
        if depth > 64:
            raise ValueError('VDF nesting is too deep')
        nodes = []
        while cursor < len(tokens):
            key = tokens[cursor]
            cursor += 1
            if key.structural and key.value == '}':
                if not nested:
                    raise ValueError('Unexpected VDF closing brace')
                return nodes, key
            if key.structural or cursor == len(tokens):
                raise ValueError('Missing VDF value')
            value = tokens[cursor]
            cursor += 1
            if value.structural and value.value == '{':
                children, close = block(depth + 1, True)
                nodes.append(Node(key, children=children, close=close))
            elif value.structural:
                raise ValueError('Missing VDF value')
            else:
                nodes.append(Node(key, value=value))
        if nested:
            raise ValueError('Missing VDF closing brace')
        return nodes, None

    return block(0, False)[0]


def _one(nodes, name):
    found = [node for node in nodes if node.key.value.casefold() == name.casefold()]
    if len(found) > 1:
        raise ValueError('Ambiguous duplicate Steam settings key')
    return found[0] if found else None


def _child(nodes, name):
    node = _one(nodes, name)
    if node is None:
        return None
    if node.children is None:
        raise ValueError('Expected a Steam settings section')
    return node


def _value(nodes, name):
    node = _one(nodes, name)
    if node is None:
        return None
    if node.value is None:
        raise ValueError('Expected a Steam settings value')
    return node.value.value


def _decode(data):
    return data.decode('utf-8', 'surrogateescape')


def _encode(text):
    return text.encode('utf-8', 'surrogateescape')


def _read(path):
    flags = os.O_RDONLY | os.O_NOFOLLOW
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Steam settings must be a regular file')
        data = stream.read(32 * 1024 * 1024 + 1)
        if len(data) > 32 * 1024 * 1024:
            raise ValueError('Steam settings file is unexpectedly large')
        return data


def _x64_executable(path):
    try:
        with path.open('rb') as stream:
            header = stream.read(64)
            if len(header) != 64 or header[:2] != b'MZ':
                return False
            offset = int.from_bytes(header[60:64], 'little')
            if offset > 1024 * 1024:
                return False
            stream.seek(offset)
            return stream.read(6) == b'PE\0\0\x64\x86'
    except OSError:
        return False


def resolve_exe_directory(root: Path) -> Path | None:
    """Return a unique x64 game directory, or None when browsing is needed."""
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        return None
    candidates = set()
    excluded = {'engine', 'redist', '_commonredist', 'directx', 'vcredist', 'support',
                'amdnr-linux', '.amdnr-linux-backup', '.git'}
    helpers = re.compile(r'(crash(?:report|pad|sender)|unitycrashhandler|unins\d*(?:$|[_.-])|'
                         r'setup(?:$|[_.-])|ue[45]prereq|launcher(?:$|[_.-]))', re.I)
    try:
        # Prune helper/source trees before descending instead of traversing all
        # of their assets only to reject their executables afterwards.
        for directory, children, files in os.walk(root, followlinks=False):
            parent = Path(directory)
            children[:] = [name for name in children if name.casefold() not in excluded]
            for name in files:
                if not name.casefold().endswith('.exe'):
                    continue
                path = parent / name
                if path.is_symlink() or helpers.match(path.stem):
                    continue
                if _x64_executable(path):
                    candidates.add(parent)
    except OSError:
        return None
    for name in ('x64_dx12', 'win64'):
        preferred = {path for path in candidates if path.name.casefold() == name}
        if preferred:
            return next(iter(preferred)) if len(preferred) == 1 else None
    return next(iter(candidates)) if len(candidates) == 1 else None


def unreal_game_executable(executable: Path) -> Path:
    """The Shipping executable an Unreal root launcher starts, else the executable.

    A packaged Unreal game's root `Name.exe` only starts
    `<Project>/Binaries/Win64/Name-Win64-Shipping.exe`, so a proxy DLL placed
    beside the root executable never loads in the game.
    """
    executable = Path(executable)
    pattern = f'*/Binaries/Win64/{glob.escape(executable.stem)}-Win64-Shipping.exe'
    found = [path for path in executable.parent.glob(pattern, case_sensitive=False)
             if not path.is_symlink() and _x64_executable(path)]
    return found[0].resolve() if len(found) == 1 else executable


def discover_games(roots: list[Path] | None = None) -> list[SteamGame]:
    if roots is None:
        home = Path.home()
        roots = [home / '.local/share/Steam', home / '.steam/steam',
                 home / '.var/app/com.valvesoftware.Steam/.local/share/Steam']
    games, seen_roots = {}, set()
    for candidate in roots:
        root = Path(candidate).expanduser().resolve()
        if root in seen_roots or not root.is_dir():
            continue
        seen_roots.add(root)
        libraries = {root}
        ownership = None
        for file in (root / 'steamapps/libraryfolders.vdf', root / 'config/libraryfolders.vdf'):
            try:
                section = _child(parse_vdf(_decode(_read(file))), 'libraryfolders')
                if section is None:
                    continue
                indexed = {}
                for entry in section.children if section else []:
                    if not entry.key.value.isdecimal():
                        continue
                    value = entry.value.value if entry.value else _value(entry.children, 'path')
                    if value and '\0' not in value and Path(value).is_absolute():
                        library = Path(value).resolve()
                        libraries.add(library)
                        apps = _child(entry.children, 'apps') if entry.children is not None else None
                        for app in apps.children if apps else []:
                            if re.fullmatch(r'[0-9]+', app.key.value):
                                indexed.setdefault(app.key.value, set()).add(library)
                if ownership is None:
                    # steamapps/libraryfolders.vdf is current. An old config/
                    # copy may reveal fallback libraries, but must not override
                    # this file's ownership decisions (including an empty map).
                    ownership = indexed
            except (OSError, ValueError):
                continue
        candidates = {}
        for library in sorted(libraries):
            for manifest in sorted((library / 'steamapps').glob('appmanifest_*.acf')):
                try:
                    match = re.fullmatch(r'appmanifest_([0-9]+)\.acf', manifest.name)
                    if not match:
                        continue
                    section = _child(parse_vdf(_decode(_read(manifest))), 'AppState')
                    if section is None:
                        continue
                    appid = _value(section.children, 'appid')
                    folder = _value(section.children, 'installdir')
                    name = _value(section.children, 'name')
                    if (appid != match[1] or not folder or folder in ('.', '..')
                            or any(char in folder for char in '/\\\0') or not name):
                        continue
                    common = (library / 'steamapps/common').resolve()
                    install = common / folder
                    if not install.is_dir() or not install.resolve().is_relative_to(common):
                        continue
                    install = install.resolve()
                    if (install / 'toolmanifest.vdf').is_file() or appid == '228980':
                        continue  # Proton, Steam Linux Runtime, and common redistributables.
                    candidates.setdefault(appid, []).append(SteamGame(appid, name, install, root, library))
                except (OSError, ValueError):
                    continue
        for appid, copies in candidates.items():
            owners = (ownership or {}).get(appid)
            if owners:
                copies = [game for game in copies if game.library in owners]
                if not copies:
                    # A stale manifest elsewhere does not override Steam's
                    # explicit owner, even if that indexed install is missing.
                    continue
            copies.sort(key=lambda game: (str(game.library), str(game.install_dir)))
            chosen = copies[0]
            ambiguous = len(copies) > 1 or bool(owners and len(owners) > 1)
            games[(root, appid)] = SteamGame(
                chosen.appid, chosen.name, chosen.install_dir, chosen.steam_root,
                chosen.library, None if ambiguous else resolve_exe_directory(chosen.install_dir), ambiguous)
    return sorted(games.values(), key=lambda game: (game.name.casefold(), game.appid, str(game.steam_root)))


def steam_is_running(proc_root: Path = Path('/proc'), uid: int | None = None) -> bool:
    """Match the current user's Steam main executable, not steamwebhelper."""
    uid = os.getuid() if uid is None else uid
    for process in proc_root.iterdir():
        if not process.name.isdecimal():
            continue
        try:
            if process.stat().st_uid != uid or (process / 'comm').read_text().strip() != 'steam':
                continue
            try:
                executable = os.readlink(process / 'exe').removesuffix(' (deleted)')
                if Path(executable).name == 'steam':
                    return True
            except PermissionError:
                return True  # Cannot safely prove that this matching process exited.
        except (OSError, UnicodeError):
            continue
    return False


def _closed():
    if steam_is_running():
        raise ValueError('Close Steam normally before changing its launch options, then try again.')


def _identity(game):
    if not re.fullmatch(r'[0-9]+', game.appid):
        raise ValueError('Invalid Steam app ID')
    return str(Path(game.steam_root).expanduser().resolve())


def journal_path(game: SteamGame, state_dir: Path) -> Path:
    key = hashlib.sha256(os.fsencode(_identity(game))).hexdigest()[:20]
    return Path(state_dir).expanduser().absolute() / 'steam' / key / (game.appid + '.json')


def _private_directory(path):
    if path.is_symlink():
        raise ValueError('Launcher state directory must not be a symlink')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_uid != os.getuid():
        raise ValueError('Launcher state must belong to the current user')
    path.chmod(0o700)


@contextmanager
def _lock(game, state_dir):
    path = journal_path(game, state_dir)
    _private_directory(path.parent)
    descriptor = os.open(path.with_suffix('.lock'), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another launcher operation is changing this game’s Steam settings') from None
        yield path
    finally:
        os.close(descriptor)


def _atomic_write(path, data, expected, *, steam_file=False):
    """Compare immediately before replace; never overwrite observed new data."""
    temporary = None
    try:
        mode = stat.S_IMODE(path.stat().st_mode) if expected is not None else 0o600
        with tempfile.NamedTemporaryFile(prefix='.' + path.name + '.', dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            os.fchmod(output.fileno(), mode)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        current = _read(path) if path.exists() or path.is_symlink() else None
        if current != expected:
            raise ValueError('Steam settings changed during this operation; no newer settings were overwritten')
        if steam_file:
            _closed()
        os.replace(temporary, path)
        temporary = None
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _app(text, appid):
    nodes = parse_vdf(text)
    for name in ('UserLocalConfigStore', 'Software', 'Valve', 'Steam', 'apps', appid):
        node = _child(nodes, name)
        if node is None:
            return None
        nodes = node.children
    return node


def _options(text, appid):
    app = _app(text, appid)
    if app is None:
        raise ValueError('Steam no longer has this game in the recorded account')
    option = _one(app.children, 'LaunchOptions')
    if option is not None and option.value is None:
        raise ValueError('Malformed Steam launch options')
    return app, option


def _account_path(game, account):
    if not re.fullmatch(r'[0-9]+', account):
        raise ValueError('Invalid Steam account settings path')
    root = Path(_identity(game))
    path = root / 'userdata' / account / 'config/localconfig.vdf'
    if (not path.parent.resolve().is_relative_to(root / 'userdata')
            or any(item.is_symlink() for item in (root / 'userdata', path.parent.parent, path.parent, path))):
        raise ValueError('Steam account settings path leaves its userdata directory')
    return path


def _accounts(game, required=True):
    entries = []
    for account in sorted((Path(_identity(game)) / 'userdata').glob('*')):
        if not account.name.isdecimal():
            continue
        path = _account_path(game, account.name)
        if not path.exists():
            continue
        data = _read(path)
        text = _decode(data)
        if _app(text, game.appid) is not None:
            entries.append((account.name, path, data))
    if not entries and required:
        raise ValueError('Launch this game once in Steam, then close Steam and try again.')
    return entries


def _quote(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t') + '"'


def _replace_options(text, appid, value, raw=None, inserted=None):
    app, option = _options(text, appid)
    if value is None:
        if option is None:
            return text, None
        if inserted:
            start = text.rfind(inserted, 0, option.value.end + len(inserted))
            if start >= 0 and start <= option.key.start < start + len(inserted):
                return text[:start] + text[start + len(inserted):], None
        start, end = option.key.start, option.value.end
        line_start, line_end = text.rfind('\n', 0, start) + 1, text.find('\n', end)
        line_end = len(text) if line_end < 0 else line_end + 1
        if not text[line_start:start].strip() and not text[end:line_end].strip():
            start, end = line_start, line_end
        return text[:start] + text[end:], None
    token = raw if raw is not None else _quote(value)
    if option is not None:
        return text[:option.value.start] + token + text[option.value.end:], None
    newline = '\r\n' if '\r\n' in text else '\n'
    close = app.close.start
    line = text.rfind('\n', 0, close) + 1
    indent = text[line:close]
    if not indent.strip():
        position = line
        addition = indent + '\t"LaunchOptions"\t\t' + token + newline
    else:
        position = close
        addition = newline + '\t"LaunchOptions"\t\t' + token + newline
    return text[:position] + addition + text[position:], addition


def _with_wrapper(options, wrapper):
    options = options or ''
    if any(char in options for char in '\0\r\n'):
        raise ValueError('Steam launch options contain unsupported control characters')
    try:
        words = shlex.split(options)
        lexer = shlex.shlex(options, posix=True, punctuation_chars=True)
        lexer.whitespace_split, lexer.commenters = True, ''
        if any(word and (all(char in ';&|<>()' for char in word) or word.startswith('#')) for word in lexer):
            raise ValueError('Compound shell launch options need manual configuration')
    except ValueError as error:
        raise ValueError('Malformed or compound Steam launch options; configure them manually') from error
    if '$(' in options or '`' in options:
        raise ValueError('Shell substitutions in launch options need manual configuration')
    count = options.count('%command%')
    if count > 1 or (count == 1 and (words.count('%command%') != 1
                                   or not re.search(r'(?<!\S)%command%(?!\S)', options))):
        raise ValueError('Steam launch options must contain a single unquoted %command%')
    quoted = shlex.quote(wrapper)
    if count:
        start = options.index('%command%')
        before = options[:start]
        prefix = shlex.split(before)
        if prefix and prefix[-1] == wrapper:
            # Recover the exact shell token suffix, retaining earlier wrappers.
            match = None
            for index in range(len(before)):
                if index and not before[index - 1].isspace():
                    continue
                try:
                    if shlex.split(before[index:]) == [wrapper]:
                        match = index
                        break
                except ValueError:
                    continue
            if match is None:
                raise ValueError('Cannot safely identify the existing wrapper token')
            return options, before[:match] + options[start:], True
        if wrapper in words:
            raise ValueError('Existing wrapper is in an unexpected launch-option position')
        return before + quoted + ' ' + options[start:], options, False
    return quoted + ' %command%' + (' ' + options if options else ''), options, False


def _wrapper_path(wrapper, game=None):
    wrapper = Path(wrapper).expanduser()
    if not wrapper.is_absolute() or any(char in str(wrapper) for char in '\0\n\r') or '%command%' in str(wrapper):
        raise ValueError('The wrapper must have an absolute, valid path')
    wrapper = wrapper.resolve()
    if game is not None and game.exe_dir is not None:
        planned = Path(game.exe_dir).resolve() / 'amdnr-linux/run.sh'
        if wrapper == planned and (not wrapper.exists() or wrapper.is_file()):
            # The backend installs a missing wrapper and repairs its execute
            # permission. Do not block that repair before the backend runs.
            # Standalone configure_launch still requires a runnable wrapper.
            return str(wrapper)
    if not wrapper.is_file() or not os.access(wrapper, os.X_OK):
        raise ValueError('The game wrapper is missing or not executable')
    return str(wrapper)


def _journal(path, game):
    if not path.exists() and not path.is_symlink():
        return None, None
    raw = _read(path)
    try:
        data = json.loads(raw)
        if (data['version'] != 1 or data['appid'] != game.appid
                or data['steam_root'] != _identity(game) or not isinstance(data['entries'], list)
                or not data['entries'] or not isinstance(data['wrapper'], str)):
            raise ValueError()
        accounts = set()
        for entry in data['entries']:
            _account_path(game, entry['account'])
            if entry['account'] in accounts or not isinstance(entry['new'], str):
                raise ValueError()
            accounts.add(entry['account'])
            if entry['original'] is not None and not isinstance(entry['original'], str):
                raise ValueError()
            if entry['original_token'] is not None and not isinstance(entry['original_token'], str):
                raise ValueError()
            if entry.get('inserted') is not None and not isinstance(entry['inserted'], str):
                raise ValueError()
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise ValueError('Invalid Steam launch-option journal; preserve it for recovery') from None
    return data, raw


def _configure(game, wrapper, path, *, planned=False):
    _closed()
    wrapper = _wrapper_path(wrapper, game if planned else None)
    journal, journal_raw = _journal(path, game)
    if journal is not None:
        if journal['wrapper'] != wrapper:
            raise ValueError('Restore the previous Steam wrapper before installing a different one')
        for entry in journal['entries']:
            current = _decode(_read(_account_path(game, entry['account'])))
            _, option = _options(current, game.appid)
            if (option.value.value if option else None) != entry['new']:
                raise ValueError('Steam launch options changed since configuration; preserve your changes and configure manually')
        return journal['entries'][0]['new']
    entries, changes = [], []
    for account, config, original_data in _accounts(game):
        original_text = _decode(original_data)
        _, option = _options(original_text, game.appid)
        original = option.value.value if option else None
        new, without_wrapper, adopted = _with_wrapper(original, wrapper)
        updated, inserted = _replace_options(original_text, game.appid, new)
        # Adopting the same wrapper leaves the file byte-for-byte unchanged.
        if adopted:
            updated = original_text
        entry = {'account': account, 'original': without_wrapper if adopted else original,
                 'original_token': _quote(without_wrapper) if adopted else
                     (original_text[option.value.start:option.value.end] if option else None),
                 'new': new, 'inserted': inserted,
                 'before_sha256': hashlib.sha256(original_data).hexdigest()}
        entries.append(entry)
        changes.append((config, original_data, _encode(updated)))
    journal = {'version': 1, 'appid': game.appid, 'steam_root': _identity(game),
               'wrapper': wrapper, 'entries': entries}
    new_journal = _encode(json.dumps(journal, ensure_ascii=True, indent=2) + '\n')
    _receipt_plan(path, changes, new_journal)
    backups = path.parent / (game.appid + '-backups')
    _private_directory(backups)
    for entry, (_, before, _) in zip(entries, changes):
        backup = backups / (entry['account'] + '.vdf')
        if backup.exists() or backup.is_symlink():
            raise ValueError('Existing Steam backup has no journal; preserve it before retrying')
        _atomic_write(backup, before, None)
    _atomic_write(path, new_journal, journal_raw)
    for config, before, after in changes:
        if after != before:
            _atomic_write(config, after, before, steam_file=True)
    return entries[0]['new']


def configure_launch(game: SteamGame, wrapper: Path, state_dir: Path) -> str:
    """Configure every account containing this game; return its first option."""
    with _lock(game, state_dir) as path:
        _no_pending_receipt(path)
        return _configure(game, wrapper, path)


def _restore(game, path):
    _closed()
    journal, raw = _journal(path, game)
    if journal is None:
        return
    changes = []
    for entry in journal['entries']:
        config = _account_path(game, entry['account'])
        before = _read(config)
        text = _decode(before)
        _, option = _options(text, game.appid)
        current = option.value.value if option else None
        if current == entry['original']:
            continue  # Also recovers an interrupted configuration/restoration.
        if current != entry['new']:
            raise ValueError('Steam launch options were edited later; they were preserved. Remove only the AMDNR wrapper manually.')
        restored, _ = _replace_options(text, game.appid, entry['original'], entry['original_token'], entry.get('inserted'))
        changes.append((config, before, _encode(restored)))
    _receipt_plan(path, changes, None)
    for config, before, after in changes:
        _atomic_write(config, after, before, steam_file=True)
    if _read(path) != raw:
        raise ValueError('Steam launch-option journal changed during restoration')
    path.unlink()
    # Remove originals only after all options were restored successfully.
    backups = path.parent / (game.appid + '-backups')
    for entry in journal['entries']:
        (backups / (entry['account'] + '.vdf')).unlink(missing_ok=True)
    try:
        backups.rmdir()
    except OSError:
        pass


def restore_launch(game: SteamGame, state_dir: Path):
    with _lock(game, state_dir) as path:
        _no_pending_receipt(path)
        _restore(game, path)


def wrapper_leftover(game: SteamGame, wrapper: Path, state_dir: Path) -> bool:
    """Whether Steam still names this wrapper for the game, in its options or our journal. Read-only."""
    wrapper = str(Path(wrapper).expanduser().resolve())
    try:
        journal, _ = _journal(journal_path(game, state_dir), game)
        if journal is not None:
            return journal['wrapper'] == wrapper
        for _, _, data in _accounts(game, required=False):
            _, option = _options(_decode(data), game.appid)
            if option and wrapper in option.value.value:
                return True
    except (OSError, ValueError):
        pass
    return False


def remove_wrapper(game: SteamGame, wrapper: Path, state_dir: Path) -> bool:
    """Take a leftover wrapper out of the game's launch options; True if Steam's settings changed.

    Options this launcher recorded go back to what they were. Otherwise, as for a
    wrapper added by hand, only the wrapper in front of %command% is removed.
    """
    wrapper = str(Path(wrapper).expanduser().resolve())
    with _lock(game, state_dir) as path:
        _no_pending_receipt(path)
        _closed()
        journal, _ = _journal(path, game)
        if journal is not None:
            if journal['wrapper'] != wrapper:
                return False  # another folder's installation owns these options
            try:
                _restore(game, path)
                return True
            except ValueError:
                pass  # edited since: keep those edits and take out only the wrapper
        changed = False
        for _, config, data in _accounts(game, required=False):
            text = _decode(data)
            _, option = _options(text, game.appid)
            current = option.value.value if option else None
            if not current or wrapper not in current:
                continue
            try:
                _, without, adopted = _with_wrapper(current, wrapper)
            except ValueError:
                adopted = False
            if not adopted:
                raise ValueError('Steam launch options use the AMDNR wrapper in a way that cannot be edited safely. '
                                 'Remove it from the game’s launch options in Steam.')
            restored, _ = _replace_options(text, game.appid, None if without.strip() == '%command%' else without)
            _atomic_write(config, _encode(restored), data, steam_file=True)
            changed = True
        if journal is not None:
            path.unlink()
            backups = path.parent / (game.appid + '-backups')
            for entry in journal['entries']:
                (backups / (entry['account'] + '.vdf')).unlink(missing_ok=True)
            try:
                backups.rmdir()
            except OSError:
                pass
        return changed


def _snapshot(game, path):
    journal, raw = _journal(path, game)
    accounts = _accounts(game) if journal is None else [
        (entry['account'], _account_path(game, entry['account']), _read(_account_path(game, entry['account'])))
        for entry in journal['entries']]
    options = []
    for account, config, data in accounts:
        text = _decode(data)
        _, option = _options(text, game.appid)
        options.append((account, config, data, option.value.value if option else None,
                        text[option.value.start:option.value.end] if option else None))
    backups = path.parent / (game.appid + '-backups')
    saved_backups = {item.name: _read(item) for item in backups.glob('*.vdf')} if backups.exists() else {}
    return raw, options, saved_backups


def _rollback(game, path, snapshot, expected):
    _closed()
    current_journal = _read(path) if path.exists() else None
    old_journal, options, old_backups = snapshot
    allowed_journals = (expected.get('journal:sha256'), _digest(old_journal))
    if _digest(current_journal) not in allowed_journals:
        raise ValueError('Steam launch-option journal changed; rollback preserved the newer journal')
    changes = []
    for account, config, old_data, old_option, raw in options:
        before = _read(config)
        text = _decode(before)
        _, option = _options(text, game.appid)
        current = option.value.value if option else None
        if current == old_option:
            continue
        if current != expected.get(account):
            raise ValueError('Steam options changed during installation; rollback preserved the newer options')
        # Exact original bytes where possible, otherwise edit only this option.
        if _digest(before) == expected.get(account + ':sha256'):
            restored = old_data
        else:
            restored = _encode(_replace_options(text, game.appid, old_option, raw)[0])
        changes.append((config, before, restored))
    for config, before, after in changes:
        _atomic_write(config, after, before, steam_file=True)
    current = _read(path) if path.exists() else None
    if old_journal is None:
        if current is not None:
            path.unlink()
    else:
        _atomic_write(path, old_journal, current)
    backups = path.parent / (game.appid + '-backups')
    if old_backups:
        _private_directory(backups)
    for file in backups.glob('*.vdf'):
        if file.name not in old_backups:
            file.unlink()
    for name, data in old_backups.items():
        target = backups / name
        _atomic_write(target, data, _read(target) if target.exists() else None)
    if not old_backups and backups.exists():
        backups.rmdir()


def _digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def transaction_path(game: SteamGame, state_dir: Path) -> Path:
    return journal_path(game, state_dir).with_suffix('.transaction.json')


def _no_pending_receipt(path):
    receipt = path.with_suffix('.transaction.json')
    if receipt.exists() or receipt.is_symlink():
        raise ValueError('An interrupted Steam operation needs recovery before changing this game')


def _save_receipt(game, path, snapshot):
    """Publish durable snapshots before any ownership or Steam file changes."""
    _no_pending_receipt(path)
    directory = path.with_suffix('.transaction')
    if directory.is_symlink():
        raise ValueError('Steam recovery directory must not be a symlink')
    if directory.exists():
        # No published receipt means the interrupted snapshot preceded changes.
        shutil.rmtree(directory)
    _private_directory(directory)
    old_journal, options, backups = snapshot
    record = {'version': 1, 'appid': game.appid, 'steam_root': _identity(game),
              'prior_journal': base64.b64encode(old_journal).decode() if old_journal is not None else None,
              'options': [], 'backups': {}, 'expected': {'journal:sha256': _digest(old_journal)}}
    for account, _, data, option, raw in options:
        _atomic_write(directory / ('original-' + account + '.vdf'), data, None)
        record['options'].append({'account': account, 'original': option, 'raw': raw, 'sha256': _digest(data)})
        record['expected'][account] = option
        record['expected'][account + ':sha256'] = _digest(data)
    for name, data in backups.items():
        if not re.fullmatch(r'[0-9]+\.vdf', name):
            raise ValueError('Unexpected Steam backup filename')
        _atomic_write(directory / ('backup-' + name), data, None)
        record['backups'][name] = _digest(data)
    _atomic_write(path.with_suffix('.transaction.json'), _encode(json.dumps(record, indent=2) + '\n'), None)


def _receipt_plan(path, changes, journal):
    """Persist desired hashes before the first write, including partial failure."""
    receipt = path.with_suffix('.transaction.json')
    if not receipt.exists():
        return
    before = _read(receipt)
    record = json.loads(before)
    for config, _, after in changes:
        account = config.parent.parent.name
        _, option = _options(_decode(after), record['appid'])
        record['expected'][account] = option.value.value if option else None
        record['expected'][account + ':sha256'] = _digest(after)
    record['expected']['journal:sha256'] = _digest(journal)
    _atomic_write(receipt, _encode(json.dumps(record, indent=2) + '\n'), before)


def _load_receipt(game, path):
    receipt = path.with_suffix('.transaction.json')
    if not receipt.exists() and not receipt.is_symlink():
        return None
    directory = path.with_suffix('.transaction')
    try:
        if directory.is_symlink():
            raise ValueError()
        record = json.loads(_read(receipt))
        if (record['version'] != 1 or record['appid'] != game.appid
                or record['steam_root'] != _identity(game) or not isinstance(record['expected'], dict)
                or not isinstance(record['options'], list) or not isinstance(record['backups'], dict)):
            raise ValueError()
        prior = record['prior_journal']
        prior = base64.b64decode(prior, validate=True) if prior is not None else None
        options, backups, seen = [], {}, set()
        for item in record['options']:
            account = item['account']
            config = _account_path(game, account)
            if account in seen or any(value is not None and not isinstance(value, str)
                                      for value in (item['original'], item['raw'], record['expected'][account])):
                raise ValueError()
            seen.add(account)
            data = _read(directory / ('original-' + account + '.vdf'))
            if _digest(data) != item['sha256']:
                raise ValueError()
            options.append((account, config, data, item['original'], item['raw']))
        for name, checksum in record['backups'].items():
            if not re.fullmatch(r'[0-9]+\.vdf', name):
                raise ValueError()
            data = _read(directory / ('backup-' + name))
            if _digest(data) != checksum:
                raise ValueError()
            backups[name] = data
        return (prior, options, backups), record['expected']
    except (OSError, KeyError, TypeError, ValueError):
        raise ValueError('Invalid Steam recovery snapshot; preserve the launcher state for recovery') from None


def _finish_receipt(game, path, keep_changes):
    loaded = _load_receipt(game, path)
    if loaded is None:
        return
    snapshot, expected = loaded
    if keep_changes:
        for account, config, _, _, _ in snapshot[1]:
            _, option = _options(_decode(_read(config)), game.appid)
            if (option.value.value if option else None) != expected[account]:
                raise ValueError('Steam launch options changed after an interrupted operation; newer options were preserved')
        current = _read(path) if path.exists() else None
        if _digest(current) != expected['journal:sha256']:
            raise ValueError('Steam ownership journal changed after an interrupted operation; it was preserved')
    else:
        _rollback(game, path, snapshot, expected)
    path.with_suffix('.transaction.json').unlink()
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    shutil.rmtree(path.with_suffix('.transaction'))


def recover_launch_transaction(game: SteamGame, state_dir: Path, keep_changes: bool):
    """Finish a crashed controller operation using its durable backend receipt.

    Call only after backend recovery decides whether its corresponding game
    operation committed. False restores pre-call options, True keeps the plan.
    """
    with _lock(game, state_dir) as path:
        _closed()
        _finish_receipt(game, path, keep_changes)


@contextmanager
def _transaction(game, state_dir, wrapper=None, *, defer_recovery=False):
    with _lock(game, state_dir) as path:
        _closed()
        snapshot = _snapshot(game, path)
        _save_receipt(game, path, snapshot)
        try:
            result = _restore(game, path) if wrapper is None else _configure(game, wrapper, path, planned=True)
            yield result
        except BaseException:
            # A controller with its own durable backend receipt must decide
            # whether an exception happened before or after the game commit.
            # Keep our snapshot until that authoritative decision is available.
            if not defer_recovery:
                _finish_receipt(game, path, False)
            raise
        else:
            _finish_receipt(game, path, True)


def launch_transaction(game: SteamGame, wrapper: Path, state_dir: Path, *, defer_recovery=False):
    """Rollback on errors, or defer to an outer durable controller when asked."""
    return _transaction(game, state_dir, wrapper, defer_recovery=defer_recovery)


def restore_launch_transaction(game: SteamGame, state_dir: Path, *, defer_recovery=False):
    """Rollback on errors, or defer to an outer durable controller when asked."""
    return _transaction(game, state_dir, defer_recovery=defer_recovery)
