# SPDX-License-Identifier: GPL-3.0-or-later
"""Proxy policy and PE parser tests using synthetic files; no games are launched."""
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import proxies


def pe_image(normal=(), delayed=(), *, delay_attributes=1, image_base=0x140000000):
    data = bytearray(0x2200)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", data, 0x84, 0x8664, 1)
    struct.pack_into("<H", data, 0x94, 240)
    optional = 0x98
    struct.pack_into("<H", data, optional, 0x20B)
    struct.pack_into("<Q", data, optional + 24, image_base)
    struct.pack_into("<II", data, optional + 56, 0x3000, 0x200)
    struct.pack_into("<I", data, optional + 108, 16)
    section = optional + 240
    data[section:section + 8] = b".rdata\0\0"
    struct.pack_into("<IIII", data, section + 8, 0x2000, 0x1000, 0x2000, 0x200)
    name_offset = 0x600
    for names, directory, offset, size in ((normal, 1, 0x200, 20), (delayed, 13, 0x400, 32)):
        if not names:
            continue
        struct.pack_into("<II", data, optional + 112 + directory * 8, offset + 0xE00, (len(names) + 1) * size)
        for index, name in enumerate(names):
            encoded = name.encode("ascii") + b"\0"
            data[name_offset:name_offset + len(encoded)] = encoded
            rva = name_offset + 0xE00
            if directory == 1:
                struct.pack_into("<IIIII", data, offset + index * size, 0, 0, 0, rva, 0)
            else:
                address = rva if delay_attributes & 1 else rva + image_base
                struct.pack_into("<II", data, offset + index * size, delay_attributes, address)
            name_offset += len(encoded)
    return data


class ProxyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="amdnr proxy ")
        self.addCleanup(directory.cleanup)
        self.game = Path(directory.name)

    def executable(self, data, name="Game.exe"):
        path = self.game / name
        path.write_bytes(data)
        return path

    def test_normalize_only_supported_names(self):
        for name in proxies.SUPPORTED:
            self.assertEqual(proxies.normalize(name.upper()), name)
            self.assertEqual(proxies.normalize(name[:-4]), name)
        self.assertEqual(proxies.normalize(" Auto ", allow_auto=True), "auto")
        for invalid in ("auto", "next", "dxgi.dll", "d3d12.dll", "d3d11.dll", "../winmm.dll",
                        "C:\\version.dll", "winmm.dll/", "winmm.dll\0", "", None, 1):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                proxies.normalize(invalid)

    def test_normal_and_delay_imports_preserve_order_and_deduplicate(self):
        path = self.executable(pe_image(("KERNEL32.dll", "VERSION.DLL"), ("dbghelp.dll", "version.dll")))
        self.assertEqual(proxies.imports(path), ("kernel32.dll", "version.dll", "dbghelp.dll"))

    def test_old_delay_virtual_addresses_use_image_base(self):
        path = self.executable(pe_image(delayed=("WINHTTP.dll",), delay_attributes=0, image_base=0x400000))
        self.assertEqual(proxies.imports(path), ("winhttp.dll",))

    def test_empty_images_and_unreadable_or_special_files_fall_back(self):
        self.assertEqual(proxies.imports(self.executable(pe_image())), ())
        self.assertEqual(proxies.imports(self.game / "missing.exe"), ())
        self.assertEqual(proxies.imports(self.game), ())
        fifo = self.game / "pipe.exe"
        os.mkfifo(fifo)
        self.assertEqual(proxies.imports(fifo), ())
        with patch.object(proxies.os, "open", side_effect=PermissionError("denied")):
            self.assertEqual(proxies.imports(self.game / "Game.exe"), ())

    def test_malformed_headers_and_addresses_fail_closed(self):
        original = pe_image(("version.dll",))
        mutations = {
            "DOS magic": lambda data: data.__setitem__(slice(0, 2), b"NO"),
            "PE signature": lambda data: data.__setitem__(slice(0x80, 0x84), b"NOPE"),
            "x86": lambda data: struct.pack_into("<H", data, 0x84, 0x14C),
            "PE32": lambda data: struct.pack_into("<H", data, 0x98, 0x10B),
            "section count": lambda data: struct.pack_into("<H", data, 0x86, 65535),
            "header pointer": lambda data: struct.pack_into("<I", data, 0x3C, 0xFFFFFFF0),
            "short optional": lambda data: struct.pack_into("<H", data, 0x94, 111),
            "directory overflow": lambda data: struct.pack_into("<I", data, 0x98 + 108, 17),
            "short headers": lambda data: struct.pack_into("<I", data, 0x98 + 60, 0x100),
            "raw section overflow": lambda data: struct.pack_into("<I", data, 0x188 + 20, 0xFFFFF000),
            "virtual overflow": lambda data: struct.pack_into("<I", data, 0x188 + 12, 0xFFFFFFF0),
            "name out of file": lambda data: struct.pack_into("<I", data, 0x200 + 12, 0x3000),
            "missing null descriptor": lambda data: struct.pack_into("<I", data, 0x98 + 112 + 8 + 4, 20),
            "directory beyond section": lambda data: struct.pack_into("<I", data, 0x98 + 112 + 8 + 4, 0x3000),
            "zero directory with size": lambda data: struct.pack_into("<I", data, 0x98 + 112 + 8, 0),
            "unterminated name": lambda data: data.__setitem__(slice(0x600, 0x800), b"x" * 512),
            "invalid name encoding": lambda data: data.__setitem__(0x600, 0xFF),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                data = original.copy()
                mutate(data)
                self.assertEqual(proxies.imports(self.executable(data)), ())
        for length in (0, 1, 63, 128, 152, 400, len(original) - 1):
            with self.subTest(length=length):
                self.assertEqual(proxies.imports(self.executable(original[:length])), ())

    def test_malformed_delay_directory_drops_partial_normal_results(self):
        data = pe_image(("version.dll",), ("dbghelp.dll",))
        struct.pack_into("<I", data, 0x400, 2)
        self.assertEqual(proxies.imports(self.executable(data)), ())

    def test_virtual_zero_fill_is_not_treated_as_file_data(self):
        data = pe_image(("version.dll",))
        struct.pack_into("<I", data, 0x188 + 16, 0x100)
        self.assertEqual(proxies.imports(self.executable(data)), ())

    def test_parser_only_requests_bounded_reads_for_large_images(self):
        class BoundedReader(io.BytesIO):
            def read(self, count=-1):
                self_outer.assertGreaterEqual(count, 0)
                self_outer.assertLessEqual(count, 65535)
                return super().read(count)
        self_outer = self
        reader = BoundedReader(pe_image(("version.dll",)))
        self.assertEqual(proxies._pe_imports(reader, 2 ** 40), ("version.dll",))

    def test_imported_names_preferred_then_default_order(self):
        self.executable(pe_image(("VERSION.DLL",), ("dbghelp.dll",)))
        self.assertEqual(proxies.candidates(self.game),
                         ("version.dll", "dbghelp.dll", "winmm.dll", "wininet.dll", "winhttp.dll"))
        self.assertEqual(proxies.choose(self.game), "version.dll")

    def test_multiple_executables_use_default_unless_one_is_selected(self):
        self.executable(pe_image(("version.dll",)))
        self.executable(pe_image(("dbghelp.dll",)), "Launcher.exe")
        self.assertEqual(proxies.candidates(self.game), proxies.SUPPORTED)
        self.assertEqual(proxies.choose(self.game, executable="Game.exe"), "version.dll")

    def test_case_insensitive_files_directories_and_broken_links_are_occupied(self):
        (self.game / "WINMM.DLL").write_bytes(b"other mod")
        (self.game / "Version.dll").mkdir()
        (self.game / "DBGHELP.DLL").symlink_to("missing")
        for name in proxies.SUPPORTED[:3]:
            self.assertTrue(proxies.occupied(self.game, name))
            with self.assertRaisesRegex(ValueError, "occupied"):
                proxies.choose(self.game, requested=name)
        self.assertEqual(proxies.choose(self.game), "wininet.dll")

    def test_managed_current_preserved_and_manual_retry_ignores_history(self):
        (self.game / "winmm.dll").write_bytes(b"verified managed host")
        self.executable(pe_image(("version.dll",)))
        self.assertFalse(proxies.occupied(self.game, "winmm.dll", current="winmm.dll"))
        self.assertEqual(proxies.choose(self.game, current="winmm.dll", tried=proxies.SUPPORTED), "winmm.dll")
        self.assertEqual(proxies.choose(self.game, requested="version", current="winmm.dll",
                                        tried=proxies.SUPPORTED), "version.dll")

    def test_current_does_not_authorize_case_collision_or_symlink(self):
        (self.game / "WINMM.DLL").write_bytes(b"foreign")
        self.assertTrue(proxies.occupied(self.game, "winmm.dll", current="winmm.dll"))
        (self.game / "winmm.dll").write_bytes(b"managed")
        self.assertTrue(proxies.occupied(self.game, "winmm.dll", current="winmm.dll"))
        (self.game / "winmm.dll").unlink()
        (self.game / "WINMM.DLL").unlink()
        (self.game / "winmm.dll").symlink_to("missing")
        self.assertTrue(proxies.occupied(self.game, "winmm.dll", current="winmm.dll"))

    def test_next_skips_history_and_occupied_names_and_does_not_cycle(self):
        (self.game / "version.dll").write_bytes(b"foreign")
        self.assertEqual(proxies.choose(self.game, requested="next", current="winmm.dll",
                                        tried=("dbghelp.dll",)), "wininet.dll")
        with self.assertRaisesRegex(ValueError, "No untried free proxy"):
            proxies.choose(self.game, requested="next", current="winmm.dll", tried=proxies.SUPPORTED)

    def test_missing_game_directory_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "Cannot inspect game folder"):
            proxies.choose(self.game / "missing")


class ProxyConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="amdnr proxy record ")
        self.addCleanup(temporary.cleanup)
        self.bundle = Path(temporary.name)
        self.record = self.bundle / "proxy.json"

    def test_legacy_missing_record_and_supported_names(self):
        self.assertEqual(proxies.read_config(self.bundle), "winmm.dll")
        for name in proxies.SUPPORTED:
            self.record.write_text(json.dumps({"proxy": name}))
            self.assertEqual(proxies.read_config(self.bundle), name)

    def test_invalid_records_fail_closed(self):
        records = (b"", b"x" * 4097, b"{", b"\xff", b"[]", b"{}", b'{"proxy":"auto"}',
                   b'{"proxy":"dxgi.dll"}', b'{"proxy":"WINMM.DLL"}', b'{"proxy":"../winmm.dll"}',
                   b'{"proxy":null}', b'{"proxy":[]}', b'{"proxy":"version.dll","extra":true}',
                   b'{"proxy":"version.dll","proxy":"winmm.dll"}', b"[" * 1500 + b"]" * 1500)
        for content in records:
            with self.subTest(content=content[:60]):
                self.record.write_bytes(content)
                with self.assertRaisesRegex(ValueError, "Invalid proxy configuration"):
                    proxies.read_config(self.bundle)

    def test_symlink_directory_and_fifo_rejected_without_blocking(self):
        target = self.bundle / "other.json"
        target.write_text('{"proxy":"version.dll"}')
        self.record.symlink_to(target)
        with self.assertRaises(ValueError):
            proxies.read_config(self.bundle)
        self.record.unlink()
        self.record.mkdir()
        with self.assertRaises(ValueError):
            proxies.read_config(self.bundle)
        self.record.rmdir()
        os.mkfifo(self.record)
        with self.assertRaises(ValueError):
            proxies.read_config(self.bundle)
        link = self.bundle / "bundle-link"
        link.symlink_to(self.bundle, target_is_directory=True)
        with self.assertRaises(ValueError):
            proxies.read_config(link)


if __name__ == "__main__":
    unittest.main()
