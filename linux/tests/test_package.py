#!/usr/bin/env python3
"""Exercise installation against real temporary files, including failed copies."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import sys
import zipfile
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
        (self.package / "d3d12.dll").write_bytes(b"graphics replacement")
        shutil.copy2(ROOT / "proxies.py", self.bundle / "proxies.py")
        (self.package / "d3dcompiler_47.dll").write_bytes(b"native compiler")
        (self.game / "d3d12.dll").write_bytes(b"original mod")

    def test_install_restore_and_preserve_unrelated_files(self):
        manage.install(self.package, self.game)
        self.assertEqual((self.game / "winmm.dll").read_bytes(), b"replacement")
        (self.game / "unrelated.save").write_bytes(b"save")
        manage.uninstall(self.game)
        self.assertEqual((self.game / "d3d12.dll").read_bytes(), b"original mod")
        self.assertFalse((self.game / "winmm.dll").exists())
        self.assertEqual((self.game / "unrelated.save").read_bytes(), b"save")
        self.assertFalse((self.game / manage.BACKUP).exists())

    def test_stage_uses_pe_unixlib_without_legacy_elf_builtin(self):
        build = self.root / "build"
        for name in ("d3d12.dll", "d3d12core.dll", "x86_64-windows/amdhip64_7.dll",
                     "x86_64-unix/amdhip64_7.so", "x86_64-unix/libamdnr_signal_guard.so",
                     "x86_64-unix/amdhip64_7.dll.so"):
            path = build / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
        compiler = self.root / "compiler.dll"
        compiler.write_bytes(b"MZ fixture")
        archive = self.root / "release.zip"
        with zipfile.ZipFile(archive, "w") as release:
            release.writestr("OptiScaler.ini", "[DlssNr]\nEnabled=false\nNrBackend=lmxxf\n")
            for name in ("OptiScaler.dll", "LmxxfNrRuntime.dll", "LmxxfNrRuntime.pak"):
                release.writestr(name, name)
        staged = self.root / "staged"
        with patch.dict(manage.RELEASES, {manage.sha(archive): "test"}):
            manage.stage(archive, compiler, staged, build, None)
        unix = staged / "amdnr-linux/x86_64-unix"
        self.assertEqual((unix / "amdhip64_7.so").read_bytes(), b"x86_64-unix/amdhip64_7.so")
        self.assertFalse((unix / "amdhip64_7.dll.so").exists())
        self.assertTrue((staged / "amdnr-linux/x86_64-windows/amdhip64_7.dll").is_file())
        self.assertTrue((staged / "amdnr-linux/proxies.py").is_file())
        self.assertEqual(json.loads((staged / "amdnr-linux/proxy.json").read_text()), {"proxy": "winmm.dll"})

    def test_automatic_proxy_skips_case_insensitive_foreign_mod(self):
        (self.game / "WINMM.DLL").write_bytes(b"another mod")
        manage.install(self.package, self.game)
        self.assertEqual((self.game / "WINMM.DLL").read_bytes(), b"another mod")
        self.assertFalse((self.game / "winmm.dll").exists())
        self.assertEqual((self.game / "version.dll").read_bytes(), b"replacement")
        record = json.loads((self.game / manage.RECORD).read_text())
        self.assertEqual(record["launcher"]["proxy_name"], "version.dll")
        self.assertEqual(json.loads((self.game / "amdnr-linux/proxy.json").read_text()), {"proxy": "version.dll"})
        manage.uninstall(self.game)
        self.assertEqual((self.game / "WINMM.DLL").read_bytes(), b"another mod")
        self.assertFalse((self.game / "version.dll").exists())

    def test_manual_proxy_selection_and_collision_rejection(self):
        (self.game / "VERSION.DLL").write_bytes(b"another mod")
        with self.assertRaises(ValueError):
            manage.install(self.package, self.game, proxy="version.dll")
        self.assertFalse((self.game / manage.RECORD).exists())
        manage.install(self.package, self.game, proxy="dbghelp.dll")
        self.assertEqual((self.game / "dbghelp.dll").read_bytes(), b"replacement")
        self.assertFalse((self.game / "winmm.dll").exists())
        manage.uninstall(self.game)
        self.assertEqual((self.game / "VERSION.DLL").read_bytes(), b"another mod")

    def test_extra_proxy_in_package_is_rejected_before_backups(self):
        for name in ("version.dll", "Winmm.dll"):
            with self.subTest(name=name):
                (self.package / name).write_bytes(b"second host")
                for proxy in ("winmm.dll", "version.dll"):
                    with self.assertRaisesRegex(ValueError, "extra proxy"):
                        manage.install(self.package, self.game, proxy=proxy)
                self.assertEqual(set(path.name for path in self.game.iterdir()), {"d3d12.dll"})
                self.assertEqual((self.game / "d3d12.dll").read_bytes(), b"original mod")
                (self.package / name).unlink()

    def test_cli_install_switch_and_uninstall(self):
        for name in ("OptiScaler.ini", "d3d12core.dll", "amdnr-linux/x86_64-unix/amdhip64_7.so"):
            (self.package / name).write_bytes(b"fixture")
        shutil.copy2(ROOT / "launch.py", self.bundle / "launch.py")
        (self.bundle / "run.sh").chmod(0o755)
        env = dict(os.environ, XDG_DATA_HOME=str(self.root / "data"), XDG_CACHE_HOME=str(self.root / "cache"))

        def command(*args):
            result = subprocess.run([sys.executable, str(ROOT / "manage.py"), *map(str, args)],
                                    env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        command("install", self.package, self.game, "--proxy", "version.dll")
        self.assertTrue((self.game / "version.dll").is_file())
        command("switch-proxy", self.game, "--proxy", "dbghelp.dll")
        self.assertFalse((self.game / "version.dll").exists())
        self.assertEqual((self.game / "dbghelp.dll").read_bytes(), b"replacement")
        command("switch-proxy", self.game, "--proxy", "next")
        self.assertEqual(json.loads((self.game / "amdnr-linux/proxy.json").read_text()), {"proxy": "winmm.dll"})
        command("uninstall", self.game)
        self.assertEqual(set(path.name for path in self.game.iterdir()), {"d3d12.dll"})
        self.assertEqual((self.game / "d3d12.dll").read_bytes(), b"original mod")

    def test_cli_clean_lists_asks_and_removes_leftovers_of_a_lost_install(self):
        (self.package / "winmm.dll").write_bytes(b"MZ" + "OptiScaler.ini".encode("utf-16-le") + "AMDNR".encode("utf-16-le"))
        manage.install(self.package, self.game)
        # A mod manager keeps the log in its own folder and links it back.
        log = self.root / "mod manager/OptiScaler.log"
        log.parent.mkdir()
        log.write_bytes(b"[W] AMDNR session start\n")
        (self.game / "OptiScaler.log").symlink_to(log)
        (self.game / "unrelated.save").write_bytes(b"save")
        (self.game / manage.RECORD).unlink()
        env = dict(os.environ, XDG_DATA_HOME=str(self.root / "data"), XDG_CACHE_HOME=str(self.root / "cache"))

        def command(*args, answer=""):
            result = subprocess.run([sys.executable, str(ROOT / "manage.py"), "clean", str(self.game), *args],
                                    env=env, input=answer, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout

        output = command(answer="n\n")
        for line in ("Remove: OptiScaler.log", "Remove: winmm.dll", "Remove: amdnr-linux/run.sh",
                     "Remove: d3dcompiler_47.dll", "Put back original: d3d12.dll", "Nothing was changed.",
                     f"Delete linked file: {log} (the link OptiScaler.log points to it)"):
            self.assertIn(line, output)
        self.assertTrue((self.game / "winmm.dll").exists())
        self.assertTrue(log.exists())
        self.assertIn("Leftover AMDNR files removed. Original game files were put back.", command("--yes"))
        self.assertEqual(sorted(path.name for path in self.game.iterdir()), ["d3d12.dll", "unrelated.save"])
        self.assertEqual((self.game / "d3d12.dll").read_bytes(), b"original mod")
        self.assertEqual(list(log.parent.iterdir()), [])
        self.assertIn("No leftover AMDNR files were found.", command())

    def test_modified_file_requires_explicit_restore(self):
        manage.install(self.package, self.game)
        (self.game / "winmm.dll").write_bytes(b"later user mod")
        with self.assertRaisesRegex(ValueError, "Modified since install"):
            manage.uninstall(self.game)
        self.assertEqual((self.game / "winmm.dll").read_bytes(), b"later user mod")
        manage.uninstall(self.game, force=True)
        self.assertEqual((self.game / "d3d12.dll").read_bytes(), b"original mod")

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
        self.assertEqual((self.game / "d3d12.dll").read_bytes(), b"original mod")
        self.assertEqual(set(p.name for p in self.game.iterdir()), {"d3d12.dll"})

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
        dll = prefix / "drive_c/windows/system32/AMDHIP64_7.DLL"
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
        self.assertTrue(compiler.with_name("AMDHIP64_7.DLL").is_file())
        self.assertEqual((self.game / "d3d12.dll").read_bytes(), b"original mod")
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
        # Wine loads the exact-case name first, so a lowercase one would hide the bridge.
        for name in ("AMDHIP64_7.DLL", "amdhip64_7.dll"):
            with self.subTest(name=name):
                prefix = self.root / ("test prefix " + name)
                dll = prefix / "drive_c/windows/system32" / name
                dll.parent.mkdir(parents=True)
                dll.write_bytes(b"unrelated Windows runtime")
                with self.assertRaisesRegex(ValueError, "different HIP DLL"):
                    launch.prepare_prefix(self.bundle, prefix)
                self.assertEqual(dll.read_bytes(), b"unrelated Windows runtime")
                self.assertEqual([path.name for path in dll.parent.iterdir()], [name])
                self.assertFalse((self.bundle / "prefixes.json").exists())

    def test_hip_bridge_avoids_the_name_proton_cachyos_deletes(self):
        prefix = self.root / "prefix"
        launch.prepare_prefix(self.bundle, prefix)
        system32 = prefix / "drive_c/windows/system32"
        # Proton-CachyOS unlinks exactly this path on every start unless ZLUDA is on.
        self.assertFalse(os.path.lexists(system32 / "amdhip64_7.dll"))
        self.assertEqual((system32 / "AMDHIP64_7.DLL").read_bytes(), b"test placeholder")
        launch.restore_prefixes(self.bundle)
        self.assertEqual(list(system32.iterdir()), [])

    def legacy_prefix(self, prefix, bridge=b"test placeholder"):
        """Prefix state an earlier wrapper left: the bridge journalled under the lowercase name."""
        launch.prepare_prefix(self.bundle, prefix)
        system32 = prefix / "drive_c/windows/system32"
        (system32 / "AMDHIP64_7.DLL").rename(system32 / "amdhip64_7.dll")
        (system32 / "amdhip64_7.dll").write_bytes(bridge)
        record = self.bundle / "prefixes.json"
        entries = json.loads(record.read_text())
        for entry in entries:
            if entry["path"] == str(system32 / "AMDHIP64_7.DLL"):
                entry.update(path=str(system32 / "amdhip64_7.dll"), sha256=launch.sha(system32 / "amdhip64_7.dll"))
        record.write_text(json.dumps(entries))
        return system32

    def test_earlier_lowercase_bridge_moves_to_the_new_name(self):
        for state in ("present", "deleted by Proton", "older bridge"):
            with self.subTest(state=state):
                prefix = self.root / ("prefix " + state)
                system32 = self.legacy_prefix(prefix, b"older bridge" if state == "older bridge" else b"test placeholder")
                if state == "deleted by Proton":
                    (system32 / "amdhip64_7.dll").unlink()
                launch.prepare_prefix(self.bundle, prefix)
                self.assertFalse(os.path.lexists(system32 / "amdhip64_7.dll"))
                self.assertEqual((system32 / "AMDHIP64_7.DLL").read_bytes(), b"test placeholder")
                entries = json.loads((self.bundle / "prefixes.json").read_text())
                self.assertEqual(sorted(Path(entry["path"]).name for entry in entries),
                                 ["AMDHIP64_7.DLL", "d3dcompiler_47.dll"])
                launch.restore_prefixes(self.bundle)
                self.assertEqual(list(system32.iterdir()), [])

    def test_identical_lowercase_bridge_of_another_install_is_left_alone(self):
        prefix = self.root / "shared prefix"
        other = prefix / "drive_c/windows/system32/amdhip64_7.dll"
        other.parent.mkdir(parents=True)
        other.write_bytes(b"test placeholder")
        launch.prepare_prefix(self.bundle, prefix)
        self.assertTrue(other.with_name("AMDHIP64_7.DLL").is_file())
        launch.restore_prefixes(self.bundle)
        self.assertEqual([path.name for path in other.parent.iterdir()], ["amdhip64_7.dll"])

    def test_earlier_release_bridge_without_its_journal_is_set_aside(self):
        # A game folder moved to the Trash without Uninstall leaves its bridge in the prefix.
        published = b"bridge of an earlier release"
        digest = hashlib.sha256(published).hexdigest()
        for name in ("amdhip64_7.dll", "AMDHIP64_7.DLL"):
            with self.subTest(name=name), patch.dict(launch.RELEASED_HIP, {digest: "v0.2.2-preview"}):
                prefix = self.root / ("orphaned prefix " + name)
                system32 = prefix / "drive_c/windows/system32"
                system32.mkdir(parents=True)
                (system32 / name).write_bytes(published)
                notes = launch.prepare_prefix(self.bundle, prefix)
                aside = system32 / f"{name}.{digest[:16]}.bak"
                self.assertEqual(aside.read_bytes(), published)
                self.assertEqual(sorted(path.name for path in system32.iterdir()),
                                 sorted(["AMDHIP64_7.DLL", "d3dcompiler_47.dll", aside.name]))
                self.assertEqual((system32 / "AMDHIP64_7.DLL").read_bytes(), b"test placeholder")
                self.assertEqual(len(notes), 1)
                self.assertIn("v0.2.2-preview", notes[0])
                self.assertIn(str(aside), notes[0])
                entries = json.loads((self.bundle / "prefixes.json").read_text())
                self.assertEqual(sorted(Path(entry["path"]).name for entry in entries),
                                 ["AMDHIP64_7.DLL", "d3dcompiler_47.dll"])
                launch.restore_prefixes(self.bundle)
                # The earlier bridge was never this installation's: Uninstall leaves it set aside.
                self.assertEqual([path.name for path in system32.iterdir()], [aside.name])

    def test_earlier_bridge_set_aside_again_keeps_one_copy(self):
        # Another game on the prefix still runs a wrapper that copies the lowercase bridge back.
        published = b"bridge of an earlier release"
        digest = hashlib.sha256(published).hexdigest()
        prefix = self.root / "shared prefix"
        system32 = prefix / "drive_c/windows/system32"
        system32.mkdir(parents=True)
        with patch.dict(launch.RELEASED_HIP, {digest: "v0.3.0-preview to v0.3.4-preview"}):
            for _ in range(2):
                (system32 / "amdhip64_7.dll").write_bytes(published)
                launch.prepare_prefix(self.bundle, prefix)
                self.assertFalse(os.path.lexists(system32 / "amdhip64_7.dll"))
            self.assertEqual(sorted(path.name for path in system32.iterdir()),
                             ["AMDHIP64_7.DLL", f"amdhip64_7.dll.{digest[:16]}.bak", "d3dcompiler_47.dll"])
            self.assertEqual(launch.prepare_prefix(self.bundle, prefix), [])

    def test_earlier_bridge_is_not_set_aside_over_a_different_file(self):
        published = b"bridge of an earlier release"
        digest = hashlib.sha256(published).hexdigest()
        prefix = self.root / "prefix"
        system32 = prefix / "drive_c/windows/system32"
        system32.mkdir(parents=True)
        (system32 / "amdhip64_7.dll").write_bytes(published)
        aside = system32 / f"amdhip64_7.dll.{digest[:16]}.bak"
        aside.write_bytes(b"something else")
        with patch.dict(launch.RELEASED_HIP, {digest: "v0.2.2-preview"}):
            with self.assertRaisesRegex(ValueError, "Cannot set aside"):
                launch.prepare_prefix(self.bundle, prefix)
            # A link to an earlier bridge is not a file an earlier wrapper copied.
            aside.unlink()
            (system32 / "amdhip64_7.dll").rename(self.root / "linked bridge.dll")
            (system32 / "amdhip64_7.dll").symlink_to(self.root / "linked bridge.dll")
            with self.assertRaisesRegex(ValueError, "different HIP DLL"):
                launch.prepare_prefix(self.bundle, prefix)
        self.assertEqual([path.name for path in system32.iterdir()], ["amdhip64_7.dll"])
        self.assertFalse((self.bundle / "prefixes.json").exists())

    def test_published_bridges_are_recognised(self):
        # v0.2.2-preview's bridge, which a Resident Evil Requiem prefix kept after its game folder was trashed.
        self.assertEqual(launch.RELEASED_HIP["be0a5019356bce110202f2d611178709f8d7f43ae0637836400a85e97dae165f"],
                         "v0.2.2-preview")
        for digest in launch.RELEASED_HIP:
            self.assertRegex(digest, r"\A[0-9a-f]{64}\Z")

    def test_case_insensitive_folder_keeps_the_bridge_journalled(self):
        prefix = self.root / "casefolded prefix"
        system32 = self.legacy_prefix(prefix)
        # A casefolded folder shows one file under both names; a hard link stands in for that.
        os.link(system32 / "amdhip64_7.dll", system32 / "AMDHIP64_7.DLL")
        launch.prepare_prefix(self.bundle, prefix)
        self.assertEqual((system32 / "AMDHIP64_7.DLL").read_bytes(), b"test placeholder")
        entries = json.loads((self.bundle / "prefixes.json").read_text())
        self.assertEqual(sorted(Path(entry["path"]).name for entry in entries), ["AMDHIP64_7.DLL", "d3dcompiler_47.dll"])
        launch.restore_prefixes(self.bundle)
        self.assertFalse((system32 / "AMDHIP64_7.DLL").exists())

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

    def test_interrupted_prefix_copy_resumes_and_keeps_original_backup(self):
        for original_kind in ('file', 'symlink', 'missing'):
            with self.subTest(original_kind=original_kind):
                prefix = self.root / ('prefix ' + original_kind)
                dll = prefix / 'drive_c/windows/system32/d3dcompiler_47.dll'
                dll.parent.mkdir(parents=True)
                original = self.root / 'Wine compiler.dll'
                original.write_bytes(b'original Wine compiler')
                if original_kind == 'file':
                    dll.write_bytes(original.read_bytes())
                elif original_kind == 'symlink':
                    dll.symlink_to(original)
                copy = launch.shutil.copy2

                def interrupted(source, destination, *args, **kwargs):
                    if Path(source) == self.package / 'd3dcompiler_47.dll':
                        Path(destination).write_bytes(b'partial copy')
                        raise OSError('interrupted prefix copy')
                    return copy(source, destination, *args, **kwargs)

                with patch.object(launch.shutil, 'copy2', side_effect=interrupted):
                    with self.assertRaisesRegex(OSError, 'interrupted prefix copy'):
                        launch.prepare_prefix(self.bundle, prefix)
                if original_kind != 'missing':
                    self.assertEqual(dll.read_bytes(), original.read_bytes())
                launch.prepare_prefix(self.bundle, prefix)
                self.assertEqual(dll.read_bytes(), b'native compiler')
                entries = json.loads((self.bundle / 'prefixes.json').read_text())
                self.assertEqual(len(entries), 2)
                launch.restore_prefixes(self.bundle)
                if original_kind == 'missing':
                    self.assertFalse(dll.exists())
                else:
                    self.assertEqual(dll.read_bytes(), b'original Wine compiler')
                    self.assertEqual(dll.is_symlink(), original_kind == 'symlink')

    def test_missing_owned_prefix_file_is_recreated_but_modified_file_is_preserved(self):
        prefix = self.root / 'prefix'
        launch.prepare_prefix(self.bundle, prefix)
        dll = prefix / 'drive_c/windows/system32/AMDHIP64_7.DLL'
        dll.unlink()
        launch.prepare_prefix(self.bundle, prefix)
        self.assertEqual(dll.read_bytes(), b'test placeholder')
        dll.write_bytes(b'unrelated replacement')
        with self.assertRaisesRegex(ValueError, 'managed prefix file changed'):
            launch.prepare_prefix(self.bundle, prefix)
        self.assertEqual(dll.read_bytes(), b'unrelated replacement')

    def test_interrupted_initial_hip_copy_can_be_retried(self):
        prefix = self.root / 'prefix'
        with patch.object(launch.shutil, 'copy2', side_effect=OSError('copy failed')):
            with self.assertRaisesRegex(OSError, 'copy failed'):
                launch.prepare_prefix(self.bundle, prefix)
        launch.prepare_prefix(self.bundle, prefix)
        dll = prefix / 'drive_c/windows/system32/AMDHIP64_7.DLL'
        self.assertEqual(dll.read_bytes(), b'test placeholder')
        launch.restore_prefixes(self.bundle)
        self.assertFalse(dll.exists())

    def test_invalid_prefix_journal_rejected_before_file_changes(self):
        prefix = self.root / 'prefix'
        record = self.bundle / 'prefixes.json'
        invalid = ({}, [{'path': '/tmp/outside', 'sha256': '0' * 64}],
                   [{'path': str(prefix / 'drive_c/windows/system32/amdhip64_7.dll'), 'sha256': None}])
        for entries in invalid:
            with self.subTest(entries=entries):
                record.write_text(json.dumps(entries))
                with self.assertRaisesRegex(ValueError, 'Invalid prefix ownership'):
                    launch.prepare_prefix(self.bundle, prefix)
                with self.assertRaisesRegex(ValueError, 'Invalid prefix ownership'):
                    launch.restore_prefixes(self.bundle)
                self.assertFalse(prefix.exists())

    def test_prefix_parent_symlink_does_not_redirect_install_or_restore(self):
        prefix = self.root / 'prefix'
        outside = self.root / 'unrelated system32'
        outside.mkdir()
        system32 = prefix / 'drive_c/windows/system32'
        system32.parent.mkdir(parents=True)
        system32.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'parent became a symlink'):
            launch.prepare_prefix(self.bundle, prefix)
        self.assertEqual(list(outside.iterdir()), [])
        system32.unlink()
        launch.prepare_prefix(self.bundle, prefix)
        system32.rename(prefix / 'saved-system32')
        system32.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'parent became a symlink'):
            launch.restore_prefixes(self.bundle, force=True)
        self.assertEqual(list(outside.iterdir()), [])

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

    def test_environment_rejects_legacy_elf_builtin(self):
        legacy = self.bundle / "x86_64-unix/amdhip64_7.dll.so"
        legacy.write_bytes(b"old Wine ELF builtin")
        with self.assertRaisesRegex(ValueError, "Legacy ELF HIP bridge"):
            launch.environment(self.bundle, {})
        self.assertEqual(legacy.read_bytes(), b"old Wine ELF builtin")

if __name__ == "__main__":
    unittest.main()
