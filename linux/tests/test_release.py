#!/usr/bin/env python3
"""Exercise release archives with local Git fixtures; no network or game files."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("amdnr_release", ROOT / "package-release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="amdnr release test ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "fork"
        self.source = self.root / "graphics"
        self.package = self.root / "tested package"
        self.output = self.root / "release assets"
        self.marker = b"DO NOT SHIP LOCAL PRIVATE DATA"
        self.init_repo(self.repo)
        self.init_repo(self.source)
        dependency = self.root / "dependency"
        self.init_repo(dependency)
        self.write(dependency / "src.c", b"int dependency(void) { return 1; }\n")
        self.write(dependency / "LICENSES/MIT.txt", b"nested dependency notice\n")
        self.commit(dependency)
        self.git(self.source, "-c", "protocol.file.allow=always", "submodule", "add", "-q",
                 str(dependency), "subprojects/dependency")
        # Preserve a realistic upstream URL, rather than embedding the fixture's
        # local clone path in the archive we are checking for private paths.
        self.git(self.source, "config", "-f", ".gitmodules",
                 "submodule.subprojects/dependency.url", "https://example.invalid/dependency.git")
        for name in ("COPYING", "LICENSE", "AUTHORS"):
            self.write(self.source / name, ("graphics " + name + "\n").encode())
        self.write(self.source / "src.c", b"int shared_buffer = 0;\n")
        self.write(self.source / "build-win64.txt", b"fixture cross file\n")
        self.commit(self.source)
        revision = self.git(self.source, "rev-parse", "HEAD").decode().strip()
        self.write(self.source / "src.c", b"int shared_buffer = 1;\n")
        graphics_patch = self.git(self.source, "diff", "--", "src.c")
        self.write(self.repo / "linux/patches/vkd3d-proton-shared-buffers.patch", graphics_patch)
        for name in ("INSTALL.md", "README.md", "RELEASE-NOTES.md", "setup.py", "build.sh"):
            self.write(self.repo / "linux" / name, ("release fixture " + name + "\n").encode())
        (self.repo / "linux/build.sh").chmod(0o755)
        self.write(self.repo / "linux/bridge/source.c", b"bridge source\n")
        self.write(self.repo / "linux/licenses/toolchain.txt", b"toolchain notice\n")
        self.write(self.repo / "LICENSE", b"fork license\n")
        for name in release.HEADERS:
            self.write(self.repo / "main/AMDNR-source/optiscaler/dlssnr" / name, b"support header\n")
        self.write(self.repo / ".gitignore", b"linux/build/\nlinux/deps/\n*.log\nprivate/\n")
        self.commit(self.repo)
        for name in ("linux/build/local.log", "linux/deps/cached.zip", "private/user.txt"):
            self.write(self.repo / name, self.marker)
        for name in ("build/untracked.o", "local.log"):
            self.write(self.source / name, self.marker)
        self.write(self.source / "subprojects/dependency/untracked.log", self.marker)
        for destination, original in release.BINARIES.items():
            self.write(self.package / original, ("tested binary " + destination).encode())
        for name in ("winmm.dll", "LmxxfNrRuntime.pak", "d3dcompiler_47.dll",
                     "amdnr-linux/rocm/lib/libamdhip64.so.7", "game.sav"):
            self.write(self.package / name, self.marker)
        for owner, name, value in ((release, "ROOT", self.repo),
                                   (release, "VKD3D_REVISION", revision)):
            replacement = patch.object(owner, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)

    def write(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def git(self, directory, *args):
        return subprocess.check_output(["git", "-C", str(directory), *args], stderr=subprocess.STDOUT)

    def init_repo(self, directory):
        directory.mkdir()
        self.git(directory, "init", "-q")
        self.git(directory, "config", "user.name", "Release Tests")
        self.git(directory, "config", "user.email", "release-tests@example.invalid")

    def commit(self, directory):
        self.git(directory, "add", ".")
        self.git(directory, "commit", "-qm", "fixture")

    def package_release(self, output=None):
        argv = ["package-release.py", "--version", "0.1.0", "--package", str(self.package),
                "--vkd3d-source", str(self.source), "--output", str(output or self.output)]
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
            release.main()

    def contents(self, archive):
        with tarfile.open(archive) as tar:
            result = {}
            for member in tar.getmembers():
                path = Path(member.name)
                self.assertFalse(path.is_absolute())
                self.assertNotIn("..", path.parts)
                self.assertNotIn(".git", path.parts)
                self.assertEqual((member.uid, member.gid, member.uname, member.gname), (0, 0, "", ""))
                self.assertTrue(member.isfile(), member.name)
                data = tar.extractfile(member).read()
                self.assertNotIn(self.marker, data, member.name)
                self.assertNotIn(os.fsencode(self.root), data, member.name)
                result[member.name] = data
            return result

    def test_archives_contain_tested_binaries_complete_source_and_not_local_data(self):
        self.package_release()
        addon = self.output / "AMDNR-Linux-v0.1.0.tar.gz"
        source = self.output / "AMDNR-Linux-v0.1.0-vkd3d-source.tar.gz"
        self.assertEqual({path.name for path in self.output.iterdir()},
                         {addon.name, source.name, "INSTALL.md", "SHA256SUMS"})
        files = self.contents(addon)
        prefix = "AMDNR-Linux-v0.1.0/"
        binaries = {prefix + "linux/build/" + name for name in release.BINARIES}
        self.assertEqual({name for name in files if name.endswith((".dll", ".so"))}, binaries)
        sums = {}
        for line in files[prefix + "BINARY-SHA256SUMS"].decode().splitlines():
            digest, name = line.split("  ")
            self.assertEqual(hashlib.sha256(files[prefix + name]).hexdigest(), digest)
            sums[name] = digest
        self.assertEqual(set(sums), {"linux/build/" + name for name in release.BINARIES})
        for destination, original in release.BINARIES.items():
            self.assertEqual(files[prefix + "linux/build/" + destination], (self.package / original).read_bytes())
        manifest = json.loads(files[prefix + "RELEASE-MANIFEST.json"])
        self.assertEqual(manifest["binaries"], sums)
        self.assertEqual(manifest["source_commit"], self.git(self.repo, "rev-parse", "HEAD").decode().strip())
        self.assertIn(prefix + "linux/licenses/toolchain.txt", files)
        self.assertIn(prefix + "linux/build/vkd3d-licenses/subprojects/dependency/LICENSES/MIT.txt", files)
        with tarfile.open(addon) as tar:
            self.assertEqual(tar.getmember(prefix + "linux/build.sh").mode & 0o777, 0o755)
        files = self.contents(source)
        prefix = "AMDNR-Linux-v0.1.0-vkd3d-source/"
        self.assertEqual(files[prefix + "vkd3d-proton/src.c"], b"int shared_buffer = 1;\n")
        self.assertIn(prefix + "vkd3d-proton/subprojects/dependency/src.c", files)
        self.assertIn(prefix + "vkd3d-proton/subprojects/dependency/LICENSES/MIT.txt", files)
        for line in (self.output / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split("  ")
            self.assertEqual(release.sha(self.output / name), digest)
        second = self.root / "second output"
        self.package_release(second)
        for first in self.output.iterdir():
            self.assertEqual(first.read_bytes(), (second / first.name).read_bytes())

    def test_symlinked_input_binary_is_rejected_without_publishing_partial_assets(self):
        path = self.package / release.BINARIES["d3d12.dll"]
        original = self.root / "outside.dll"
        path.rename(original)
        path.symlink_to(original)
        with self.assertRaisesRegex(ValueError, "regular file"):
            self.package_release()
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob("amdnr-release-*")))

    def test_extra_graphics_source_changes_are_rejected_before_packaging(self):
        self.write(self.source / "build-win64.txt", b"unexpected build changes\n")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(subprocess.CalledProcessError):
            self.package_release()
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
