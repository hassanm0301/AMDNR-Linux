"""Optional component catalog, verification and staging, on synthetic files only."""
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import re
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


if __name__ == "__main__":
    unittest.main()
