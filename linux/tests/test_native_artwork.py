"""Offline artwork cache, PE-resource and asynchronous UI regression checks."""
import io
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_launcher.artwork import MAX_IMAGE_BYTES, Source, Store, executable_icon


def icon_executable(pixels=b"fixture icon", pe32=False):
    """A small PE fixture with a group icon and a language-specific RT_ICON."""
    raw = bytearray(0x400 + len(pixels))
    raw[:2] = b"MZ"
    struct.pack_into("<I", raw, 60, 0x80)
    raw[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", raw, 0x86, 1)
    struct.pack_into("<H", raw, 0x94, 240)
    optional = 0x98
    struct.pack_into("<H", raw, optional, 0x10b if pe32 else 0x20b)
    directory = optional + (96 if pe32 else 112)
    struct.pack_into("<I", raw, directory - 4, 16)
    struct.pack_into("<II", raw, directory + 16, 0x1000, len(raw) - 0x200)
    section = optional + 240
    struct.pack_into("<III", raw, section + 12, 0x1000, len(raw) - 0x200, 0x200)

    def table(offset, pairs):
        struct.pack_into("<HH", raw, 0x200 + offset + 12, 0, len(pairs))
        for n, pair in enumerate(pairs):
            struct.pack_into("<II", raw, 0x200 + offset + 16 + n * 8, *pair)

    table(0, [(14, 0x80000030), (3, 0x800000b0)])
    table(0x30, [(1, 0x80000060)])
    table(0x60, [(1033, 0x90)])
    struct.pack_into("<IIII", raw, 0x290, 0x1100, 20, 0, 0)
    group = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHIH", 32, 32, 0, 0, 1, 32, len(pixels), 7)
    raw[0x300:0x314] = group
    table(0xb0, [(7, 0x800000d0)])
    table(0xd0, [(1033, 0xf0)])
    struct.pack_into("<IIII", raw, 0x2f0, 0x1140, len(pixels), 0, 0)
    raw[0x340:0x340 + len(pixels)] = pixels
    return bytes(raw)


class ArtworkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="artwork-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = Store(self.root / "cache")
        self.source = Source("292030", self.root / "Steam")

    @staticmethod
    def decode(data):
        return data if data.startswith(b"image:") else None

    def response(self, data=b"image:steam", content_type="image/jpeg"):
        stream = io.BytesIO(data)
        stream.headers = {"Content-Type": content_type}
        return stream

    def test_download_then_offline_cache_without_another_request(self):
        with patch("native_launcher.artwork.urllib.request.urlopen", return_value=self.response()) as request:
            self.assertEqual(self.store.load(self.source, "thumbnail", self.decode), b"image:steam")
            self.assertIn("/292030/header.jpg", request.call_args.args[0].full_url)
            self.assertEqual(request.call_args.kwargs["timeout"], 5)
        with patch("native_launcher.artwork.urllib.request.urlopen", side_effect=OSError("offline")) as request:
            self.assertEqual(Store(self.store.directory).load(self.source, "thumbnail", self.decode), b"image:steam")
            request.assert_not_called()

    def test_banner_falls_back_to_header_and_caches_it(self):
        with patch("native_launcher.artwork.urllib.request.urlopen", side_effect=[OSError("404"), self.response()]) as request:
            self.assertEqual(self.store.load(self.source, "banner", self.decode), b"image:steam")
            self.assertIn("library_hero.jpg", request.call_args_list[0].args[0].full_url)
            self.assertEqual(request.call_count, 2)

    def test_steam_local_cache_needs_no_network(self):
        local = self.source.steam_root / "appcache/librarycache/292030_header.jpg"
        local.parent.mkdir(parents=True)
        local.write_bytes(b"image:local")
        with patch("native_launcher.artwork.urllib.request.urlopen") as request:
            self.assertEqual(self.store.load(self.source, "thumbnail", self.decode), b"image:local")
            request.assert_not_called()

    def test_invalid_cached_data_is_replaced_by_valid_download(self):
        with patch("native_launcher.artwork.urllib.request.urlopen", return_value=self.response()):
            self.store.load(self.source, "thumbnail", self.decode)
        next(self.store.directory.glob("*.img")).write_bytes(b"corrupt")
        with patch("native_launcher.artwork.urllib.request.urlopen", return_value=self.response(b"image:replacement")):
            self.assertEqual(self.store.load(self.source, "thumbnail", self.decode), b"image:replacement")

    def test_bad_responses_are_not_cached_as_images_and_failures_are_throttled(self):
        for data, content in ((b"<html>error", "text/html"), (b"corrupt", "image/jpeg"),
                              (b"x" * (MAX_IMAGE_BYTES + 1), "image/jpeg")):
            with self.subTest(content=content, length=len(data)):
                store = Store(self.root / str(len(data)))
                with patch("native_launcher.artwork.urllib.request.urlopen", return_value=self.response(data, content)):
                    self.assertIsNone(store.load(self.source, "thumbnail", self.decode))
                self.assertEqual(list(store.directory.glob("*.img")), [])
                with patch("native_launcher.artwork.urllib.request.urlopen") as request:
                    self.assertIsNone(store.load(self.source, "thumbnail", self.decode))
                    request.assert_not_called()

    def test_invalid_app_id_never_becomes_a_url(self):
        with patch("native_launcher.artwork.urllib.request.urlopen") as request:
            self.assertIsNone(self.store.load(Source("../123"), "banner", self.decode))
            request.assert_not_called()

    def test_extract_pe32_and_pe64_icons_without_executing(self):
        for pe32 in (False, True):
            path = self.root / "game.exe"
            path.write_bytes(icon_executable(pe32=pe32))
            ico = executable_icon(path)
            self.assertEqual(struct.unpack_from("<HHH", ico), (0, 1, 1))
            size, offset = struct.unpack_from("<II", ico, 14)
            self.assertEqual(ico[offset:offset + size], b"fixture icon")

    def test_missing_and_malformed_icons_are_safe_fallbacks(self):
        for raw in (b"not PE", b"MZ" + b"\0" * 62, icon_executable()[:300]):
            path = self.root / "game.exe"
            path.write_bytes(raw)
            self.assertIsNone(self.store.load(Source(executable=path), "banner", self.decode))

    def test_icon_cache_refreshes_when_executable_changes(self):
        path = self.root / "game.exe"
        path.write_bytes(b"version one")
        source = Source(executable=path)
        with patch("native_launcher.artwork.executable_icon", return_value=b"image:icon") as extract:
            self.assertEqual(self.store.load(source, "thumbnail", self.decode), b"image:icon")
            self.assertEqual(self.store.load(source, "banner", self.decode), b"image:icon")
            self.assertEqual(extract.call_count, 1)
            path.write_bytes(b"new executable with a new icon")
            self.store.load(source, "thumbnail", self.decode)
            self.assertEqual(extract.call_count, 2)

    def test_resource_outside_section_is_rejected(self):
        raw = bytearray(icon_executable())
        struct.pack_into("<I", raw, 0x2f0, 0xffffffff)
        path = self.root / "game.exe"
        path.write_bytes(raw)
        with self.assertRaises(ValueError):
            executable_icon(path)


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QBuffer, QIODevice, Qt
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from native_launcher.artwork_widgets import ArtworkLoader, decode_image
    from native_launcher.app import Window
    from test_native_gui import FakeController
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, "Optional artwork UI tests need PySide6")
class ArtworkGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="artwork-ui-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.image = QImage(240, 120, QImage.Format.Format_RGB32)
        self.image.fill(QColor("#315e70"))

    def settle(self, condition):
        deadline = time.monotonic() + 5
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(10)
        self.assertTrue(condition())

    def test_slow_artwork_is_background_and_requests_are_deduplicated(self):
        loader = ArtworkLoader(self.root)
        release = threading.Event()
        started = threading.Event()
        results = []

        def slow(*args):
            started.set()
            release.wait(3)
            return self.image

        loader.ready.connect(lambda *result: results.append(result))
        try:
            with patch.object(loader.store, "load", side_effect=slow) as load:
                loader.request(Source("12"), "banner")
                loader.request(Source("12"), "banner")
                self.settle(started.is_set)
                self.assertFalse(results)
                # The Qt main thread keeps processing while the image job waits.
                release.set()
                self.settle(lambda: bool(results))
                self.assertEqual(load.call_count, 1)
                self.assertFalse(results[0][2].isNull())
        finally:
            release.set()
            loader.close()
            loader.pool.waitForDone(5000)

    def test_banner_ignores_late_results_and_thumbnails_survive_search(self):
        with patch("native_launcher.artwork_widgets.Store.load", return_value=self.image):
            window = Window(FakeController(self.root))
            window.show()
            try:
                window.refresh()
                self.settle(lambda: not window.busy and window.banner.image is not None)
                self.assertFalse(window.library.item(0).icon().isNull())
                window.search.setText("adventure")
                self.settle(lambda: not window.busy and window.artwork_key == "steam:13")
                expected = window.banner.image
                window.artwork_ready("steam:12", "banner", None)
                self.assertIs(window.banner.image, expected)
                window.search.setText("absent")
                self.settle(lambda: not window.busy and window.game is None)
                self.assertFalse(window.banner.isVisible())
                self.assertIsNone(window.artwork_key)
                window.artwork_ready("steam:13", "banner", self.image)
                self.assertIsNone(window.banner.image)
            finally:
                self.settle(lambda: not window.busy)
                window.close()
                window.artwork.pool.waitForDone(5000)
                window.deleteLater()

    def test_executable_png_icon_decodes_for_banner(self):
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        self.image.save(buffer, "PNG")
        path = self.root / "game.exe"
        path.write_bytes(icon_executable(bytes(buffer.data())))
        image = decode_image(executable_icon(path))
        self.assertIsNotNone(image)
        self.assertGreater(image.width(), 0)

    def test_largest_executable_icon_frame_is_selected(self):
        frames, entries, offset = [], [], 6 + 2 * 16
        for size in (16, 128):
            image = self.image.scaled(size, size)
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            image.save(buffer, "PNG")
            pixels = bytes(buffer.data())
            entries.append(struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(pixels), offset))
            frames.append(pixels)
            offset += len(pixels)
        image = decode_image(struct.pack("<HHH", 0, 1, 2) + b"".join(entries + frames))
        self.assertIsNotNone(image)
        self.assertEqual(image.width(), 128)

    def test_added_game_uses_its_selected_executable_without_network(self):
        from native_launcher.steam import SteamGame
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        self.image.save(buffer, "PNG")
        executable = self.root / "chosen.exe"
        executable.write_bytes(icon_executable(bytes(buffer.data())))
        controller = FakeController(self.root)
        controller.game_key = lambda game: "manual-game"
        controller.settings["executables"] = {"manual-game": str(executable)}
        controller.games = [SteamGame("", "Manual game", self.root, Path(), self.root, self.root)]
        with patch("native_launcher.artwork.urllib.request.urlopen") as network:
            window = Window(controller)
            window.show()
            try:
                window.refresh()
                self.settle(lambda: not window.busy and window.banner.image is not None)
                self.assertTrue(window.banner.is_icon)
                self.assertEqual(window.artwork_key, "exe:" + str(executable))
                self.settle(lambda: not window.artwork.pending)
                network.assert_not_called()
            finally:
                self.settle(lambda: not window.busy)
                window.close()
                window.artwork.pool.waitForDone(5000)
                window.deleteLater()


if __name__ == "__main__":
    unittest.main()
