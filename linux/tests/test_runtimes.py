"""Optional component catalog, verification and staging, on synthetic files only."""
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import os
import re
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


runtimes = module("runtimes")
manage = module("manage")


def digest(data):
    return hashlib.sha256(data).hexdigest()


class CatalogTests(unittest.TestCase):
    def test_pins_are_well_formed_and_hosts_name_known_runtimes(self):
        versions = [runtime.version for runtime in runtimes.DANIEL_RUNTIMES]
        self.assertEqual(len(set(versions)), len(versions))
        for runtime in runtimes.DANIEL_RUNTIMES:
            self.assertRegex(runtime.sha256, r"^[0-9a-f]{64}$")
            self.assertRegex(runtime.pass_sha256, r"^[0-9a-f]{64}$")
            self.assertTrue(runtime.url.startswith("https://github.com/3zwr1/AMD-NR---OptiScaler/releases/download/"))
            self.assertTrue(runtime.url.endswith(f"/v{runtime.version}-Runtime.zip"))
            self.assertTrue(set(runtime.generations) <= {"rdna4", "rdna3", "rdna2"})
        for host, offered in runtimes.HOST_RUNTIMES.items():
            self.assertTrue(set(offered) <= set(versions), host)
        self.assertEqual(len({runtime.pass_sha256 for runtime in runtimes.DANIEL_RUNTIMES}), len(versions))
        for plugin in runtimes.OPTIPATCHER:
            self.assertRegex(plugin.sha256, r"^[0-9a-f]{64}$")
            self.assertTrue(plugin.url.startswith("https://github.com/optiscaler/OptiPatcher/releases/download/"))

    def test_host_builds_are_pinned_and_list_runtimes_they_drive(self):
        versions = [runtime.version for runtime in runtimes.DANIEL_RUNTIMES]
        for build in runtimes.HOST_BUILDS:
            self.assertRegex(build.sha256, r"^[0-9a-f]{64}$")
            self.assertTrue(build.url.startswith(build.homepage + "/releases/download/"))
            self.assertNotIn(build.host_version, manage.RELEASES.values())
            self.assertTrue(runtimes.HOST_RUNTIMES[build.host_version])
            self.assertTrue(set(runtimes.HOST_RUNTIMES[build.host_version]) <= set(versions))
            self.assertIn("OptiScaler.dll", build.files)
            self.assertFalse(any(name.lower().endswith((".bat", ".ps1")) for name in build.files))
        self.assertIs(runtimes.host_build("presr"), runtimes.PRESR)
        self.assertIs(runtimes.host_build_for(runtimes.PRESR.sha256), runtimes.PRESR)
        self.assertIsNone(runtimes.host_build(None))
        self.assertIsNone(runtimes.host_build("amdnr"))
        self.assertIsNone(runtimes.host_build_for("0" * 64))
        with self.assertRaises(ValueError):
            runtimes.host_build("other")
        # The pre-SR host identifies danielblnc's own 0.3.x builds, not the 0.4+ repackaged ones.
        self.assertEqual(runtimes.HOST_RUNTIMES[runtimes.PRESR.host_version], ("0.3.1",))
        self.assertNotIn("0.3.1", runtimes.HOST_RUNTIMES["0.3.5.5R4"])
        self.assertNotIn("0.3.1", runtimes.HOST_RUNTIMES["0.3.5.6"])

    def test_every_amdnr_release_lists_runtimes_and_separate_lmxxf_archives_are_pinned(self):
        for version in manage.RELEASES.values():
            self.assertIn(version, runtimes.HOST_RUNTIMES)
            self.assertNotIn("0.3.1", runtimes.HOST_RUNTIMES[version])
        # 0.3.5.8R2's host pins the same pass digests as 0.3.5.6.
        self.assertEqual(runtimes.HOST_RUNTIMES["0.3.5.8R2"], runtimes.HOST_RUNTIMES["0.3.5.6"])
        self.assertEqual(set(manage.LMXXF_ARCHIVES), {"0.3.5.8R2"})
        for version, separate in manage.LMXXF_ARCHIVES.items():
            self.assertIn(version, manage.RELEASES.values())
            self.assertRegex(separate.sha256, r"^[0-9a-f]{64}$")
            self.assertEqual(separate.version, version)
            self.assertEqual(separate.url, "https://github.com/3zwr1/AMD-NR---OptiScaler/releases/download/"
                                           f"Alpha{version}/{separate.name}")
            self.assertEqual(separate.name, f"AMDNR-lmxxf-{version}.zip")

    def test_lookup_rejects_unknown_versions(self):
        self.assertEqual(runtimes.daniel_runtime("0.5.1").pass_sha256[:8], "493b4a3b")
        self.assertEqual(runtimes.optipatcher().version, runtimes.OPTIPATCHER[0].version)
        self.assertIs(runtimes.runtime_for_pass(runtimes.DANIEL_RUNTIMES[0].pass_sha256), runtimes.DANIEL_RUNTIMES[0])
        for call in (lambda: runtimes.daniel_runtime("9.9.9"), lambda: runtimes.optipatcher("0.01")):
            with self.assertRaises(ValueError):
                call()


class HostAndGpuTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="amdnr runtimes ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_host_digest_is_found_across_read_chunks(self):
        runtime = runtimes.DANIEL_RUNTIMES[1]
        host = self.root / "OptiScaler.dll"
        needle = bytes.fromhex(runtime.pass_sha256)
        host.write_bytes(b"MZ" + b"\0" * (1024 * 1024 - 12) + needle + b"\0" * 64)
        self.assertTrue(runtimes.host_drives(host, runtime))
        self.assertFalse(runtimes.host_drives(host, runtimes.DANIEL_RUNTIMES[0]))
        with self.assertRaises(ValueError):
            runtimes.host_drives(self.root, runtime)

    def topology(self, nodes):
        root = self.root / "nodes"
        for index, properties in enumerate(nodes):
            node = root / str(index)
            node.mkdir(parents=True)
            (node / "properties").write_text("".join(f"{key} {value}\n" for key, value in properties.items()))
        return root

    def test_gpu_detection_prefers_the_largest_gpu_and_marks_apus(self):
        cpu = {"cpu_cores_count": 8, "simd_count": 0, "gfx_target_version": 0}
        discrete = {"simd_count": 128, "gfx_target_version": 120001}
        integrated = {"simd_count": 4, "gfx_target_version": 100306}
        gpu = runtimes.detect_gpu(self.topology([cpu, discrete, integrated]))
        self.assertEqual(gpu, {"gfx": "gfx1201", "generation": "rdna4", "apu": False, "simd_count": 128})
        self.assertIsNone(runtimes.detect_gpu(self.root / "missing"))

    def test_gpu_and_host_filters(self):
        def offered(gpu, host_version="0.3.5.6"):
            return [runtime.version for runtime in runtimes.runtimes_for(gpu, host_version=host_version)]
        rdna4 = {"gfx": "gfx1201", "generation": "rdna4", "apu": False}
        self.assertEqual(offered(rdna4), ["0.6.0", "0.5.1", "0.5.0", "0.4.3", "0.4.1", "0.4.0"])
        self.assertEqual(offered(rdna4, "0.3.4.2"), ["0.5.0", "0.4.3", "0.4.1", "0.4.0"])
        self.assertEqual(offered(rdna4, "0.3.5.5R4"), offered(rdna4))
        self.assertEqual(offered({"gfx": "gfx1030", "generation": "rdna2", "apu": False}), ["0.6.0"])
        self.assertEqual(offered({"gfx": "gfx1103", "generation": "rdna3", "apu": True}), [])
        self.assertEqual(offered({"gfx": "gfx1010", "generation": "rdna1", "apu": False}), [])
        self.assertEqual(offered(None), list(runtimes.HOST_RUNTIMES["0.3.5.6"]))
        self.assertEqual(offered(rdna4, "unknown host"), [])
        presr = runtimes.PRESR.host_version
        self.assertEqual(offered(rdna4, presr), ["0.3.1"])
        self.assertEqual(offered({"gfx": "gfx1100", "generation": "rdna3", "apu": False}, presr), ["0.3.1"])
        self.assertEqual(offered({"gfx": "gfx1030", "generation": "rdna2", "apu": False}, presr), [])


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="amdnr runtime archive ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.pass_data = b"pass DLL fixture"

    def archive(self, members):
        path = self.root / f"archive-{len(list(self.root.iterdir()))}.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in members.items():
                archive.writestr(name, data)
        runtime = replace(runtimes.DANIEL_RUNTIMES[0], sha256=digest(path.read_bytes()), pass_sha256=digest(self.pass_data))
        return path, runtime

    def members(self, **changes):
        members = {name: self.pass_data for name in runtimes.DANIEL_PASSES}
        members.update({runtimes.DANIEL_WEIGHTS: b"weights", "danielblnc_ATTRIBUTION.txt": b"credit"})
        members.update(changes)
        return {name: data for name, data in members.items() if data is not None}

    def test_exact_files_are_extracted_and_verified(self):
        archive, runtime = self.archive(self.members())
        extracted = runtimes.extract_daniel(archive, runtime, self.root / "out")
        self.assertEqual(sorted(path.name for path in extracted.iterdir()),
                         sorted([*runtimes.DANIEL_FILES, "danielblnc_ATTRIBUTION.txt"]))

    def test_unexpected_missing_traversal_and_changed_members_are_refused(self):
        cases = {
            "extra": self.members(**{"setup.exe": b"never extracted"}),
            "traversal": self.members(**{"../escape.txt": b"never extracted"}),
            "nested": self.members(**{"sub/dlssnr_amd_pass1.dll": self.pass_data}),
            "missing weights": self.members(**{runtimes.DANIEL_WEIGHTS: None}),
            "changed pass": self.members(**{"dlssnr_amd_pass3.dll": b"different build"}),
        }
        for label, members in cases.items():
            with self.subTest(label):
                archive, runtime = self.archive(members)
                with self.assertRaises(ValueError):
                    runtimes.extract_daniel(archive, runtime, self.root / ("out " + label))
                self.assertFalse((self.root / "escape.txt").exists())
        archive, runtime = self.archive(self.members())
        with self.assertRaisesRegex(ValueError, "checksum"):
            runtimes.extract_daniel(archive, replace(runtime, sha256="0" * 64), self.root / "out checksum")

    def test_fetch_verifies_and_reuses_cache_without_leaving_partials(self):
        source = self.root / "published.bin"
        source.write_bytes(b"published bytes")
        cache = self.root / "cache"
        fetched = runtimes.fetch(source.as_uri(), digest(b"published bytes"), cache, "file.bin", size=15)
        self.assertEqual(fetched.read_bytes(), b"published bytes")
        source.write_bytes(b"replaced upstream")
        self.assertEqual(runtimes.fetch(source.as_uri(), digest(b"published bytes"), cache, "file.bin"), fetched)
        with self.assertRaisesRegex(ValueError, "checksum"):
            runtimes.fetch(source.as_uri(), digest(b"other"), cache, "other.bin")
        with self.assertRaisesRegex(ValueError, "larger"):
            runtimes.fetch(source.as_uri(), digest(b"replaced upstream"), cache, "big.bin", size=4)
        self.assertEqual(sorted(path.name for path in cache.iterdir()), ["file.bin"])


class StageComponentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="amdnr stage components ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.build = self.root / "build"
        for name in ("d3d12.dll", "d3d12core.dll", "x86_64-windows/amdhip64_7.dll",
                     "x86_64-unix/amdhip64_7.so", "x86_64-unix/libamdnr_signal_guard.so"):
            path = self.build / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
        self.compiler = self.root / "compiler.dll"
        self.compiler.write_bytes(b"MZ fixture")
        self.runtime = replace(runtimes.DANIEL_RUNTIMES[0], pass_sha256=digest(b"pass"))
        self.files = self.root / "daniel"
        self.files.mkdir()
        for name in runtimes.DANIEL_PASSES:
            (self.files / name).write_bytes(b"pass")
        (self.files / runtimes.DANIEL_WEIGHTS).write_bytes(b"weights")
        (self.files / "danielblnc_ATTRIBUTION.txt").write_text("credit")
        self.plugin = self.root / "OptiPatcher.asi"
        self.plugin.write_bytes(b"asi")

    def stage(self, host, **components):
        archive = self.root / f"release-{len(list(self.root.glob('release-*')))}.zip"
        with zipfile.ZipFile(archive, "w") as release:
            release.writestr("OptiScaler.ini", "; comment\n[DlssNr]\nEnabled=false\nNrBackend=lmxxf\n"
                                               "[Plugins]\nLoadAsiPlugins=auto\n")
            release.writestr("OptiScaler.dll", host)
            for name in ("LmxxfNrRuntime.dll", "LmxxfNrRuntime.pak"):
                release.writestr(name, name)
        staged = self.root / f"staged-{archive.stem}"
        with patch.dict(manage.RELEASES, {manage.sha(archive): "test"}):
            manage.stage(archive, self.compiler, staged, self.build, None, **components)
        return staged

    def test_runtime_and_plugin_are_staged_with_matching_settings(self):
        staged = self.stage(b"MZ host " + bytes.fromhex(self.runtime.pass_sha256),
                            daniel=(self.runtime, self.files),
                            optipatcher=(runtimes.OPTIPATCHER[0], self.plugin))
        for name in (*runtimes.DANIEL_FILES, "danielblnc_ATTRIBUTION.txt"):
            self.assertTrue((staged / name).is_file(), name)
        self.assertEqual((staged / runtimes.OPTIPATCHER_PATH).read_bytes(), b"asi")
        ini = (staged / "OptiScaler.ini").read_text()
        self.assertIn("NrBackend=daniel", ini)
        self.assertIn("LoadAsiPlugins=true", ini)
        self.assertIn("; comment", ini)
        components = json.loads((staged / "amdnr-linux/components.json").read_text())
        self.assertEqual(components["neural_runtime"], "daniel")
        self.assertEqual(components["daniel"]["pass_sha256"], self.runtime.pass_sha256)
        self.assertEqual(components["optipatcher"]["version"], runtimes.OPTIPATCHER[0].version)
        source = (staged / "amdnr-linux/SOURCE.txt").read_text()
        self.assertIn("Daniel Blanco", source)
        self.assertIn("OptiPatcher", source)

    def test_default_package_records_builtin_runtime_only(self):
        staged = self.stage(b"MZ host")
        components = json.loads((staged / "amdnr-linux/components.json").read_text())
        self.assertEqual(components, {"neural_runtime": "lmxxf", "daniel": None, "optipatcher": None})
        self.assertFalse(any((staged / name).exists() for name in runtimes.DANIEL_FILES))
        self.assertRegex((staged / "OptiScaler.ini").read_text(), r"(?m)^LoadAsiPlugins=auto$")

    def test_host_that_does_not_pin_the_runtime_is_refused(self):
        with self.assertRaisesRegex(ValueError, "does not drive"):
            self.stage(b"MZ host without digests", daniel=(self.runtime, self.files))
        self.assertFalse(any(re.match(r"staged-", path.name) for path in self.root.iterdir()))

    def test_model_e_is_staged_only_for_a_host_that_reads_its_receipt(self):
        folder = self.root / "model-e"
        folder.mkdir()
        (folder / "amd_fidelityfx_dx12.dll").write_bytes(built_dll())
        details = {"dlss_version": "310.7.0"}
        staged = self.stage(b"MZ host " + "amdnr-helix-e.ini".encode("utf-16-le"), model_e=(folder, details))
        provider = staged / runtimes.MODEL_E_FOLDER
        self.assertEqual(sorted(path.name for path in provider.iterdir()),
                         sorted([*runtimes.MODEL_E_DLLS, runtimes.MODEL_E_RECEIPT]))
        ini = (staged / "OptiScaler.ini").read_text()
        self.assertRegex(ini, r"(?m)^\[Upscalers\]\nFfxSRProvider=helix_e$")
        self.assertRegex(ini, r"(?m)^\[Libraries\]\nHelixSRPath=AMDNR-SR-E$")
        components = json.loads((staged / "amdnr-linux/components.json").read_text())
        self.assertEqual(components["model_e"]["sha256"], digest(built_dll()))
        self.assertEqual(components["neural_runtime"], "lmxxf")
        self.assertIn("do not share or upload", (staged / "amdnr-linux/SOURCE.txt").read_text())
        with self.assertRaisesRegex(ValueError, "cannot load Model E"):
            self.stage(b"MZ older host", model_e=(folder, details))

    def stage_split(self, lmxxf_members, give=True, pinned=None):
        """Stage a release laid out like 0.3.5.8R2: lmxxf is a second archive on the release."""
        count = len(list(self.root.glob("split-*.zip")))
        archive, lmxxf = self.root / f"split-{count}.zip", self.root / f"split-{count}-lmxxf.zip"
        with zipfile.ZipFile(archive, "w") as release:
            release.writestr("OptiScaler.ini", "[DlssNr]\nEnabled=false\n")
            release.writestr("OptiScaler.dll", b"MZ host")
            release.writestr("OptiScaler\\libxess.dll", b"xess")
            release.writestr("Licenses/AMDNR_NOTICE.txt", "main notice")
            release.writestr("README.md", "docs")
        with zipfile.ZipFile(lmxxf, "w") as component:
            for name, data in lmxxf_members.items():
                component.writestr(name, data)
        separate = runtimes.Download(lmxxf.name, "test", "https://example.invalid/" + lmxxf.name,
                                     pinned or manage.sha(lmxxf), lmxxf.stat().st_size)
        staged = self.root / f"staged-{archive.stem}"
        with patch.dict(manage.RELEASES, {manage.sha(archive): "test"}), \
                patch.dict(manage.LMXXF_ARCHIVES, {"test": separate}):
            manage.stage(archive, self.compiler, staged, self.build, None, lmxxf=lmxxf if give else None)
        return staged

    def lmxxf_members(self, **changes):
        members = {"LmxxfNrRuntime.dll": b"MZ lmxxf", "LmxxfNrRuntime.pak": b"weights",
                   "Licenses/lmxxf_LICENSE.txt": "MIT", "Licenses/AMDNR_NOTICE.txt": "component notice",
                   "README.txt": "component readme", "R2-UPDATE.txt": "notes"}
        members.update(changes)
        return {name: data for name, data in members.items() if data is not None}

    def test_split_release_takes_lmxxf_from_its_separate_archive(self):
        staged = self.stage_split(self.lmxxf_members())
        top = sorted(path.relative_to(staged).as_posix() for path in staged.rglob("*")
                     if path.is_file() and "amdnr-linux" not in path.parts)
        self.assertEqual(top, sorted([
            "winmm.dll", "OptiScaler.ini", "OptiScaler/libxess.dll", "LmxxfNrRuntime.dll", "LmxxfNrRuntime.pak",
            "Licenses/AMDNR_NOTICE.txt", "Licenses/lmxxf_LICENSE.txt", "d3d12.dll", "d3d12core.dll",
            "d3dcompiler_47.dll"]))
        self.assertEqual((staged / "LmxxfNrRuntime.pak").read_bytes(), b"weights")
        self.assertEqual((staged / "Licenses/AMDNR_NOTICE.txt").read_text(), "main notice")
        self.assertRegex((staged / "OptiScaler.ini").read_text(), r"(?m)^NrBackend=lmxxf$")
        components = json.loads((staged / "amdnr-linux/components.json").read_text())
        self.assertEqual(components, {"neural_runtime": "lmxxf", "daniel": None, "optipatcher": None})
        source = (staged / "amdnr-linux/SOURCE.txt").read_text()
        self.assertIn("Host/runtime: AMDNR test, ", source)
        self.assertRegex(source, r"(?m)^lmxxf archive: https://example\.invalid/split-0-lmxxf\.zip, SHA-256 [0-9a-f]{64}$")

    def test_split_release_needs_its_pinned_lmxxf_archive(self):
        cases = ((dict(give=False), "ships lmxxf as a separate archive"),
                 (dict(pinned="0" * 64), "checksum mismatch"),
                 (dict(lmxxf_members=self.lmxxf_members(**{"LmxxfNrRuntime.pak": None})), "does not contain"))
        for options, message in cases:
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                self.stage_split(**{"lmxxf_members": self.lmxxf_members(), **options})
        with self.assertRaisesRegex(ValueError, "has no separate lmxxf archive"):
            self.stage(b"MZ host", lmxxf=self.root / "split-0-lmxxf.zip")
        self.assertFalse(any(re.match(r"staged-", path.name) for path in self.root.iterdir()))

    def stage_alternate(self, members, **components):
        """Stage an archive laid out like the pre-SR release, under a test digest."""
        archive = self.root / f"alternate-{len(list(self.root.glob('alternate-*')))}.zip"
        with zipfile.ZipFile(archive, "w") as release:
            for name, data in members.items():
                release.writestr(name, data)
        build = replace(manage.runtimes.PRESR, sha256=manage.sha(archive))
        staged = self.root / f"staged-{archive.stem}"
        with patch.object(manage.runtimes, "HOST_BUILDS", (build,)):
            manage.stage(archive, self.compiler, staged, self.build, None, **components)
        return staged

    def presr_members(self, **changes):
        members = {
            "OptiScaler.dll": b"MZ pre-SR host " + bytes.fromhex(self.runtime.pass_sha256),
            "OptiScaler.ini": "[DlssNr]\r\nEnabled=false\r\nNrBackend=lmxxf\r\n[Plugins]\r\nLoadAsiPlugins=auto\r\n",
            "OptiScaler\\libxess.dll": b"xess", "OptiScaler\\D3D12_OptiScaler\\D3D12Core.dll": b"agility",
            "Licenses\\OptiScaler_LICENSE.txt": b"GPL", "experimental_lighting\\GatherCS.cso": b"cso",
            "Setup.bat": b"never", "Setup.ps1": b"never", "Uninstall_OptiScaler_NR.bat": b"never",
            "Uninstall_OptiScaler_NR.ps1": b"never", "README.md": b"docs", "SHA256SUMS.txt": b"sums",
            "LmxxfNrRuntime.dll": b"needs unpublished weights", "lmxxf-modules\\deep_fast.hsaco": b"hsaco",
            "shaders\\native_codec_encode.hlsl": b"hlsl",
        }
        members.update(changes)
        return members

    def test_alternate_host_is_staged_without_its_installer_scripts(self):
        staged = self.stage_alternate(self.presr_members(), daniel=(self.runtime, self.files),
                                      optipatcher=(runtimes.OPTIPATCHER[0], self.plugin))
        top = sorted(path.relative_to(staged).as_posix() for path in staged.rglob("*")
                     if path.is_file() and "amdnr-linux" not in path.parts)
        self.assertEqual(top, sorted([
            "winmm.dll", "OptiScaler.ini", "OptiScaler/libxess.dll", "OptiScaler/D3D12_OptiScaler/D3D12Core.dll",
            "Licenses/OptiScaler_LICENSE.txt", "experimental_lighting/GatherCS.cso", runtimes.OPTIPATCHER_PATH,
            "d3d12.dll", "d3d12core.dll", "d3dcompiler_47.dll", "danielblnc_ATTRIBUTION.txt", *runtimes.DANIEL_FILES]))
        ini = (staged / "OptiScaler.ini").read_text()
        self.assertRegex(ini, r"(?m)^NrBackend=daniel$")
        self.assertRegex(ini, r"(?m)^Enabled=true$")
        self.assertRegex(ini, r"(?m)^LoadAsiPlugins=true$")
        components = json.loads((staged / "amdnr-linux/components.json").read_text())
        self.assertEqual(components["neural_runtime"], "daniel")
        self.assertEqual({key: components["host"][key] for key in ("build", "version")},
                         {"build": "presr", "version": runtimes.PRESR.version})
        source = (staged / "amdnr-linux/SOURCE.txt").read_text()
        self.assertIn(f"Host/runtime: {runtimes.PRESR.title} (independent OptiScaler fork by TheAutomatic", source)

    def test_alternate_host_needs_a_runtime_it_drives_and_safe_member_names(self):
        with self.assertRaisesRegex(ValueError, "danielblnc's runtime only"):
            self.stage_alternate(self.presr_members())
        with self.assertRaisesRegex(ValueError, "does not drive"):
            self.stage_alternate(self.presr_members(**{"OptiScaler.dll": b"MZ other build"}),
                                 daniel=(self.runtime, self.files))
        with self.assertRaisesRegex(ValueError, "Unsafe package path"):
            self.stage_alternate(self.presr_members(**{"OptiScaler\\..\\..\\escape.dll": b"x"}),
                                 daniel=(self.runtime, self.files))
        self.assertFalse((self.root / "escape.dll").exists())
        self.assertFalse(any(re.match(r"staged-", path.name) for path in self.root.iterdir()))


def built_dll(prefix=b"MZ helixsr"):
    """A DLL carrying a network the way HelixSR's setup appends it."""
    return prefix + b"\0" * 64 + b"w" * 8 + b"k" * 8 + b"HXSRNET1" + struct.pack("<QQQ", 8, 8, 1)


# Stands in for HelixSR's setup/helixsr_setup.py: appends a network to OUT's DLL.
FAKE_SETUP = """import os, pathlib, struct, sys
args = sys.argv[1:]
out = pathlib.Path(args[0])
dxc, dlss = args[args.index("--dxc") + 1], pathlib.Path(args[args.index("--dlss") + 1])
assert "--yes" in args and os.access(dxc, os.X_OK) and dlss.read_bytes() == b"nvidia dll"
assert not any(key.startswith("PYTHON") for key in os.environ if key != "PYTHONDONTWRITEBYTECODE")
dll = out / "amd_fidelityfx_dx12.dll"
dll.write_bytes(dll.read_bytes() + b"w" * 8 + b"k" * 8 + b"HXSRNET1" + struct.pack("<QQQ", 8, 8, 1))
(out / "helixsr_setup.json").write_text('{"shaders": 60}')
print("[helixsr-setup] building the network", flush=True)
print("[helixsr-setup] drop-in DLLs for FSR 3.1 games: " + str(out / "fsr3-drop-in"), flush=True)
"""


class ModelETests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="amdnr model e ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def publish(self, name, members):
        """A zip served from a file URL, and its pin."""
        path = self.root / "published" / name
        path.parent.mkdir(exist_ok=True)
        with zipfile.ZipFile(path, "w") as bundle:
            for member, data in members.items():
                bundle.writestr(member, data)
        return path

    def pins(self, release_members=None):
        root = f"HelixSR-{runtimes.HELIXSR.version}/"
        release = self.publish("HelixSR.zip", release_members or {
            root + "LICENSE": "HelixSR Freeware License", root + "THIRD_PARTY_NOTICES.md": "notices",
            root + "amd_fidelityfx_dx12.dll": b"MZ helixsr" + b"\0" * 64,
            root + "setup/helixsr_setup.py": FAKE_SETUP})
        dxc = self.publish("dxc.zip", {f"bin\\x64\\{name}": name for name in ("dxc.exe", "dxcompiler.dll", "dxil.dll")}
                           | {"bin\\x64\\dxv.exe": "not wanted"})
        dlss = self.root / "published" / "nvngx_dlss.dll"
        dlss.write_bytes(b"nvidia dll")
        def pin(item, path):
            data = path.read_bytes()
            return replace(item, url=path.as_uri(), sha256=digest(data), size=len(data))
        for name, item, path in (("HELIXSR", runtimes.HELIXSR, release), ("DXC_FOR_HELIXSR", runtimes.DXC_FOR_HELIXSR, dxc),
                                 ("DLSS_FOR_HELIXSR", runtimes.DLSS_FOR_HELIXSR, dlss)):
            replacement = patch.object(runtimes, name, pin(item, path))
            replacement.start()
            self.addCleanup(replacement.stop)
        wine = self.root / "wine"
        wine.write_text("#!/bin/sh\nexit 0\n")
        wine.chmod(0o755)
        return wine, dlss

    def test_receipt_is_exactly_what_the_host_parses(self):
        main, sr = "a" * 64, "b" * 64
        self.assertEqual(runtimes.model_e_receipt(main, sr),
                         f"[HelixSR]\nSchema=1\nProvider=HelixSR\nModel=E\nMainSHA256={main}\nSR_SHA256={sr}\n")
        with self.assertRaises(ValueError):
            runtimes.model_e_receipt("A" * 64, sr)

    def test_built_network_footer_and_host_support_are_read_from_files(self):
        dll = self.root / "amd_fidelityfx_dx12.dll"
        dll.write_bytes(built_dll())
        self.assertTrue(runtimes.helixsr_built(dll))
        dll.write_bytes(b"MZ helixsr" + b"\0" * 64)
        self.assertFalse(runtimes.helixsr_built(dll))
        dll.write_bytes(built_dll(b"PE"))
        self.assertFalse(runtimes.helixsr_built(dll))
        self.assertFalse(runtimes.helixsr_built(self.root / "missing.dll"))
        host = self.root / "OptiScaler.dll"
        host.write_bytes(b"MZ" + b"\0" * (1024 * 1024 - 20) + "amdnr-helix-e.ini".encode("utf-16-le"))
        self.assertTrue(runtimes.host_loads_model_e(host))
        host.write_bytes(b"MZ older host")
        self.assertFalse(runtimes.host_loads_model_e(host))

    def built_folder(self):
        folder = self.root / "built"
        folder.mkdir()
        (folder / "amd_fidelityfx_dx12.dll").write_bytes(built_dll())
        (folder / "LICENSE").write_text("HelixSR Freeware License")
        (folder / "build.json").write_text(json.dumps({"helixsr_sha256": runtimes.HELIXSR.sha256,
                                                       "sha256": digest(built_dll()), "dlss_version": "310.7.0"}))
        return folder

    def test_staged_layout_carries_two_copies_and_a_matching_receipt(self):
        folder = self.built_folder()
        self.assertIsNotNone(runtimes.load_model_e(folder))
        package = self.root / "package"
        package.mkdir()
        self.assertEqual(runtimes.stage_model_e(folder, package), digest(built_dll()))
        staged = package / runtimes.MODEL_E_FOLDER
        for name in runtimes.MODEL_E_DLLS:
            self.assertEqual((staged / name).read_bytes(), built_dll())
        self.assertEqual((staged / runtimes.MODEL_E_RECEIPT).read_text(),
                         runtimes.model_e_receipt(digest(built_dll()), digest(built_dll())))
        self.assertEqual((staged / "HelixSR_LICENSE").read_text(), "HelixSR Freeware License")
        (folder / "amd_fidelityfx_dx12.dll").write_bytes(built_dll(b"MZ changed"))
        self.assertIsNone(runtimes.load_model_e(folder))

    def test_build_needs_consent_or_a_local_dll_before_any_download(self):
        wine, dlss = self.pins()
        cache = self.root / "cache"
        with self.assertRaises(runtimes.ModelEConsentNeeded):
            runtimes.build_model_e(cache, wine=wine, python=sys.executable)
        other = self.root / "other_dlss.dll"
        other.write_bytes(b"another DLSS version")
        with self.assertRaisesRegex(ValueError, "different file"):
            runtimes.build_model_e(cache, dlss=other, wine=wine, python=sys.executable)
        with patch.object(runtimes, "find_numpy_python", return_value=None), \
                self.assertRaisesRegex(runtimes.ModelEUnavailable, "numpy"):
            runtimes.build_model_e(cache, dlss=dlss, wine=wine)
        with patch.object(runtimes, "find_wine", return_value=None), \
                self.assertRaisesRegex(runtimes.ModelEUnavailable, "Wine"):
            runtimes.build_model_e(cache, dlss=dlss, python=sys.executable)
        self.assertFalse(cache.exists())

    def test_build_runs_helixsr_setup_once_and_keeps_only_the_result(self):
        wine, _ = self.pins()
        cache = self.root / "cache"
        messages = []
        with patch.dict(os.environ, {"PYTHONPATH": "/packaged/python"}):
            folder, details = runtimes.build_model_e(cache, accept_dlss_license=True, wine=wine,
                                                     python=sys.executable, progress=messages.append)
        self.assertEqual(folder, runtimes.model_e_folder(cache))
        self.assertEqual(sorted(path.name for path in folder.iterdir()),
                         ["LICENSE", "THIRD_PARTY_NOTICES.md", "amd_fidelityfx_dx12.dll", "build.json"])
        self.assertTrue(runtimes.helixsr_built(folder / "amd_fidelityfx_dx12.dll"))
        self.assertEqual(details["helixsr_setup"], {"shaders": 60})
        self.assertIn("Model E: building the network", messages)
        self.assertFalse([message for message in messages if "drop-in" in message])
        # NVIDIA's DLL, the extracted release and the compiler are gone; the archives stay cached.
        self.assertEqual(sorted(path.name for path in (cache / "model-e").iterdir()), [folder.name])
        self.assertFalse(list(cache.rglob("nvngx_dlss.dll")))
        with patch.object(runtimes, "_run_setup", side_effect=AssertionError("rebuilt")):
            self.assertEqual(runtimes.build_model_e(cache)[0], folder)

    def test_failed_setup_leaves_no_partial_build(self):
        root = f"HelixSR-{runtimes.HELIXSR.version}/"
        wine, dlss = self.pins({root + "LICENSE": "l", root + "THIRD_PARTY_NOTICES.md": "n",
                                root + "amd_fidelityfx_dx12.dll": b"MZ helixsr",
                                root + "setup/helixsr_setup.py": "print('[helixsr-setup] no'); raise SystemExit(3)"})
        cache = self.root / "cache"
        with self.assertRaisesRegex(ValueError, "could not build Model E"):
            runtimes.build_model_e(cache, dlss=dlss, wine=wine, python=sys.executable, progress=lambda message: None)
        self.assertEqual(list((cache / "model-e").iterdir()), [])
        self.assertIsNone(runtimes.model_e_build(cache))

    def test_release_members_outside_its_folder_are_refused(self):
        root = f"HelixSR-{runtimes.HELIXSR.version}/"
        for index, bad in enumerate((root + "../escape.py", "other/setup.py", "/absolute.py", root + "setup\\helixsr_setup.py")):
            with self.subTest(member=bad):
                archive = self.publish("bad.zip", {root + "LICENSE": "l", bad: "x"})
                with patch.object(runtimes, "HELIXSR", replace(runtimes.HELIXSR, sha256=digest(archive.read_bytes()))), \
                        self.assertRaisesRegex(ValueError, "unexpected member"):
                    runtimes._extract_helixsr(archive, self.root / f"out{index}")
                self.assertFalse((self.root / f"out{index}").exists())
        self.assertFalse((self.root / "escape.py").exists())

    def test_compiler_wrapper_passes_linux_paths_as_wine_paths(self):
        record = self.root / "arguments"
        wine = self.root / "fake wine"
        wine.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$WINEPREFIX\" \"$@\" > '{record}'\n")
        wine.chmod(0o755)
        wrapper = self.root / "dxc.sh"
        runtimes._dxc_wrapper(wrapper, wine, self.root / "dxc dir/dxc.exe", self.root / "pfx")
        subprocess.run([str(wrapper), "-T", "cs_6_6", "/work/a b/k.hlsl", "-Fo", "/work/k.cso"], check=True)
        self.assertEqual(record.read_text().splitlines(),
                         [str(self.root / "pfx"), str(self.root / "dxc dir/dxc.exe"), "-T", "cs_6_6",
                          "Z:\\work\\a b\\k.hlsl", "-Fo", "Z:\\work\\k.cso"])

    def test_wine_is_found_in_steam_proton_before_the_system(self):
        proton = self.root / "Steam/compatibilitytools.d/GE-Proton/files/bin/wine"
        proton.parent.mkdir(parents=True)
        proton.write_text("#!/bin/sh\n")
        proton.chmod(0o755)
        self.assertEqual(runtimes.find_wine([self.root / "Steam"]), proton)
        with patch.object(runtimes.shutil, "which", return_value=None):
            self.assertIsNone(runtimes.find_wine([self.root / "none"]))


if __name__ == "__main__":
    unittest.main()
