"""Offline packaging safeguards; no downloads, desktop, or game modifications."""
import importlib.util
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
