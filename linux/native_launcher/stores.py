# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only discovery of Windows games installed through Heroic and Lutris.

Heroic (Epic Games through legendary, GOG, Amazon through nile, and sideloaded
apps) and Lutris (Wine runner) keep their own launch settings. Nothing here
writes their files: the manager installs into the game folder, and the user adds
the wrapper in that launcher. Both launchers hand the wrapper the game's prefix,
which launch.py requires outside Steam: WINEPREFIX, or STEAM_COMPAT_DATA_PATH
(whose pfx folder holds the prefix) when Heroic runs Proton.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import stat
import string
from urllib.parse import quote

from .steam import SteamGame, _x64_executable, resolve_exe_directory

MAX_FILE_BYTES = 16 * 1024 * 1024
HEROIC_FLATPAK = "com.heroicgameslauncher.hgl"
LUTRIS_FLATPAK = "net.lutris.Lutris"
STORES = {"heroic": "Heroic", "lutris": "Lutris"}
PLACES = {"heroic": "the game's Settings > Advanced > Wrapper command",
          "lutris": "Configure > System options > Command prefix (shown with Advanced)"}
# Heroic's store backends, which its launch links name.
HEROIC_RUNNERS = {"Epic Games": "legendary", "GOG": "gog", "Amazon": "nile", "Sideloaded": "sideload"}
LUTRIS_SERVICES = {"egs": "Epic Games", "gog": "GOG", "amazon": "Amazon", "humblebundle": "Humble Bundle",
                   "itchio": "itch.io", "ubisoft": "Ubisoft Connect", "ea_app": "EA app", "origin": "Origin",
                   "battlenet": "Battle.net"}
# Store clients that Lutris installs as games; the games they start live elsewhere.
CLIENTS = {"eadesktop", "origin", "upc", "ubisoftconnect", "uplay", "battle.net", "battle.net launcher",
           "epicgameslauncher", "galaxyclient", "steam", "rockstar games launcher", "launcher"}


@dataclass(frozen=True)
class StoreGame(SteamGame):
    """A game another Linux launcher starts. appid stays "", as for a manual game."""
    store: str = ""                 # "heroic" or "lutris"
    source: str = ""                # storefront shown in the library (Epic Games, GOG, Wine…)
    store_id: str = ""              # Heroic appName or Lutris slug
    executable: Path | None = None  # the launcher's own target, when it is a usable x64 executable
    prefix: Path | None = None      # the Windows prefix the wrapper will prepare, when known
    runner: str = ""                # Heroic's Wine type: wine, proton, toolkit or crossover
    wrappers: tuple[str, ...] = ()  # wrapper commands configured in the launcher at discovery
    store_runner: str = ""          # Heroic's store backend: legendary, gog, nile or sideload
    lutris_id: str = ""             # Lutris' database id, which its own shortcuts launch


def _base(environ, name, fallback):
    value = environ.get(name)
    return Path(value) if value and Path(value).is_absolute() else fallback


def heroic_roots(home=None, environ=None):
    """Heroic's configuration folders, native and Flatpak."""
    home = Path.home() if home is None else Path(home)
    environ = os.environ if environ is None else environ
    return [_base(environ, "XDG_CONFIG_HOME", home / ".config") / "heroic",
            home / ".var/app" / HEROIC_FLATPAK / "config/heroic"]


def lutris_roots(home=None, environ=None):
    """(configuration, data) folder pairs for native and Flatpak Lutris.

    Lutris reads games/*.yml from its configuration folder, or from its data
    folder when ~/.config/lutris does not exist; pga.db is always in data.
    """
    home = Path.home() if home is None else Path(home)
    environ = os.environ if environ is None else environ
    flatpak = home / ".var/app" / LUTRIS_FLATPAK
    return [(_base(environ, "XDG_CONFIG_HOME", home / ".config") / "lutris",
             _base(environ, "XDG_DATA_HOME", home / ".local/share") / "lutris"),
            (flatpak / "config/lutris", flatpak / "data/lutris")]


def _read(path, limit=MAX_FILE_BYTES):
    """Read a bounded regular file. O_NONBLOCK keeps a FIFO from stalling discovery."""
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError(f"Not a regular file: {path}")
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"Unexpectedly large file: {path}")
    return data


def _json(path):
    try:
        return json.loads(_read(path).decode("utf-8-sig"))
    except RecursionError as error:
        raise ValueError(f"Too deeply nested: {path}") from error


def _optional_json(path, default=None):
    try:
        return _json(path)
    except (OSError, ValueError):
        return default


def _text(value):
    return value if isinstance(value, str) and value and "\0" not in value else None


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _resolve(path):
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return None


def _absolute(value):
    text = _text(value)
    if text is None:
        return None
    path = Path(text).expanduser()
    return path if path.is_absolute() else None


def _directory(value):
    """An existing absolute directory named by launcher metadata."""
    path = _absolute(value)
    path = _resolve(path) if path else None
    return path if path and path.is_dir() else None


def is_prefix(path):
    return (Path(path) / "drive_c").is_dir()


def _relative(value):
    """A launcher's relative path, which may use Windows separators."""
    text = _text(value)
    if text is None or text[:1] in "/\\" or re.match(r"[A-Za-z]:", text):
        return None
    parts = [part for part in re.split(r"[\\/]+", text) if part not in ("", ".")]
    return Path(*parts) if parts and ".." not in parts else None


def _join(root, relative):
    """Join a relative path, matching each part's case as Windows would."""
    path = root
    for part in relative.parts:
        candidate = path / part
        if not candidate.exists():
            try:
                with os.scandir(path) as entries:
                    matches = [entry.name for entry in entries if entry.name.casefold() == part.casefold()]
            except OSError:
                return None
            if len(matches) != 1:
                return None
            candidate = path / matches[0]
        path = candidate
    return path


def _usable(path):
    """The resolved path if it is a 64-bit Windows game executable, not a store client."""
    path = _resolve(path) if path else None
    if (path is None or path.suffix.casefold() != ".exe" or path.stem.casefold() in CLIENTS
            or not path.is_file() or not _x64_executable(path)):
        return None
    return path


def _windows(platform):
    return platform is None or str(platform).casefold() in ("windows", "win32", "win64")


def _locate(install_dir, executable):
    """Scan for the game directory as Steam discovery does; fall back to the
    launcher's own target when the scan is ambiguous. A whole Wine prefix is
    never scanned: its Windows folder holds many unrelated executables."""
    if not is_prefix(install_dir):
        found = resolve_exe_directory(install_dir)
        if found is not None:
            return found
    return executable.parent if executable else None


def _game(store, source, store_id, name, install_dir, executable, **details):
    executable = _usable(executable)
    if executable is not None and not executable.is_relative_to(install_dir):
        executable = None
    return StoreGame("", name or install_dir.name, install_dir, Path(), install_dir,
                     _locate(install_dir, executable), store=store, source=source, store_id=store_id,
                     executable=executable, **details)


def _heroic_settings(root, app_name, defaults):
    """Per-game settings; Heroic copies its defaults there on first use."""
    if re.fullmatch(r"[\w.-]+", app_name) and app_name not in (".", ".."):
        settings = _mapping(_optional_json(root / "GamesConfig" / f"{app_name}.json", {})).get(app_name)
        if isinstance(settings, dict):
            return settings, True
    return defaults, False


def _heroic_details(root, app_name, defaults):
    settings, own = _heroic_settings(root, app_name, defaults)
    runner = (_text(_mapping(settings.get("wineVersion")).get("type")) or "").casefold()
    # The default prefix is a folder of per-game prefixes, so only a game's own
    # setting names its prefix. Proton keeps the Windows prefix in pfx.
    base = _absolute(settings.get("winePrefix")) if own else None
    prefix = None
    if base is not None and runner != "crossover":
        prefix = base / "pfx" if runner == "proton" or (not runner and (base / "pfx").is_dir()) else base
        prefix = _resolve(prefix) or prefix
    wrappers = tuple(text for option in _list(settings.get("wrapperOptions")) if isinstance(option, dict)
                     for text in [_text(option.get("exe"))] if text)
    return dict(prefix=prefix, runner=runner, wrappers=wrappers)


def _heroic_games(root):
    games = []
    defaults = _mapping(_mapping(_optional_json(root / "config.json", {})).get("defaultSettings"))

    def add(source, app_name, title, install_dir, executable):
        app_name = _text(app_name)
        if app_name and install_dir is not None:
            games.append(_game("heroic", source, app_name, _text(title), install_dir, executable,
                               store_runner=HEROIC_RUNNERS[source], **_heroic_details(root, app_name, defaults)))

    epic = _optional_json(root / "legendaryConfig/legendary/installed.json", {})
    for key, entry in sorted(_mapping(epic).items()):
        if not isinstance(entry, dict) or entry.get("is_dlc") or not _windows(entry.get("platform")):
            continue
        install_dir, relative = _directory(entry.get("install_path")), _relative(entry.get("executable"))
        add("Epic Games", entry.get("app_name") or key, entry.get("title"), install_dir,
            _join(install_dir, relative) if install_dir and relative else None)

    titles = {}
    for library in (root / "store_cache/gog_library.json", root / "gog_store/library.json"):
        for item in _list(_mapping(_optional_json(library, {})).get("games")):
            if isinstance(item, dict) and _text(item.get("app_name")) and _text(item.get("title")):
                titles.setdefault(item["app_name"], item["title"])
    for entry in _list(_mapping(_optional_json(root / "gog_store/installed.json", {})).get("installed")):
        if not isinstance(entry, dict) or entry.get("is_dlc") or not _windows(entry.get("platform")):
            continue
        app_name, install_dir = _text(entry.get("appName")), _directory(entry.get("install_path"))
        if not app_name or not re.fullmatch(r"[0-9A-Za-z_-]+", app_name) or install_dir is None:
            continue
        info = _mapping(_optional_json(install_dir / f"goggame-{app_name}.info", {}))
        tasks = [task for task in _list(info.get("playTasks")) if isinstance(task, dict)
                 and task.get("type", "FileTask") == "FileTask" and _relative(task.get("path"))]
        primary = next((task for task in tasks if task.get("isPrimary")), tasks[0] if tasks else None)
        executable = None
        if primary is not None:
            path = _relative(primary["path"])
            working = _relative(primary.get("workingDir"))
            executable = _join(install_dir, path)
            if (executable is None or not executable.is_file()) and working:
                executable = _join(install_dir, working / path)
        add("GOG", app_name, titles.get(app_name) or info.get("name"), install_dir, executable)

    nile = root / "nile_config/nile"
    titles = {}
    for item in _list(_optional_json(nile / "library.json", [])):
        if isinstance(item, dict) and _text(item.get("id")):
            title = _text(_mapping(item.get("product")).get("title"))
            if title:
                titles[item["id"]] = title
    for item in _list(_mapping(_optional_json(root / "store_cache/nile_library.json", {})).get("library")):
        if isinstance(item, dict) and _text(item.get("app_name")) and _text(item.get("title")):
            titles.setdefault(item["app_name"], item["title"])
    for entry in _list(_optional_json(nile / "installed.json", [])):
        if not isinstance(entry, dict):
            continue
        install_dir = _directory(entry.get("path"))
        command = None
        if install_dir is not None:
            main = _mapping(_mapping(_optional_json(install_dir / "fuel.json", {})).get("Main"))
            command = _relative(main.get("Command"))
        app_name = _text(entry.get("id"))
        add("Amazon", app_name, titles.get(app_name), install_dir,
            _join(install_dir, command) if install_dir and command else None)

    for entry in _list(_mapping(_optional_json(root / "sideload_apps/library.json", {})).get("games")):
        install = _mapping(entry.get("install")) if isinstance(entry, dict) else {}
        if not install or entry.get("is_installed") is False or not _windows(install.get("platform")):
            continue
        executable = _absolute(install.get("executable"))
        folder = _directory(entry.get("folder_name"))
        resolved = _resolve(executable) if executable else None
        if folder is None or is_prefix(folder) or not (resolved and resolved.is_relative_to(folder)):
            folder = _directory(str(resolved.parent)) if resolved else None
        add("Sideloaded", entry.get("app_name"), entry.get("title"), folder, executable)
    return games


_ESCAPES = {"0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n", "v": "\v", "f": "\f",
            "r": "\r", "e": "\x1b", " ": " ", '"': '"', "/": "/", "\\": "\\", "N": "\x85", "_": "\xa0",
            "L": " ", "P": " "}


def _quoted(text, index):
    """Scan a YAML quoted scalar from its opening quote: (value, index after it)."""
    mark, value, space = text[index], [], ""
    index += 1
    while index < len(text):
        char = text[index]
        if char == mark:
            if mark == "'" and text.startswith("''", index):
                value.append(space + "'")
                space, index = "", index + 2
                continue
            return "".join(value) + space, index + 1
        if char == "\\" and mark == '"':
            code = text[index + 1:index + 2]
            if code == "\n":
                # An escaped line break joins the lines and keeps earlier spaces.
                value.append(space)
                space, index = "", index + 2
                while index < len(text) and text[index] in " \t":
                    index += 1
                continue
            width = {"x": 2, "u": 4, "U": 8}.get(code, 0)
            digits = text[index + 2:index + 2 + width]
            if code in _ESCAPES:
                char = _ESCAPES[code]
            elif width and len(digits) == width and all(digit in string.hexdigits for digit in digits):
                char = chr(int(digits, 16))
            else:
                raise ValueError("Unsupported YAML escape")
            value.append(space + char)
            space, index = "", index + 2 + width
            continue
        if char in " \t":
            space += char
            index += 1
        elif char == "\n":
            # Line folding: one break reads as a space, each further break as a newline.
            breaks, index = 0, index + 1
            while index < len(text) and text[index] in " \t\n":
                breaks += text[index] == "\n"
                index += 1
            value.append("\n" * breaks or " ")
            space = ""
        else:
            value.append(space + char)
            space, index = "", index + 1
    raise ValueError("Unterminated YAML string")


def _item(body):
    return body[:1] == "-" and body[1:2] in ("", " ", "\t")


def _skip(lines, index, indent, sequence=False):
    """Skip lines nested deeper than indent (and, for a sequence, its further items)."""
    while index < len(lines):
        body = lines[index].lstrip(" ")
        depth = len(lines[index]) - len(body)
        if body.strip() and depth <= indent and not (sequence and depth == indent and _item(body)):
            break
        index += 1
    return index


def _plain(text):
    return re.split(r"[ \t]+#", text, maxsplit=1)[0].strip(" \t")


def parse_yaml(text):
    """Nested block mappings of strings, as PyYAML writes Lutris' game options.

    The bundled runtime has no YAML library. This reads what those files hold:
    block mappings with plain or quoted scalars, including long values folded
    over several lines. Sequences, flow collections, block scalars, anchors and
    tags are skipped as None. Other structure raises ValueError.
    """
    text = text.replace("\r\n", "\n")
    lines = text.split("\n")
    starts = [0]
    for line in lines[:-1]:
        starts.append(starts[-1] + len(line) + 1)
    # (owner indent, mapping or None until its first key, parent mapping, key in parent)
    stack, index = [(-1, {}, None, None)], 0
    root = stack[0][1]
    while index < len(lines):
        line = lines[index]
        body = line.lstrip(" ")
        indent = len(line) - len(body)
        if (not body.strip() or body.startswith("#")
                or (indent == 0 and (body.startswith("%") or body.rstrip() in ("---", "...")))):
            index += 1
            continue
        if body.startswith("\t"):
            raise ValueError("YAML indentation cannot use tabs")
        if _item(body):
            index = _skip(lines, index + 1, indent, sequence=True)
            continue
        while stack[-1][0] >= indent:
            stack.pop()
        owner, mapping, parent, name = stack[-1]
        if mapping is None:
            # A key with nothing after it is null until a nested key makes it a mapping.
            mapping = parent[name] = {}
            stack[-1] = (owner, mapping, parent, name)
        if body[0] in "'\"":
            key, end = _quoted(body, 0)
            match = re.match(r"[ \t]*:(?:[ \t]+|$)", body[end:])
        else:
            match, end = re.search(r":(?:[ \t]+|$)", body), 0
            key = body[:match.start()].rstrip(" \t") if match else None
        if not match or not key:
            raise ValueError("Unsupported YAML line")
        column = indent + end + match.end()
        value = line[column:]
        column += len(value) - len(value.lstrip(" \t"))
        value = value.lstrip(" \t")
        if not value or value.startswith("#"):
            mapping[key] = None
            stack.append((indent, None, mapping, key))
            index += 1
        elif value[0] in "'\"":
            mapping[key], end = _quoted(text, starts[index] + column)
            stop = text.find("\n", end)
            rest = text[end:len(text) if stop < 0 else stop].strip(" \t")
            if rest and not rest.startswith("#"):
                raise ValueError("Unexpected text after a YAML string")
            index += text.count("\n", starts[index], end) + 1
        elif value[0] in "[{|>&*!%@`":
            mapping[key] = None
            index = _skip(lines, index + 1, indent)
        else:
            parts, blanks, index = [_plain(value)], 0, index + 1
            while index < len(lines):
                follow = lines[index].strip(" \t")
                if not follow:
                    blanks, index = blanks + 1, index + 1
                    continue
                if len(lines[index]) - len(lines[index].lstrip(" ")) <= indent or follow.startswith("#"):
                    break
                parts += ["\n" * blanks or " ", _plain(follow)]
                blanks, index = 0, index + 1
            joined = "".join(parts)
            mapping[key] = None if joined in ("~", "null", "Null", "NULL") else joined
    return root


def _yaml_file(path):
    try:
        return parse_yaml(_read(path).decode("utf-8"))
    except (OSError, ValueError):
        return {}


def _find_prefix(executable):
    """Lutris' own fallback: the nearest folder above the executable holding a prefix."""
    for directory in executable.parents:
        for candidate in (directory, directory / "pfx", directory / "prefix"):
            if (candidate / "drive_c").is_dir() and (candidate / "user.reg").exists():
                return candidate
    return None


def _lutris_rows(database):
    if not database.is_file():
        return []
    # Read-only URI: Lutris may be running, and its database is never written.
    connection = sqlite3.connect("file:" + quote(str(database)) + "?mode=ro", uri=True, timeout=2)
    try:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute("SELECT * FROM games")]
    finally:
        connection.close()


def _lutris_games(config, data):
    config = config if config.is_dir() else data
    try:
        rows = _lutris_rows(data / "pga.db")
    except sqlite3.Error:
        return []
    # Lutris layers a game's options over the Wine runner's and the global ones.
    layers = [_mapping(_yaml_file(config / "runners/wine.yml").get("system")),
              _mapping(_yaml_file(config / "system.yml").get("system"))]
    games = []
    for row in rows:
        if row.get("runner") != "wine" or not row.get("installed"):
            continue
        configpath = _text(row.get("configpath"))
        options = {}
        if configpath and re.fullmatch(r"[\w.-]+", configpath) and configpath not in (".", ".."):
            options = _yaml_file(config / "games" / f"{configpath}.yml")
        game = _mapping(options.get("game"))
        directory = _directory(row.get("directory"))
        executable = _absolute(game.get("exe"))
        if executable is None and directory is not None and _relative(game.get("exe")):
            executable = _join(directory, _relative(game.get("exe")))
        usable = _usable(executable)
        if directory is not None and not is_prefix(directory):
            install_dir = directory
        elif usable is not None:
            install_dir = usable.parent  # A game inside a whole-prefix install.
        elif directory is not None:
            install_dir = directory  # The user chooses the game inside this prefix.
        else:
            continue
        prefix = _absolute(game.get("prefix"))
        if prefix is None and executable is not None:
            prefix = _find_prefix(executable)
        command = next((value for layer in (_mapping(options.get("system")), *layers)
                        for value in [layer.get("prefix_command")] if isinstance(value, str)), "")
        try:
            wrappers = tuple(shlex.split(command))
        except ValueError:
            wrappers = ()
        source = LUTRIS_SERVICES.get(row.get("service") or "", "Wine")
        number = row.get("id")
        games.append(_game("lutris", source, _text(row.get("slug")) or str(row.get("id", "")),
                           _text(row.get("name")), install_dir, usable,
                           prefix=(_resolve(prefix) or prefix) if prefix else None, wrappers=wrappers,
                           lutris_id=str(number) if type(number) is int and number > 0 else ""))
    return games


def discover_games(heroic=None, lutris=None):
    """Installed Windows games from Heroic and Lutris; their files are only read.

    Files in an unexpected shape skip that launcher, never the rest of the library.
    """
    found = []
    unreadable = (OSError, ValueError, TypeError, AttributeError, KeyError, sqlite3.Error)
    for root in heroic_roots() if heroic is None else heroic:
        try:
            if Path(root).is_dir():
                found += _heroic_games(Path(root))
        except unreadable:
            continue
    for config, data in lutris_roots() if lutris is None else lutris:
        try:
            found += _lutris_games(Path(config), Path(data))
        except unreadable:
            continue
    games, seen = [], set()
    for game in found:
        if game.install_dir not in seen:
            seen.add(game.install_dir)
            games.append(game)
    return sorted(games, key=lambda game: (game.name.casefold(), game.store, game.store_id))


def store_name(game):
    return STORES.get(getattr(game, "store", ""))


def label(game):
    """The library eyebrow, such as HEROIC / GOG; None for Steam and manual games."""
    name = store_name(game)
    return f"{name.upper()}  /  {game.source.upper()}" if name else None


def wrapper_path(game):
    return game.exe_dir / "amdnr-linux/run.sh" if game.exe_dir else None


def wrapper_line(game):
    """Heroic runs the Wrapper field as one path; Lutris splits Command prefix like a shell."""
    path = str(wrapper_path(game))
    return path if game.store == "heroic" else shlex.quote(path)


def _ours(value):
    path = Path(value).expanduser()
    return path.is_absolute() and path.name == "run.sh" and path.parent.name == "amdnr-linux"


def wrapper_state(game):
    """'current' when the launcher runs this install's wrapper, 'other' for any
    other AMDNR wrapper (an earlier folder or a removed install), else None."""
    target = wrapper_path(game)
    found = None
    for value in getattr(game, "wrappers", ()):
        if not _ours(value):
            continue
        path = Path(value).expanduser()
        if target is not None and (os.path.normpath(path) == os.path.normpath(target)
                                   or _resolve(path) == _resolve(target)):
            return "current"
        found = "other"
    return found


def launch_url(game):
    """The launcher's own link that starts this game, or None.

    Heroic has parsed heroic://launch/<runner>/<appName> since 2022 (newer
    releases also take ?appName=&runner=). It reads the path undecoded, so only
    names that need no escaping are linked. Lutris' desktop and Steam shortcuts
    use lutris:rungameid/<id>; a slug can belong to more than one install.
    """
    if (game.store == "heroic" and game.store_runner in HEROIC_RUNNERS.values()
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._~-]*", game.store_id)):
        return f"heroic://launch/{game.store_runner}/{game.store_id}"
    if game.store == "lutris" and re.fullmatch(r"[1-9][0-9]*", game.lutris_id):
        return f"lutris:rungameid/{game.lutris_id}"
    return None


def launch_issue(game, installed):
    """Why the manager does not start this game through its launcher, or None.

    It only does once the launcher runs this install's wrapper: before that the
    game would start without neural rendering, or not at all.
    """
    name = store_name(game)
    state = wrapper_state(game)
    if launch_url(game) is None:
        return f"{name} has no launch link for this game. Start it from {name}."
    if not installed:
        if state:
            return f"{name} would start this game through an AMDNR wrapper that is not installed."
        return f"Install neural rendering and add the wrapper in {name} first."
    if state != "current":
        verb = "Replace the AMDNR wrapper" if state == "other" else "Add the wrapper"
        return f"{verb} in {name}, then rescan."
    return None


def notices(game, installed=False):
    """Prefix and wrapper notices in compatibility.notices' format."""
    name = store_name(game)
    if not name:
        return []
    found = []
    if game.runner == "crossover":
        found.append(dict(level="warning", text=(
            "Heroic runs this game with CrossOver, which gives the wrapper no WINEPREFIX. Choose a Wine or Proton "
            "version in Heroic, or set WINEPREFIX to the bottle under Settings > Advanced > Environment variables.")))
    elif game.prefix is None and game.store == "lutris":
        found.append(dict(level="warning", text=(
            "Lutris has no Wine prefix for this game, and the wrapper needs one. Set Configure > Game options > "
            "Wine prefix, then rescan.")))
    elif game.prefix is not None and not (game.prefix / "drive_c").is_dir():
        found.append(dict(level="warning", text=(
            f"{name} has not created this game's prefix yet ({game.prefix}). Launch the game once from {name}, "
            "then install.")))
    elif game.prefix is not None:
        found.append(dict(level="info", text=f"{name} gives the wrapper this game's prefix: {game.prefix}"))
    state = wrapper_state(game)
    place = PLACES[game.store]
    if installed and game.exe_dir is not None:
        if state == "current":
            found.append(dict(level="info", text=f"{name} starts this game through the AMDNR wrapper."))
        else:
            verb = "Replace the AMDNR wrapper" if state == "other" else "Add the wrapper"
            found.append(dict(level="warning", text=(
                f"{verb} in {name}, under {place}: {wrapper_line(game)}\nRescan after changing it.")))
    elif state:
        found.append(dict(level="warning", text=(
            f"{name} still starts this game through an AMDNR wrapper that is not installed. Remove it from "
            f"{place}, or the game will not start.")))
    return found


def install_result(game):
    name, place = store_name(game), PLACES[game.store]
    ready = (f"Launch the game from {name}. Enable DLSS, FSR or XeSS in the game; Insert opens OptiScaler "
             "and Home toggles neural rendering.")
    if wrapper_state(game) == "current":
        return {"message": f"Installed. {name} already starts this game through the wrapper. " + ready}
    field = ("Put this path in the Wrapper field and leave Arguments empty." if game.store == "heroic"
             else "Add this at the start of Command prefix.")
    return {"title": f"Add the wrapper in {name}",
            "message": f"Installed. In {name}, open {place}. {field} {ready}",
            "launch_options": wrapper_line(game)}


def removed_message(game):
    name, place = store_name(game), PLACES[game.store]
    if wrapper_state(game):
        return f"Removed. Original game files restored. Remove the AMDNR wrapper from {place} in {name}, or the game will not start."
    return (f"Removed. Original game files restored. If you added the AMDNR wrapper in {name}, remove it from "
            f"{place}, or the game will not start.")
