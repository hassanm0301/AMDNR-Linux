import importlib.util
import io
from pathlib import Path
import tempfile
import tarfile
import unittest

spec = importlib.util.spec_from_file_location("rocm_reader", Path(__file__).resolve().parents[1] / "fetch-rocm.py")
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


def member(name, data):
    header = f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{len(data):<10}`\n".encode()
    return header + data + (b"\n" if len(data) % 2 else b"")


class DebReaderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.package = Path(temporary.name) / "package.deb"
        self.target = Path(temporary.name) / "data.tar"

    def test_extracts_only_data_without_external_tools(self):
        self.package.write_bytes(b"!<arch>\n" + member("debian-binary", b"2.0\n") + member("control.tar.xz", b"scripts") + member("data.tar.xz", b"tar data"))
        reader.extract_data(self.package, self.target)
        self.assertEqual(self.target.read_bytes(), b"tar data")

    def test_rejects_malformed_or_duplicate_members(self):
        for contents in (b"invalid", b"!<arch>\n" + member("data.tar", b"abc")[:-2],
                         b"!<arch>\n" + member("data.tar", b"a") * 2,
                         b"!<arch>\n" + member("control.tar", b"abc")):
            with self.subTest(contents=contents):
                self.package.write_bytes(contents)
                with self.assertRaises(ValueError):
                    reader.extract_data(self.package, self.target)

    def test_zstandard_tar_extraction(self):
        try:
            from compression import zstd
            compress = zstd.compress
        except ImportError:
            try:
                import zstandard
                compress = zstandard.ZstdCompressor().compress
            except ImportError:
                self.skipTest("Zstandard module is optional outside the portable runtime")
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w") as tar:
            entry = tarfile.TarInfo("usr/lib/example.so")
            entry.size = 7
            tar.addfile(entry, io.BytesIO(b"runtime"))
        self.target.write_bytes(compress(data.getvalue()))
        output = self.target.parent / "extracted"
        output.mkdir()
        reader.extract_tar(self.target, output)
        self.assertEqual((output / "usr/lib/example.so").read_bytes(), b"runtime")
