"""Update discovery and verified extraction without external network access."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_launcher import controller


class UpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="native updates ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.version = "0.3.0"
        self.name = "AMDNR-Linux-v0.3.0"
        self.url = f"https://github.com/{controller.REPOSITORY}/releases/download/linux-v0.3.0/{self.name}.tar.gz"

    def archive(self, extra=None, wrong_binary=False):
        files = {"RELEASE-MANIFEST.json": json.dumps({"release": "linux-v0.3.0"}).encode()}
        sums = []
        for name in ("d3d12.dll", "d3d12core.dll", "x86_64-windows/amdhip64_7.dll",
                     "x86_64-unix/amdhip64_7.so", "x86_64-unix/libamdnr_signal_guard.so"):
            path = "linux/build/" + name
            files[path] = b"fixture binary"
            sums.append(hashlib.sha256(files[path]).hexdigest() + "  " + path)
        files["BINARY-SHA256SUMS"] = ("\n".join(sums) + "\n").encode()
        if wrong_binary:
            files["linux/build/d3d12.dll"] = b"changed"
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:gz") as bundle:
            for name, contents in files.items():
                member = tarfile.TarInfo(self.name + "/" + name)
                member.size = len(contents)
                bundle.addfile(member, io.BytesIO(contents))
            if extra:
                bundle.addfile(extra)
        return data.getvalue()

    def release(self, data):
        return dict(version=self.version, url=self.url, sha256=hashlib.sha256(data).hexdigest())

    def test_verified_update_extracts_and_rechecks_cached_archive(self):
        data = self.archive()
        with patch.object(controller.urllib.request, "urlopen", return_value=io.BytesIO(data)) as download:
            root = controller.obtain_addon(self.release(data), self.root, lambda _: None)
        download.assert_called_once()
        self.assertTrue((root / "linux/build/d3d12.dll").is_file())
        (root / "linux/build/d3d12.dll").write_bytes(b"local damage")
        with patch.object(controller.urllib.request, "urlopen") as download:
            controller.obtain_addon(self.release(data), self.root, lambda _: None)
        download.assert_not_called()
        self.assertEqual((root / "linux/build/d3d12.dll").read_bytes(), b"fixture binary")

    def test_rejects_bad_download_digest(self):
        data = self.archive()
        with patch.object(controller.urllib.request, "urlopen", return_value=io.BytesIO(b"wrong bytes")):
            with self.assertRaisesRegex(ValueError, "verification"):
                controller.obtain_addon(self.release(data), self.root, lambda _: None)

    def test_rejects_unsafe_archive_entries(self):
        for kind in ("traversal", "symlink", "absolute"):
            entry = tarfile.TarInfo(self.name + "/../outside" if kind == "traversal" else "/outside" if kind == "absolute" else self.name + "/link")
            if kind == "symlink":
                entry.type = tarfile.SYMTYPE
                entry.linkname = "/tmp/outside"
            data = self.archive(entry)
            with self.subTest(kind=kind), patch.object(controller.urllib.request, "urlopen", return_value=io.BytesIO(data)):
                with self.assertRaisesRegex(ValueError, "unsafe"):
                    controller.obtain_addon(self.release(data), self.root, lambda _: None)

    def test_rejects_inner_binary_checksum_mismatch(self):
        data = self.archive(wrong_binary=True)
        with patch.object(controller.urllib.request, "urlopen", return_value=io.BytesIO(data)):
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                controller.obtain_addon(self.release(data), self.root, lambda _: None)

    def test_discovery_uses_only_expected_fork_asset_with_digest(self):
        asset = dict(name=self.name + ".tar.gz", browser_download_url=self.url, digest="sha256:" + "1" * 64, size=100)
        entries = [dict(tag_name="linux-v0.3.0", draft=False, assets=[asset]),
                   dict(tag_name="linux-v99.0.0", draft=True, assets=[asset]),
                   dict(tag_name="unrelated", draft=False, assets=[asset])]
        with patch.object(controller.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(entries).encode())):
            self.assertEqual(controller.available_release("0.2.0")["version"], "0.3.0")
        asset["browser_download_url"] = "https://untrusted.invalid/update.tar.gz"
        with patch.object(controller.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(entries).encode())):
            self.assertIsNone(controller.available_release("0.2.0"))
