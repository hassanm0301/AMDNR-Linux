"""Offline packaging safeguards; no downloads, desktop, or game modifications."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("native_bundle_builder", Path(__file__).resolve().parents[1] / "build-native-launcher.py")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class PortableBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="nr-bundle-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def addon(self):
        root = self.root / "addon"
        names = (*BUILDER.REQUIRED_FILES, *BUILDER.BINARIES)
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
        sums = "".join(f"{BUILDER.sha(root / name)}  {name}\n" for name in sorted(BUILDER.BINARIES))
        (root / "BINARY-SHA256SUMS").write_text(sums)
        return root

    def test_regular_assembled_addon_is_accepted(self):
        BUILDER.validate_release(self.addon())

    def test_corrupted_binary_is_rejected(self):
        root = self.addon()
        (root / sorted(BUILDER.BINARIES)[0]).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            BUILDER.validate_release(root)

    def test_extra_checksum_path_is_rejected(self):
        root = self.addon()
        with (root / "BINARY-SHA256SUMS").open("a") as stream:
            stream.write("0" * 64 + "  ../../other\n")
        with self.assertRaisesRegex(ValueError, "Unexpected binary"):
            BUILDER.validate_release(root)

    def test_duplicate_checksum_is_rejected(self):
        root = self.addon()
        path = root / "BINARY-SHA256SUMS"
        path.write_text(path.read_text() + path.read_text().splitlines()[0] + "\n")
        with self.assertRaisesRegex(ValueError, "Unexpected binary"):
            BUILDER.validate_release(root)

    def test_source_directory_symlink_is_rejected(self):
        root = self.addon()
        (root / "linux/unrelated").symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            BUILDER.validate_release(root)

    def test_repository_is_not_an_assembled_addon(self):
        root = self.addon()
        (root / ".git").mkdir()
        with self.assertRaisesRegex(ValueError, "repository checkout"):
            BUILDER.validate_release(root)

    def test_corrupt_cached_runtime_never_executes_or_downloads(self):
        name = BUILDER.PYTHON_ARCHIVE
        (self.root / name).write_bytes(b"unexpected data")
        with patch.object(BUILDER.urllib.request, "urlopen") as request:
            with self.assertRaisesRegex(ValueError, "Invalid cached download"):
                BUILDER.fetch(name, self.root)
            request.assert_not_called()

    def test_archive_rejects_escape_symlink(self):
        directory = self.root / "portable"
        directory.mkdir()
        (directory / "external").symlink_to("../private")
        with self.assertRaisesRegex(ValueError, "Escaping bundle symlink"):
            BUILDER.archive(directory, self.root / "bundle.tar.gz", 0)

    def test_appimage_rejects_escape_before_downloading(self):
        directory = self.root / "AppDir"
        directory.mkdir()
        (directory / "external").symlink_to("../private")
        with patch.object(BUILDER, "fetch") as fetch:
            with self.assertRaisesRegex(ValueError, "Escaping bundle symlink"):
                BUILDER.appimage(directory, self.root / "test.AppImage", self.root, "0.2.2-preview", 0)
            fetch.assert_not_called()

    def test_appimage_runtime_cache_is_verified_before_use(self):
        name = BUILDER.APPIMAGE_RUNTIME
        (self.root / name).write_bytes(b"changed runtime")
        with patch.object(BUILDER.urllib.request, "urlopen") as request:
            with self.assertRaisesRegex(ValueError, "Invalid cached download"):
                BUILDER.fetch(name, self.root, BUILDER.APPIMAGE_DOWNLOADS)
            request.assert_not_called()

    def test_apprun_relocates_and_preserves_arguments_without_system_python(self):
        directory = self.root / "extracted image with spaces"
        python = directory / "runtime/python/bin/python3.12"
        python.parent.mkdir(parents=True)
        # Use a tiny interpreter stand-in so the entry point itself is tested.
        python.write_text("#!/bin/sh\nprintf '%s\\n' \"$NRL_PORTABLE_ROOT\" \"${QT_PLUGIN_PATH-unset}\" \"$@\"\n")
        python.chmod(0o755)
        entry = directory / "AppRun"
        entry.write_text(BUILDER.START)
        entry.chmod(0o755)
        result = subprocess.check_output([str(entry), "--snapshot", "picture with spaces.png"],
                                         cwd=self.root, text=True,
                                         env=dict(os.environ, QT_PLUGIN_PATH="/unrelated/qt"))
        self.assertEqual(result.splitlines(), [str(directory), "unset", "-E", "-s", "-B",
                         str(directory / "addon/linux/launcher.py"), "--release-root",
                         str(directory / "addon"), "--snapshot", "picture with spaces.png"])

    def test_appimage_rejects_invalid_tool_output(self):
        directory = self.root / "AppDir"
        (directory / "runtime/licenses").mkdir(parents=True)
        (directory / "README.txt").write_text("Portable application\n")
        (directory / "RUNTIME-MANIFEST.json").write_text(json.dumps({"version": "0.2.2-preview"}))
        (self.root / BUILDER.APPIMAGETOOL).touch()
        (self.root / "LICENSE.AppImage-runtime.txt").write_text("notice")
        output = self.root / "test.AppImage"
        with patch.object(BUILDER, "fetch"), patch.object(BUILDER, "run", side_effect=lambda *a, **k: output.write_bytes(b"bad")):
            with self.assertRaisesRegex(ValueError, "type-2 AppImage"):
                BUILDER.appimage(directory, output, self.root, "0.2.2-preview", 0)


if __name__ == "__main__":
    unittest.main()
