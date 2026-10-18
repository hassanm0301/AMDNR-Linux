# SPDX-License-Identifier: GPL-3.0-or-later
"""Safe proxy-name selection and bounded PE import inspection.

This module never writes game files or loads an executable. An imported name is
only a selection hint, not proof that a game will load the proxy successfully.
PE layout reference: https://learn.microsoft.com/windows/win32/debug/pe-format
"""
import json
import os
from pathlib import Path
import stat
import struct


SUPPORTED = ("winmm.dll", "version.dll", "dbghelp.dll", "wininet.dll", "winhttp.dll")
# Names a game is known to load from its own folder, among the supported ones.
# Cyberpunk 2077's mod loaders hold winmm.dll and version.dll; Marvel's
# Spider-Man (Nixxes) loads dbghelp.dll. Preferences never displace other files.
PREFERRED = {"cyberpunk2077.exe": "wininet.dll", "spider-man.exe": "dbghelp.dll"}
_MAX_IMPORTS = 4096
_MAX_NAME = 512


def normalize(name, allow_auto=False):
    """Return a canonical supported filename; never accept paths or arbitrary DLLs."""
    if not isinstance(name, str):
        raise ValueError("Proxy name must be text")
    value = name.strip().lower()
    if allow_auto and value == "auto":
        return value
    if value and "." not in value:
        value += ".dll"
    if value not in SUPPORTED:
        raise ValueError("Unsupported proxy name; choose " + ", ".join(SUPPORTED))
    return value


class _InvalidPE(ValueError):
    pass


def _pe_imports(source, length):
    def read(offset, count):
        if offset < 0 or count < 0 or offset + count > length:
            raise _InvalidPE("File range outside executable")
        source.seek(offset)
        data = source.read(count)
        if len(data) != count:
            raise _InvalidPE("Truncated executable")
        return data

    dos = read(0, 64)
    if dos[:2] != b"MZ":
        raise _InvalidPE("Not a DOS executable")
    pe_offset = struct.unpack_from("<I", dos, 60)[0]
    if pe_offset < 64:
        raise _InvalidPE("Invalid PE header offset")
    coff = read(pe_offset, 24)
    machine, section_count = struct.unpack_from("<HH", coff, 4)
    optional_size = struct.unpack_from("<H", coff, 20)[0]
    if coff[:4] != b"PE\0\0" or machine != 0x8664 or not 1 <= section_count <= 96 or optional_size < 112:
        raise _InvalidPE("Not a supported x64 PE image")
    optional = read(pe_offset + 24, optional_size)
    if struct.unpack_from("<H", optional)[0] != 0x20B:
        raise _InvalidPE("Not a PE32+ image")
    image_base = struct.unpack_from("<Q", optional, 24)[0]
    image_size, headers_size = struct.unpack_from("<II", optional, 56)
    directory_count = struct.unpack_from("<I", optional, 108)[0]
    if directory_count > (optional_size - 112) // 8:
        raise _InvalidPE("Data directories exceed optional header")
    section_offset = pe_offset + 24 + optional_size
    if not section_offset + section_count * 40 <= headers_size <= min(length, image_size):
        raise _InvalidPE("Invalid header extent")
    sections = []
    for index in range(section_count):
        section = read(section_offset + index * 40, 40)
        virtual_size, address, raw_size, raw_offset = struct.unpack_from("<IIII", section, 8)
        mapped_size = max(virtual_size, raw_size)
        if address < headers_size or address + mapped_size > image_size:
            raise _InvalidPE("Invalid section memory extent")
        if raw_size and (raw_offset < headers_size or raw_offset + raw_size > length):
            raise _InvalidPE("Invalid section file extent")
        for previous_address, previous_mapped, previous_offset, previous_raw in sections:
            if mapped_size and previous_mapped and address < previous_address + previous_mapped and previous_address < address + mapped_size:
                raise _InvalidPE("Overlapping virtual sections")
            if raw_size and previous_raw and raw_offset < previous_offset + previous_raw and previous_offset < raw_offset + raw_size:
                raise _InvalidPE("Overlapping file sections")
        sections.append((address, mapped_size, raw_offset, raw_size))

    def mapped(rva, count):
        if rva <= 0 or count < 0 or rva + count > image_size:
            raise _InvalidPE("Invalid relative address")
        if rva < headers_size and rva + count <= headers_size:
            return rva, headers_size - rva
        for address, _, raw_offset, raw_size in sections:
            relative = rva - address
            if 0 <= relative < raw_size and relative + count <= raw_size:
                return raw_offset + relative, raw_size - relative
        raise _InvalidPE("Relative address is not backed by file data")

    def dll_name(rva):
        offset, remaining = mapped(rva, 1)
        data = read(offset, min(_MAX_NAME, remaining))
        end = data.find(b"\0")
        if end <= 0:
            raise _InvalidPE("Missing DLL name terminator")
        name = data[:end].decode("ascii")
        if any(ord(character) < 32 or ord(character) > 126 for character in name):
            raise _InvalidPE("Invalid DLL name")
        return name.lower()

    found = []
    for directory, descriptor_size in ((1, 20), (13, 32)):
        if directory >= directory_count:
            continue
        rva, size = struct.unpack_from("<II", optional, 112 + directory * 8)
        if not rva and not size:
            continue
        if not rva or size < descriptor_size:
            raise _InvalidPE("Invalid import directory")
        offset, _ = mapped(rva, size)
        terminated = False
        for index in range(min(size // descriptor_size, _MAX_IMPORTS)):
            descriptor = read(offset + index * descriptor_size, descriptor_size)
            if not any(descriptor):
                terminated = True
                break
            if directory == 1:
                name_rva = struct.unpack_from("<I", descriptor, 12)[0]
            else:
                attributes, name_rva = struct.unpack_from("<II", descriptor)
                if attributes & ~1:
                    raise _InvalidPE("Unsupported delay-import attributes")
                if not attributes & 1:
                    name_rva -= image_base
            name = dll_name(name_rva)
            if name not in found:
                found.append(name)
        if not terminated:
            raise _InvalidPE("Unterminated or excessive import directory")
    return tuple(found)


def imports(path):
    """Read normal/delay DLL imports, or return () for unreadable/malformed images.

    Reads only bounded headers, descriptors and names, even for huge executables.
    Nonblocking open plus fstat prevents special files (such as FIFOs) blocking.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(descriptor, "rb") as source:
            details = os.fstat(source.fileno())
            if not stat.S_ISREG(details.st_mode):
                return ()
            return _pe_imports(source, details.st_size)
    except (OSError, ValueError, TypeError, struct.error, UnicodeError):
        return ()


def _entries(game_dir):
    try:
        return tuple(Path(game_dir).iterdir())
    except OSError as error:
        raise ValueError(f"Cannot inspect game folder: {game_dir}") from error


def _occupied(entries, name, current):
    matches = [entry for entry in entries if entry.name.casefold() == name]
    if not matches:
        return False
    # The caller proves ownership of current. It never grants ownership of a
    # differently-cased sibling, symlink, directory, or second case variant.
    if current == name and len(matches) == 1 and matches[0].name == name:
        try:
            return not stat.S_ISREG(matches[0].lstat().st_mode)
        except OSError:
            return True
    return True


def occupied(game_dir, name, current=None):
    """Whether a name collides with anything except the verified managed current."""
    name = normalize(name)
    current = normalize(current) if current is not None else None
    return _occupied(_entries(game_dir), name, current)


def candidates(game_dir, executable=None, current=None):
    """Return free proxy names: a known per-game name, imports, then SUPPORTED order."""
    current = normalize(current) if current is not None else None
    entries = _entries(game_dir)
    if executable is None:
        executables = [entry for entry in entries if entry.suffix.lower() == ".exe" and entry.is_file()]
        executable = executables[0] if len(executables) == 1 else None
    else:
        executable = Path(executable)
        if not executable.is_absolute():
            executable = Path(game_dir) / executable
    imported = imports(executable) if executable is not None else ()
    preferred = PREFERRED.get(executable.name.lower()) if executable is not None else None
    ordered = (preferred,) if preferred else ()
    ordered += tuple(name for name in imported if name in SUPPORTED and name not in ordered)
    ordered += tuple(name for name in SUPPORTED if name not in ordered)
    return tuple(name for name in ordered if not _occupied(entries, name, current))


def choose(game_dir, requested="auto", executable=None, current=None, tried=()):
    """Choose auto/manual/next without adopting or overwriting foreign slots.

    current must come from verified installation ownership, never user input.
    Auto preserves current. Next excludes current and tried; manual selection
    ignores history so an explicit retry remains possible after exhaustion.
    Selection is advisory; installers must revalidate under their write lock.
    """
    current = normalize(current) if current is not None else None
    action = requested.strip().lower() if isinstance(requested, str) else requested
    next_proxy = action == "next"
    action = "next" if next_proxy else normalize(requested, allow_auto=True)
    available = candidates(game_dir, executable=executable, current=current)
    if action not in ("auto", "next"):
        if action not in available:
            raise ValueError(f"Proxy slot {action} is occupied by another file, directory or symlink")
        return action
    if action == "auto" and current in available:
        return current
    skipped = {normalize(name) for name in tried}
    if next_proxy and current is not None:
        skipped.add(current)
    for name in available:
        if name not in skipped:
            return name
    raise ValueError("No untried free proxy names remain; choose an available name manually to retry")


def read_config(bundle):
    """Read the strict <=4 KiB installed proxy record; legacy installs use winmm."""
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate proxy configuration key")
            result[key] = value
        return result

    directory = None
    try:
        directory = os.open(bundle, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            descriptor = os.open("proxy.json", os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 dir_fd=directory)
        except FileNotFoundError:
            return "winmm.dll"
        with os.fdopen(descriptor, "rb") as source:
            details = os.fstat(source.fileno())
            if not stat.S_ISREG(details.st_mode) or not 0 < details.st_size <= 4096:
                raise ValueError("Proxy configuration must be a regular file of at most 4 KiB")
            data = source.read(4097)
            if len(data) > 4096:
                raise ValueError("Proxy configuration is too large")
        record = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object)
        if not isinstance(record, dict) or set(record) != {"proxy"} or record["proxy"] not in SUPPORTED:
            raise ValueError("Proxy configuration must contain one canonical supported proxy name")
        return record["proxy"]
    except (OSError, ValueError, UnicodeError, RecursionError) as error:
        raise ValueError(f"Invalid proxy configuration in {bundle}: {error}") from error
    finally:
        if directory is not None:
            os.close(directory)
