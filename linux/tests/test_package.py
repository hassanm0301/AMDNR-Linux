#!/usr/bin/env python3
"""Exercise installation against real temporary files, including failed copies."""
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import unittest
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

manage = module("manage")
launch = module("launch")

class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="amdnr package test ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = self.root / "Game with spaces"
        self.game.mkdir()
        self.package = self.root / "package"
        self.bundle = self.package / "amdnr-linux"
        self.bundle.mkdir(parents=True)
        (self.bundle / "run.sh").write_text("launcher")
        (self.bundle / "x86_64-windows").mkdir()
        (self.bundle / "x86_64-windows/amdhip64_7.dll").write_bytes(b"test placeholder")
        (self.bundle / "x86_64-unix").mkdir()
        (self.bundle / "x86_64-unix/libamdnr_signal_guard.so").write_bytes(b"signal guard")
        (self.package / "winmm.dll").write_bytes(b"replacement")
        (self.package / "d3dcompiler_47.dll").write_bytes(b"native compiler")
        (self.game / "winmm.dll").write_bytes(b"original mod")

    def test_install_restore_and_preserve_unrelated_files(self):
        manage.install(self.package, self.game)
        self.assertEqual((self.game / "winmm.dll").read_bytes(), b"replacement")
        (self.game / "unrelated.save").write_bytes(b"save")
        manage.uninstall(self.game)
        self.assertEqual((self.game / "winmm.dll").read_bytes(), b"original mod")
        self.assertEqual((self.game / "unrelated.save").read_bytes(), b"save")
        self.assertFalse((self.game / manage.BACKUP).exists())

    def test_modified_file_requires_explicit_restore(self):
        manage.install(self.package, self.game)
        (self.game / "winmm.dll").write_bytes(b"later user mod")
        with self.assertRaisesRegex(ValueError, "Modified since install"):
            manage.uninstall(self.game)
        self.assertEqual((self.game / "winmm.dll").read_bytes(), b"later user mod")
        manage.uninstall(self.game, force=True)
        self.assertEqual((self.game / "winmm.dll").read_bytes(), b"original mod")

    def test_failed_install_restores_original(self):
        original_copy = manage.shutil.copy2
        def copy(source, destination, *args, **kwargs):
            if Path(source) == self.package / "winmm.dll":
                Path(destination).write_bytes(b"interrupted copy")
                raise OSError("simulated disk failure")
            return original_copy(source, destination, *args, **kwargs)
        with patch.object(manage.shutil, "copy2", side_effect=copy):
            with self.assertRaisesRegex(OSError, "simulated disk failure"):
                manage.install(self.package, self.game)
        self.assertEqual((self.game / "winmm.dll").read_bytes(), b"original mod")
        self.assertEqual(set(p.name for p in self.game.iterdir()), {"winmm.dll"})

    def test_symlink_and_reserved_paths_rejected_before_changes(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.game / "amdnr-linux").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            manage.install(self.package, self.game)
        self.assertFalse(list(outside.iterdir()))
        (self.game / "amdnr-linux").unlink()
        (self.package / manage.RECORD).write_text("bad record")
        with self.assertRaisesRegex(ValueError, "reserved"):
            manage.install(self.package, self.game)
        self.assertFalse((self.game / manage.BACKUP).exists())

    def test_prefix_is_idempotent_and_removed_with_install(self):
        manage.install(self.package, self.game)
        bundle = self.game / "amdnr-linux"
        prefix = self.root / "test prefix"
        launch.prepare_prefix(bundle, prefix)
        launch.prepare_prefix(bundle, prefix)
        dll = prefix / "drive_c/windows/system32/amdhip64_7.dll"
        self.assertTrue(dll.exists())
        manage.uninstall(self.game)
        self.assertFalse(dll.exists())

    def test_manually_staged_prefix_state_is_not_adopted(self):
        bundle = self.game / "amdnr-linux"
        shutil.copytree(self.bundle, bundle)
        shutil.copy2(self.package / "d3dcompiler_47.dll", self.game / "d3dcompiler_47.dll")
        prefix = self.root / "existing prefix"
        compiler = prefix / "drive_c/windows/system32/d3dcompiler_47.dll"
        compiler.parent.mkdir(parents=True)
        compiler.write_bytes(b"original Wine compiler")
        launch.prepare_prefix(bundle, prefix)
        journal = (bundle / "prefixes.json").read_bytes()
        backups = {path.name: path.read_bytes() for path in (bundle / "prefix-backups").iterdir()}

        with self.assertRaisesRegex(ValueError, "Existing bundle prefix state"):
            manage.install(self.package, self.game)

        self.assertEqual((bundle / "prefixes.json").read_bytes(), journal)
        self.assertEqual({path.name: path.read_bytes() for path in (bundle / "prefix-backups").iterdir()}, backups)
        self.assertEqual(compiler.read_bytes(), b"native compiler")
        self.assertTrue(compiler.with_name("amdhip64_7.dll").is_file())
        self.assertEqual((self.game / "winmm.dll").read_bytes(), b"original mod")
        self.assertFalse((self.game / manage.RECORD).exists())
        self.assertFalse((self.game / manage.BACKUP).exists())

    def test_orphaned_bundle_prefix_state_is_rejected(self):
        bundle = self.game / "amdnr-linux"
        bundle.mkdir()
        for name in ("prefixes.json.tmp", "prefix-backups", "prefixes.lock"):
            with self.subTest(name=name):
                state = bundle / name
                if name == "prefix-backups":
                    state.mkdir()
                else:
                    state.write_text("existing state")
                with self.assertRaisesRegex(ValueError, "Existing bundle prefix state"):
                    manage.install(self.package, self.game)
                self.assertTrue(state.exists())
                self.assertFalse((self.game / manage.RECORD).exists())
                self.assertFalse((self.game / manage.BACKUP).exists())
                if state.is_dir():
                    state.rmdir()
                else:
                    state.unlink()

    def test_existing_prefix_dll_is_not_overwritten(self):
        prefix = self.root / "test prefix"
        dll = prefix / "drive_c/windows/system32/amdhip64_7.dll"
        dll.parent.mkdir(parents=True)
        dll.write_bytes(b"unrelated Windows runtime")
        with self.assertRaisesRegex(ValueError, "different HIP DLL"):
            launch.prepare_prefix(self.bundle, prefix)
        self.assertEqual(dll.read_bytes(), b"unrelated Windows runtime")

    def test_prefix_compiler_restored_including_symlinks(self):
        prefix = self.root / "prefix"
        dll = prefix / "drive_c/windows/system32/d3dcompiler_47.dll"
        dll.parent.mkdir(parents=True)
        dll.write_bytes(b"Wine compiler")
        launch.prepare_prefix(self.bundle, prefix)
        self.assertEqual(dll.read_bytes(), b"native compiler")
        launch.restore_prefixes(self.bundle)
        self.assertEqual(dll.read_bytes(), b"Wine compiler")
        dll.unlink()
        outside = self.root / "Wine compiler.dll"
        outside.write_bytes(b"shared compiler")
        dll.symlink_to(outside)
        launch.prepare_prefix(self.bundle, prefix)
        self.assertFalse(dll.is_symlink())
        self.assertEqual(outside.read_bytes(), b"shared compiler")
        launch.restore_prefixes(self.bundle)
        self.assertEqual(dll.readlink(), outside)

    def test_environment_preserves_other_overrides(self):
        rocm = self.bundle / "rocm/lib"
        rocm.mkdir(parents=True)
        (rocm / "libamdhip64.so.7").write_bytes(b"fixture")
        before = {"WINEDLLOVERRIDES": "version=n", "LD_LIBRARY_PATH": "/existing", "LD_PRELOAD": "overlay.so"}
        after = launch.environment(self.bundle, before)
        self.assertEqual(before["WINEDLLOVERRIDES"], "version=n")
        self.assertTrue(after["WINEDLLOVERRIDES"].startswith("version=n;"))
        self.assertEqual(after["LD_LIBRARY_PATH"], str(rocm) + ":" + str(self.bundle / "x86_64-unix") + ":/existing")
        self.assertEqual(after["LD_PRELOAD"], "libamdnr_signal_guard.so:overlay.so")
        self.assertEqual(after["AMDNR_HIP_LIBRARY"], str(rocm / "libamdhip64.so.7"))

if __name__ == "__main__":
    unittest.main()
