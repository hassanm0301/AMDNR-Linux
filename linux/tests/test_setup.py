#!/usr/bin/env python3
"""Prebuilt setup integrity and staging tests; all network/runtime work is mocked."""
import importlib.util
import io
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("amdnr_setup", ROOT / "setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def compiler_bytes(machine=0x8664, magic=0x20b, flags=0x2022, marker=b""):
    data = bytearray(512)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3c, 128)
    data[64:64 + len(marker)] = marker
    data[128:132] = b"PE\0\0"
    struct.pack_into("<HH", data, 132, machine, 1)
    struct.pack_into("<HHH", data, 148, 240, flags, magic)
    return bytes(data)


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="amdnr setup test ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.release = self.root / "release with spaces"
        self.linux = self.release / "linux"
        self.game = self.root / "Untouched game"
        self.game.mkdir()
        self.compiler = self.game / "d3dcompiler_47.dll"
        self.compiler.write_bytes(compiler_bytes())
        (self.game / "save.dat").write_bytes(b"user save")
        self.output = self.root / "prepared package"
        self.cache = self.root / "download cache"
        self.manifest = self.release / "BINARY-SHA256SUMS"
        entries = []
        for relative in sorted(setup.BINARY_PATHS):
            path = self.release / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(relative.encode())
            entries.append(f"{setup.digest(path)}  {relative}\n")
        self.manifest.write_text("".join(entries))
        for owner, name, value in ((setup, "ROOT", self.linux), (setup.manage, "ROOT", self.linux)):
            replacement = patch.object(owner, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)

    def test_pinned_upstream_release(self):
        self.assertEqual(setup.RELEASE_VERSION, "0.3.5.6")
        self.assertEqual(setup.RELEASE_SHA256, "e7ffc09696f1a4e06d26dc0c228d15a9c74a0e56e3d24be6a130a0f5e49968d8")
        self.assertEqual(setup.RELEASE_URL, "https://github.com/3zwr1/AMD-NR---OptiScaler/releases/download/"
                                            "Alpha0.3.5.6/AMDNR-v0.3.5.6.zip")
        # Earlier pinned archives stay accepted for staging by hand.
        self.assertEqual(setup.manage.RELEASES["725cef9da0608621fe3233a7127e6511b9543061fcecaba8348b2c55c608d448"],
                         "0.3.5.5R4")
        self.assertEqual(len(set(setup.manage.RELEASES.values())), len(setup.manage.RELEASES))
        self.assertIn(setup.RELEASE_VERSION, setup.runtimes.HOST_RUNTIMES)

    def test_complete_binary_manifest(self):
        setup.verify_binaries()

    def test_changed_binary_rejected_before_download(self):
        (self.linux / "build/x86_64-unix/amdhip64_7.so").write_bytes(b"changed")
        with patch.object(setup, "fetch_archive") as download, patch.object(setup, "fetch_runtime") as runtime:
            with self.assertRaisesRegex(ValueError, "binary checksum mismatch"):
                setup.prepare(self.compiler, output=self.output, cache=self.cache)
        download.assert_not_called()
        runtime.assert_not_called()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.cache.exists())

    def test_manifest_rejects_missing_extra_duplicate_and_traversal(self):
        original = self.manifest.read_text()
        first = original.splitlines()[0]
        for content in ("\n".join(original.splitlines()[1:]) + "\n", original + first + "\n",
                        original + "0" * 64 + "  ../outside\n",
                        original + "0" * 64 + "  linux/build/extra.dll\n"):
            with self.subTest(content=content):
                self.manifest.write_text(content)
                with self.assertRaises(ValueError):
                    setup.verify_binaries()

    def test_symlink_binary_is_rejected_even_with_matching_contents(self):
        path = self.linux / "build/d3d12.dll"
        outside = self.root / "outside.dll"
        path.rename(outside)
        path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symlinked"):
            setup.verify_binaries()

    def test_compiler_requires_native_x64_pe_dll(self):
        setup.validate_compiler(self.compiler)
        for data in (compiler_bytes(machine=0x14c), compiler_bytes(magic=0x10b),
                     compiler_bytes(flags=0x22), compiler_bytes(marker=b"Wine builtin DLL"),
                     compiler_bytes(marker=b"Wine placeholder DLL"), b"MZ invalid header"):
            with self.subTest(data=data[:80]):
                self.compiler.write_bytes(data)
                with self.assertRaises(ValueError):
                    setup.validate_compiler(self.compiler)

    def test_invalid_compiler_prevents_network_and_output(self):
        self.compiler.write_bytes(compiler_bytes(marker=b"Wine builtin DLL"))
        with patch.object(setup, "fetch_archive") as download, patch.object(setup, "verify_binaries") as verify:
            with self.assertRaisesRegex(ValueError, "builtin"):
                setup.prepare(self.compiler, output=self.output, cache=self.cache)
        download.assert_not_called()
        verify.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_cached_archive_reused_only_after_hash_validation(self):
        self.cache.mkdir()
        cached = self.cache / setup.ARCHIVE_NAME
        cached.write_bytes(b"valid pinned archive")
        expected = setup.digest(cached)
        with patch.object(setup, "RELEASE_SHA256", expected), patch.object(setup.urllib.request, "urlopen") as download:
            self.assertEqual(setup.fetch_archive(self.cache), cached)
        download.assert_not_called()

    def test_bad_cache_is_repaired_atomically(self):
        self.cache.mkdir()
        cached = self.cache / setup.ARCHIVE_NAME
        cached.write_bytes(b"corrupt cache")
        verified = self.root / "verified.zip"
        verified.write_bytes(b"valid pinned archive")
        with patch.object(setup, "RELEASE_SHA256", setup.digest(verified)), \
                patch.object(setup.urllib.request, "urlopen", return_value=io.BytesIO(verified.read_bytes())) as download:
            self.assertEqual(setup.fetch_archive(self.cache), cached)
        self.assertEqual(cached.read_bytes(), verified.read_bytes())
        self.assertEqual(set(self.cache.iterdir()), {cached})
        self.assertEqual(download.call_args.args[0].full_url, setup.RELEASE_URL)

    def test_bad_download_does_not_replace_cache_or_leave_partial(self):
        self.cache.mkdir()
        cached = self.cache / setup.ARCHIVE_NAME
        cached.write_bytes(b"old corrupt cache")
        with patch.object(setup.urllib.request, "urlopen", return_value=io.BytesIO(b"bad download")):
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                setup.fetch_archive(self.cache)
        self.assertEqual(cached.read_bytes(), b"old corrupt cache")
        self.assertEqual(set(self.cache.iterdir()), {cached})

    def test_network_error_cleans_partial(self):
        with patch.object(setup.urllib.request, "urlopen", side_effect=OSError("network interrupted")):
            with self.assertRaisesRegex(OSError, "network interrupted"):
                setup.fetch_archive(self.cache)
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_existing_output_and_nested_cache_rejected(self):
        self.output.mkdir()
        (self.output / "keep").write_bytes(b"existing package")
        with patch.object(setup, "fetch_archive") as download:
            with self.assertRaisesRegex(ValueError, "Output already exists"):
                setup.prepare(self.compiler, output=self.output, cache=self.cache)
        download.assert_not_called()
        self.assertEqual((self.output / "keep").read_bytes(), b"existing package")
        with self.assertRaisesRegex(ValueError, "outside"):
            setup.prepare(self.compiler, output=self.root / "new", cache=self.root / "new/cache")

    def test_wrong_explicit_archive_is_not_downloaded_or_modified(self):
        archive = self.root / "unrecognized.zip"
        archive.write_bytes(b"user archive")
        with patch.object(setup.shutil, "which", return_value="/usr/bin/ar"), \
                patch.object(setup, "fetch_archive") as download, patch.object(setup, "fetch_runtime") as runtime:
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                setup.prepare(self.compiler, archive, self.output, self.cache)
        download.assert_not_called()
        runtime.assert_not_called()
        self.assertEqual(archive.read_bytes(), b"user archive")

    def test_alternate_host_is_downloaded_with_a_runtime_it_drives(self):
        build = setup.runtimes.PRESR
        hosted = self.root / "pre-SR.zip"
        hosted.write_bytes(b"pinned elsewhere")
        with patch.object(setup, "fetch_archive") as amdnr, patch.object(setup, "fetch_runtime"), \
                patch.object(setup.runtimes, "prepare_host", return_value=(build, hosted)) as host, \
                patch.object(setup.runtimes, "prepare_daniel", return_value=("runtime", "files")) as daniel, \
                patch.object(setup.manage, "stage") as stage:
            setup.prepare(self.compiler, output=self.output, cache=self.cache, host="presr")
        amdnr.assert_not_called()
        host.assert_called_once_with("presr", self.cache)
        self.assertEqual(daniel.call_args.args[0], setup.runtimes.HOST_RUNTIMES[build.host_version][0])
        self.assertEqual(stage.call_args.args[0], hosted)
        self.assertEqual(stage.call_args.kwargs["daniel"], ("runtime", "files"))

    def test_host_and_runtime_pairings_are_checked_before_downloading(self):
        archive = self.root / "other.zip"
        archive.write_bytes(b"not the pre-SR release")
        cases = ((dict(host="presr", daniel="0.6.0"), "drives danielblnc runtime 0.3.1 only"),
                 (dict(daniel="0.3.1"), "is offered with danielblnc runtime"),
                 (dict(host="presr", archive=archive), "pinned, unmodified OptiScaler AMD pre-SR"),
                 (dict(host="other"), "Unknown host build"))
        for options, message in cases:
            with self.subTest(message), patch.object(setup, "fetch_archive") as amdnr, \
                    patch.object(setup, "fetch_runtime") as runtime, \
                    patch.object(setup.runtimes, "fetch") as fetch, self.assertRaisesRegex(ValueError, message):
                setup.prepare(self.compiler, output=self.output, cache=self.cache, **options)
            amdnr.assert_not_called()
            runtime.assert_not_called()
            fetch.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_model_e_is_built_before_the_host_download_and_staged(self):
        built = (self.root / "model-e", {"dlss_version": "310.7.0"})
        with patch.object(setup, "fetch_archive", side_effect=AssertionError("built after the download")) as amdnr, \
                patch.object(setup.runtimes, "build_model_e", side_effect=setup.runtimes.ModelEConsentNeeded("accept")) as build, \
                self.assertRaises(setup.runtimes.ModelEConsentNeeded):
            setup.prepare(self.compiler, output=self.output, cache=self.cache, model_e=True)
        amdnr.assert_not_called()
        self.assertEqual(build.call_args.args[:3], (self.cache, None, False))
        with patch.object(setup, "fetch_archive", return_value=self.root / "amdnr.zip"), \
                patch.object(setup, "fetch_runtime"), \
                patch.object(setup.runtimes, "build_model_e", return_value=built) as build, \
                patch.object(setup.manage, "stage") as stage:
            setup.prepare(self.compiler, output=self.output, cache=self.cache, model_e=True,
                          dlss=self.root / "nvngx_dlss.dll", accept_dlss_license=False)
        self.assertEqual(build.call_args.args[:3], (self.cache, self.root / "nvngx_dlss.dll", False))
        self.assertEqual(stage.call_args.kwargs["model_e"], built)
        with patch.object(setup.runtimes, "build_model_e") as build, self.assertRaisesRegex(ValueError, "needs the AMDNR host"):
            setup.prepare(self.compiler, output=self.root / "other", cache=self.cache, host="presr", model_e=True)
        build.assert_not_called()

    def test_dlss_options_need_model_e(self):
        for options in (["--dlss", str(self.compiler)], ["--accept-nvidia-dlss-license"]):
            with self.subTest(options), patch.object(setup, "prepare") as prepare, \
                    patch("sys.stderr"), self.assertRaises(SystemExit):
                setup.main(["prepare", "--compiler", str(self.compiler), *options])
            prepare.assert_not_called()
        with patch.object(setup, "prepare") as prepare:
            setup.main(["prepare", "--compiler", str(self.compiler), "--model-e", "--accept-nvidia-dlss-license"])
        self.assertEqual(prepare.call_args.args[7:], (True, None, True))

    def test_binutils_is_not_required_by_preparation(self):
        with patch.object(setup.shutil, "which", return_value=None), \
                patch.object(setup, "fetch_archive") as download, \
                patch.object(setup, "fetch_runtime") as runtime, \
                patch.object(setup.manage, "stage") as stage:
            setup.prepare(self.compiler, output=self.output, cache=self.cache)
        download.assert_called_once()
        runtime.assert_called_once()
        stage.assert_called_once()

    def test_runtime_failure_cleans_staging_without_changing_game(self):
        archive = self.root / "pinned.zip"
        archive.write_bytes(b"verified archive")
        before = {path.name: path.read_bytes() for path in self.game.iterdir()}

        def fail(output, cache):
            output.mkdir()
            (output / "partial-library").write_bytes(b"partial runtime")
            raise OSError("interrupted runtime extraction")

        with patch.object(setup, "RELEASE_SHA256", setup.digest(archive)), \
                patch.object(setup, "fetch_runtime", side_effect=fail), \
                patch.object(setup.shutil, "which", return_value="/usr/bin/ar"), \
                patch.object(setup.manage, "stage") as stage:
            with self.assertRaisesRegex(OSError, "interrupted runtime"):
                setup.prepare(self.compiler, archive, self.output, self.cache)
        stage.assert_not_called()
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.output.parent.glob("amdnr-prepare-*")))
        self.assertEqual({path.name: path.read_bytes() for path in self.game.iterdir()}, before)

    def test_preparation_uses_real_stage_without_changing_game(self):
        # Model the small source payload shipped beside the five binaries.
        for directory in ("bridge", "patches", "tests"):
            (self.linux / directory).mkdir()
        for name in ("run.sh", "launch.py", "proxies.py"):
            (self.linux / name).write_text("fixture\n")
        (self.release / "LICENSE").write_text("fixture license\n")
        for relative in ("amd/TemporalStability.h", "amd/SystemCompiler.h",
                         "lmxxf/runtime/LmxxfNrApi.h", "lmxxf/runtime/LICENSE.lmxxf"):
            path = self.release / "main/AMDNR-source/optiscaler/dlssnr" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture header\n")
        archive = self.root / "valid release.zip"
        with zipfile.ZipFile(archive, "w") as release:
            release.writestr("OptiScaler.ini", "[DlssNr]\nEnabled=false\nNrBackend=auto\n")
            for name in ("OptiScaler.dll", "LmxxfNrRuntime.dll", "LmxxfNrRuntime.pak"):
                release.writestr(name, name)
        expected = setup.digest(archive)
        before = {path.name: path.read_bytes() for path in self.game.iterdir()}
        runtimes = []

        def runtime(output, cache):
            self.assertEqual(cache, self.cache)
            self.assertFalse(output.exists())
            (output / "lib").mkdir(parents=True)
            (output / "lib/libamdhip64.so.7").write_bytes(b"verified runtime")
            runtimes.append(output)

        with patch.object(setup, "RELEASE_SHA256", expected), \
                patch.dict(setup.manage.RELEASES, {expected: setup.RELEASE_VERSION}), \
                patch.object(setup, "fetch_runtime", side_effect=runtime), \
                patch.object(setup, "fetch_archive") as download, \
                patch.object(setup.shutil, "which", return_value="/usr/bin/ar"), \
                patch.object(setup.manage, "install", side_effect=AssertionError("No game installation allowed")):
            self.assertEqual(setup.prepare(self.compiler, archive, self.output, self.cache), self.output)
        download.assert_not_called()
        self.assertEqual({path.name: path.read_bytes() for path in self.game.iterdir()}, before)
        self.assertEqual((self.output / "d3dcompiler_47.dll").read_bytes(), before["d3dcompiler_47.dll"])
        self.assertEqual((self.output / "amdnr-linux/rocm/lib/libamdhip64.so.7").read_bytes(), b"verified runtime")
        self.assertIn("Enabled=true", (self.output / "OptiScaler.ini").read_text())
        self.assertIn("NrBackend=lmxxf", (self.output / "OptiScaler.ini").read_text())
        self.assertTrue((self.output / "amdnr-linux/x86_64-unix/amdhip64_7.so").is_file())
        self.assertFalse((self.output / "amdnr-linux/x86_64-unix/amdhip64_7.dll.so").exists())
        self.assertFalse(runtimes[0].exists())
        self.assertFalse(list(self.output.parent.glob("amdnr-prepare-*")))


if __name__ == "__main__":
    unittest.main()
