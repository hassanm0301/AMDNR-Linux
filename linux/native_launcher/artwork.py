# SPDX-License-Identifier: GPL-3.0-or-later
"""Cached Steam artwork and read-only Windows executable icon extraction."""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import struct
import tempfile
import time
import urllib.request

MAX_IMAGE_BYTES = 4 * 1024 * 1024
RETRY_SECONDS = 300
STEAM_CDN = "https://cdn.akamai.steamstatic.com/steam/apps"


@dataclass(frozen=True)
class Source:
    appid: str = ""
    steam_root: Path | None = None
    executable: Path | None = None

    @property
    def key(self):
        if re.fullmatch(r"[0-9]{1,10}", self.appid):
            return "steam:" + self.appid
        return "exe:" + str(self.executable or "")


def executable_icon(path):
    """Build an ICO from PE resources without loading or executing the program.

    Supports PE32/PE32+, PNG and DIB icons. Every offset and allocation is bounded;
    malformed, missing and oversized resources are rejected as unavailable.
    """
    with Path(path).open("rb") as stream:
        file_size = os.fstat(stream.fileno()).st_size

        def read(offset, length):
            if offset < 0 or length < 0 or length > MAX_IMAGE_BYTES or offset + length > file_size:
                raise ValueError("Invalid PE range")
            stream.seek(offset)
            data = stream.read(length)
            if len(data) != length:
                raise ValueError("Truncated PE resource")
            return data

        dos = read(0, 64)
        if dos[:2] != b"MZ":
            raise ValueError("Not a Windows executable")
        pe_offset = struct.unpack_from("<I", dos, 60)[0]
        header = read(pe_offset, 24)
        if header[:4] != b"PE\0\0":
            raise ValueError("Missing PE header")
        count = struct.unpack_from("<H", header, 6)[0]
        optional_length = struct.unpack_from("<H", header, 20)[0]
        if not 1 <= count <= 96 or not 112 <= optional_length <= 4096:
            raise ValueError("Invalid PE headers")
        optional = read(pe_offset + 24, optional_length)
        magic = struct.unpack_from("<H", optional)[0]
        directory = {0x10b: 96, 0x20b: 112}.get(magic)
        if directory is None or len(optional) < directory + 24:
            raise ValueError("Missing resource directory")
        if struct.unpack_from("<I", optional, directory - 4)[0] < 3:
            raise ValueError("Missing resource directory")
        root_rva, root_size = struct.unpack_from("<II", optional, directory + 16)
        if not root_rva or not 16 <= root_size <= 32 * 1024 * 1024:
            raise ValueError("Invalid resource directory")
        sections = read(pe_offset + 24 + optional_length, count * 40)

        def rva_read(rva, length):
            for i in range(count):
                virtual, raw_size, raw_offset = struct.unpack_from("<III", sections, i * 40 + 12)
                if virtual <= rva and rva - virtual + length <= raw_size:
                    return read(raw_offset + rva - virtual, length)
            raise ValueError("Resource outside a PE section")

        def resource(offset, length):
            if offset < 0 or offset + length > root_size:
                raise ValueError("Resource outside its directory")
            return rva_read(root_rva + offset, length)

        def entries(offset):
            table = resource(offset, 16)
            named, numbered = struct.unpack_from("<HH", table, 12)
            if named + numbered > 4096:
                raise ValueError("Too many PE resources")
            data = resource(offset + 16, (named + numbered) * 8)
            return [struct.unpack_from("<II", data, n * 8)
                    for n in range(named + numbered)]

        def child(directory_offset, identifier):
            for name, value in entries(directory_offset):
                if name == identifier and value & 0x80000000:
                    return value & 0x7fffffff
            raise ValueError("Missing icon resource")

        def leaf(directory_offset, language=None):
            choices = [(name, value) for name, value in entries(directory_offset)
                       if not value & 0x80000000]
            if not choices:
                raise ValueError("Missing icon data")
            name, offset = next((item for item in choices if item[0] == language), choices[0])
            rva, size = struct.unpack_from("<II", resource(offset, 16))
            return name, rva_read(rva, size)

        groups = entries(child(0, 14))  # RT_GROUP_ICON
        icons = child(0, 3)  # RT_ICON
        for _, group in groups[:32]:
            if not group & 0x80000000:
                continue
            language, data = leaf(group & 0x7fffffff)
            if len(data) < 6:
                continue
            reserved, kind, total = struct.unpack_from("<HHH", data)
            if reserved or kind != 1 or not 1 <= total <= 64 or len(data) < 6 + total * 14:
                continue
            directory_entries, payload = [], bytearray()
            for n in range(total):
                entry = data[6 + n * 14:20 + n * 14]
                icon_id = struct.unpack_from("<H", entry, 12)[0]
                _, pixels = leaf(child(icons, icon_id), language)
                if len(payload) + len(pixels) > MAX_IMAGE_BYTES - 6 - total * 16:
                    raise ValueError("Icon is too large")
                directory_entries.append(entry[:8] + struct.pack("<II", len(pixels), 6 + total * 16 + len(payload)))
                payload.extend(pixels)
            return struct.pack("<HHH", 0, 1, total) + b"".join(directory_entries) + payload
        raise ValueError("No usable executable icon")


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)

    def _write(self, path, data):
        temporary = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as output:
                temporary = Path(output.name)
                output.write(data)
            temporary.replace(path)
        except OSError:
            pass  # A read-only cache must not prevent artwork from being shown.
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def load(self, source, kind, decode):
        """Run off the UI thread; decode validates images before they are cached."""
        if kind not in ("thumbnail", "banner"):
            return None
        steam = source.key.startswith("steam:")
        if not steam and source.executable is None:
            return None
        identity = source.key + ":" + kind
        if not steam:
            try:
                info = source.executable.stat()
                identity = f"{source.key}:{info.st_mtime_ns}:{info.st_size}"
            except OSError:
                return None
        path = self.directory / (hashlib.sha256(identity.encode()).hexdigest() + ".img")
        try:
            if path.stat().st_size <= MAX_IMAGE_BYTES:
                image = decode(path.read_bytes())
                if image is not None:
                    return image
        except OSError:
            pass
        missing = path.with_suffix(".missing")
        try:
            if time.time() - missing.stat().st_mtime < RETRY_SECONDS:
                return None
        except OSError:
            pass
        if steam:
            names = ("library_hero.jpg", "header.jpg") if kind == "banner" else ("header.jpg",)
            for name in names:
                # Older Steam caches use these filenames. Modern hash-based
                # caches are left alone; our own cache also works offline.
                if source.steam_root is not None:
                    cache = source.steam_root / "appcache/librarycache"
                    for local in (cache / f"{source.appid}_{name}", cache / source.appid / name):
                        try:
                            if local.stat().st_size <= MAX_IMAGE_BYTES:
                                data = local.read_bytes()
                                image = decode(data)
                                if image is not None:
                                    self._write(path, data)
                                    return image
                        except OSError:
                            pass
                try:
                    request = urllib.request.Request(f"{STEAM_CDN}/{source.appid}/{name}",
                                                     headers={"User-Agent": "AMDNR-Linux-Manager"})
                    with urllib.request.urlopen(request, timeout=5) as response:
                        if not response.headers.get("Content-Type", "").lower().startswith("image/"):
                            continue
                        data = response.read(MAX_IMAGE_BYTES + 1)
                    if len(data) <= MAX_IMAGE_BYTES:
                        image = decode(data)
                        if image is not None:
                            self._write(path, data)
                            return image
                except (OSError, ValueError):
                    pass
        else:
            try:
                data = executable_icon(source.executable)
                image = decode(data)
                if image is not None:
                    self._write(path, data)
                    return image
            except (OSError, ValueError, struct.error):
                pass
        self._write(missing, b"")
        return None
