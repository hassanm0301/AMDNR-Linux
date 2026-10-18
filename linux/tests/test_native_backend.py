"""Native launcher transactions against isolated files; no games or GPU work."""
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from native_launcher.backend import LauncherBackend, CompilerNeeded, ConflictError, GameRunning


def snapshot(root):
    return {path.relative_to(root).as_posix(): ("link", str(path.readlink())) if path.is_symlink()
            else ("file", path.read_bytes()) for path in root.rglob("*") if path.is_file() or path.is_symlink()}


def compiler_data(marker=b""):
    data = bytearray(512)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3c, 128)
    data[64:64 + len(marker)] = marker
    data[128:132] = b"PE\0\0"
    struct.pack_into("<HH", data, 132, 0x8664, 1)
    struct.pack_into("<HHH", data, 148, 240, 0x2022, 0x20b)
    return bytes(data)


class BackendTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="native backend ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.game = self.root / "Game with spaces"
        self.game.mkdir()
        (self.game / "d3d12.dll").write_bytes(b"original user mod")
        (self.game / "OptiScaler.ini").write_bytes(b"original configuration")
        (self.game / "save.dat").write_bytes(b"save game")
        self.initial = snapshot(self.game)
        self.messages = []
        # Synthetic A/B packages exercise legacy metadata independently of the
        # real release manifest beside an extracted launcher's test suite.
        self.release = self.root / "backend release"
        (self.release / "linux").mkdir(parents=True)
        for name in ("launch.py", "manage.py", "setup.py"):
            shutil.copy2(ROOT / name, self.release / "linux" / name)
        self.backend = LauncherBackend(self.release, self.root / "cache", self.root / "state", self.messages.append)
        running = patch.object(self.backend, "_running", return_value=[])
        running.start()
        self.addCleanup(running.stop)
        self.a = self.package("A")
        self.b = self.package("B")

    def package(self, version):
        package = self.root / ("package " + version)
        names = ("winmm.dll", "OptiScaler.ini", "d3d12.dll", "d3d12core.dll", "d3dcompiler_47.dll",
                 "LmxxfNrRuntime.dll", "LmxxfNrRuntime.pak", "amdnr-linux/run.sh", "amdnr-linux/launch.py", "amdnr-linux/proxies.py",
                 "amdnr-linux/x86_64-unix/amdhip64_7.so", "amdnr-linux/x86_64-windows/amdhip64_7.dll",
                 "amdnr-linux/x86_64-unix/libamdnr_signal_guard.so")
        for name in names:
            path = package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((version + " " + name).encode())
        (package / "amdnr-linux/SOURCE.txt").write_text(f"Host/runtime: AMDNR {version}, fixture\n")
        (package / "amdnr-linux/run.sh").chmod(0o755)
        return package

    def prefix(self):
        prefix = self.root / "isolated prefix"
        original = prefix / "drive_c/windows/system32/d3dcompiler_47.dll"
        original.parent.mkdir(parents=True)
        original.write_bytes(b"original prefix compiler")
        self.backend.launch.prepare_prefix(self.game / "amdnr-linux", prefix)
        return prefix, original

    def test_running_wine_game_detected_with_relative_and_dos_executables(self):
        processes = self.root / 'processes'
        processes.mkdir()
        runner = self.root / 'wine64'
        runner.write_bytes(b'Unix Wine runner')
        (self.game / 'Binaries').mkdir()
        for pid, argument in ((100001, b'Game.exe'), (100002, b'C:\\Games\\Game.exe'),
                              (100003, b'Z:' + os.fsencode(self.game / 'Game.exe')),
                              (100005, os.fsencode(self.game.name + '/Game.exe'))):
            process = processes / str(pid)
            process.mkdir()
            (process / 'exe').symlink_to(runner)
            working_directory = self.root if pid == 100005 else self.game / 'Binaries'
            (process / 'cwd').symlink_to(working_directory, target_is_directory=True)
            (process / 'cmdline').write_bytes(b'wine64\0' + argument + b'\0')
        unrelated = processes / '100004'
        unrelated.mkdir()
        (unrelated / 'exe').symlink_to(runner)
        (unrelated / 'cwd').symlink_to(self.root, target_is_directory=True)
        (unrelated / 'cmdline').write_bytes(b'wine64\0Other.exe\0')
        self.assertEqual(sorted(LauncherBackend._running(self.backend, self.game, processes)),
                         [100001, 100002, 100003, 100005])

    def test_install_update_uninstall_preserve_originals_and_configuration(self):
        result = self.backend.install(self.game, self.a)
        self.assertEqual(result["status"], "installed")
        self.assertEqual(result["version"], "A")
        backup = self.game / self.backend.manage.BACKUP
        originals = snapshot(backup)
        (self.game / "OptiScaler.ini").write_bytes(b"user changed settings")
        self.backend.update(self.game, self.b)
        self.assertEqual((self.game / "OptiScaler.ini").read_bytes(), b"user changed settings")
        self.assertEqual(snapshot(backup), originals)
        self.assertEqual(self.backend.inspect(self.game)["version"], "B")
        self.assertEqual((self.game / "winmm.dll").read_bytes(), b"B winmm.dll")
        self.backend.uninstall(self.game)
        self.assertEqual(snapshot(self.game), self.initial)
        saved = Path(self.backend.diagnostics(self.game)["saved_settings"])
        self.assertEqual(saved.read_bytes(), b"user changed settings")
        self.assertFalse(backup.exists())

    def test_auto_proxy_skips_foreign_names_case_insensitively_and_uninstall_preserves_them(self):
        foreign = self.game / 'WINMM.DLL'
        foreign.write_bytes(b'other mod')
        before = snapshot(self.game)
        result = self.backend.install(self.game, self.a)
        self.assertEqual(result['proxy_name'], 'version.dll')
        self.assertEqual(result['proxy_history'], ['version.dll'])
        self.assertEqual((self.game / 'version.dll').read_bytes(), b'A winmm.dll')
        self.assertFalse((self.game / 'winmm.dll').exists())
        self.assertEqual(json.loads((self.game / 'amdnr-linux/proxy.json').read_text()), {'proxy': 'version.dll'})
        self.assertEqual(next(option for option in result['proxy_options'] if option['name'] == 'winmm.dll')['available'], False)
        self.backend.uninstall(self.game)
        self.assertEqual(snapshot(self.game), before)

    def test_manual_proxy_conflicts_and_reserved_names_are_refused_before_install(self):
        foreign = self.game / 'VERSION.DLL'
        foreign.write_bytes(b'game version helper')
        before = snapshot(self.game)
        for name in ('version.dll', 'dxgi.dll', 'd3d12.dll', 'd3d11.dll', '../version.dll'):
            with self.subTest(name=name), self.assertRaises(ConflictError):
                self.backend.install(self.game, self.a, proxy=name)
            self.assertEqual(snapshot(self.game), before)

    def test_auto_proxy_honors_selected_executable_imports_and_inspection_order(self):
        from test_proxies import pe_image
        executable = self.game / 'ActualGame.exe'
        executable.write_bytes(pe_image(('WINHTTP.DLL', 'VERSION.DLL')))
        (self.game / 'Launcher.exe').write_bytes(pe_image(('winmm.dll',)))
        before = self.backend.inspect(self.game, executable=executable)
        self.assertEqual(before['proxy_options'][0]['name'], 'winhttp.dll')
        result = self.backend.install(self.game, self.a, executable=executable)
        self.assertEqual(result['proxy_name'], 'winhttp.dll')
        self.assertEqual(result['proxy_next'], 'version.dll')

    def test_selected_proxy_survives_repair_and_update_and_preserves_settings(self):
        self.backend.install(self.game, self.a, proxy='dbghelp')
        (self.game / 'OptiScaler.ini').write_bytes(b'user settings')
        (self.game / 'dbghelp.dll').write_bytes(b'damaged host')
        self.backend.repair(self.game, self.a)
        self.assertEqual((self.game / 'dbghelp.dll').read_bytes(), b'A winmm.dll')
        self.backend.update(self.game, self.b)
        self.assertEqual(self.backend.inspect(self.game)['proxy_name'], 'dbghelp.dll')
        self.assertEqual((self.game / 'dbghelp.dll').read_bytes(), b'B winmm.dll')
        self.assertEqual((self.game / 'OptiScaler.ini').read_bytes(), b'user settings')
        self.assertFalse((self.game / 'winmm.dll').exists())

    def test_update_can_change_proxy_without_leaving_an_old_host(self):
        self.backend.install(self.game, self.a, proxy='version.dll')
        result = self.backend.update(self.game, self.b, proxy='wininet.dll')
        self.assertEqual(result['proxy_name'], 'wininet.dll')
        self.assertEqual(result['proxy_history'], ['version.dll', 'wininet.dll'])
        self.assertFalse((self.game / 'version.dll').exists())
        self.assertEqual((self.game / 'wininet.dll').read_bytes(), b'B winmm.dll')
        self.backend.uninstall(self.game)
        self.assertEqual(snapshot(self.game), self.initial)

    def test_switch_reuses_installed_host_without_downloads_and_preserves_unrelated_files(self):
        self.backend.install(self.game, self.a)
        (self.game / 'OptiScaler.ini').write_bytes(b'user settings')
        (self.game / 'notes.txt').write_bytes(b'notes')
        backups = snapshot(self.game / self.backend.manage.BACKUP)
        self.backend.operation_id = 'proxy-switch-token'
        with patch.object(self.backend, 'prepare_package', side_effect=AssertionError('must not prepare')):
            result = self.backend.switch_proxy(self.game)
        self.assertEqual(result['proxy_name'], 'version.dll')
        self.assertEqual(result['proxy_history'], ['winmm.dll', 'version.dll'])
        self.assertEqual(result['proxy_next'], 'dbghelp.dll')
        self.assertFalse((self.game / 'winmm.dll').exists())
        self.assertEqual((self.game / 'version.dll').read_bytes(), b'A winmm.dll')
        self.assertEqual((self.game / 'OptiScaler.ini').read_bytes(), b'user settings')
        self.assertEqual((self.game / 'notes.txt').read_bytes(), b'notes')
        self.assertEqual(snapshot(self.game / self.backend.manage.BACKUP), backups)
        receipt = self.backend.operation_receipt(self.game)
        self.assertEqual(receipt['action'], 'switch_proxy')
        self.assertEqual(receipt['operation_id'], 'proxy-switch-token')
        record = json.loads((self.game / self.backend.manage.RECORD).read_text())
        self.assertEqual(record['launcher']['operation_id'], 'proxy-switch-token')
        self.assertNotIn('winmm.dll', record['installed'])
        self.assertIn('version.dll', record['installed'])

    def test_next_proxy_stops_after_all_available_names_were_tried(self):
        self.backend.install(self.game, self.a)
        for name in self.backend.proxies.SUPPORTED[1:]:
            self.assertEqual(self.backend.switch_proxy(self.game, proxy='NEXT')['proxy_name'], name)
        before = snapshot(self.game)
        self.assertIsNone(self.backend.inspect(self.game)['proxy_next'])
        self.assertTrue(self.backend.inspect(self.game)['proxy_switch_supported'])
        self.assertIsNone(self.backend.inspect(self.game)['proxy_switch_issue'])
        with self.assertRaisesRegex(ConflictError, 'No untried'):
            self.backend.switch_proxy(self.game)
        self.assertEqual(snapshot(self.game), before)
        self.assertEqual(self.backend.switch_proxy(self.game, proxy='winmm.dll')['proxy_name'], 'winmm.dll')
        self.assertEqual(len(self.backend.inspect(self.game)['proxy_history']), len(self.backend.proxies.SUPPORTED))

    def test_switch_refuses_foreign_alias_and_modified_or_missing_host(self):
        self.backend.install(self.game, self.a)
        alias = self.game / 'VERSION.DLL'
        alias.write_bytes(b'foreign proxy')
        before = snapshot(self.game)
        with self.assertRaisesRegex(ConflictError, 'occupied'):
            self.backend.switch_proxy(self.game, proxy='version.dll')
        self.assertEqual(snapshot(self.game), before)
        host = self.game / 'winmm.dll'
        for damaged in (True, False):
            if damaged:
                host.write_bytes(b'user replacement')
            else:
                host.unlink()
            before = snapshot(self.game)
            with self.assertRaisesRegex(ConflictError, 'Repair'):
                self.backend.switch_proxy(self.game)
            self.assertIsNone(self.backend.inspect(self.game)['proxy_next'])
            self.assertEqual(snapshot(self.game), before)

    def test_auto_repair_and_update_do_not_change_alias_when_current_has_case_collision(self):
        self.backend.install(self.game, self.a)
        (self.game / 'WINMM.DLL').write_bytes(b'unrelated casing collision')
        before = snapshot(self.game)
        for action in (self.backend.repair, self.backend.update):
            with self.subTest(action=action.__name__), self.assertRaisesRegex(ConflictError, 'casing collision'):
                action(self.game, self.a)
            self.assertEqual(snapshot(self.game), before)
        result = self.backend.inspect(self.game)
        self.assertFalse(result['proxy_switch_supported'])
        self.assertIn('casing', result['proxy_switch_issue'])

    def test_legacy_proxy_metadata_defaults_to_winmm_and_switch_restores_its_original(self):
        self.backend.install(self.game, self.a)
        record_path = self.game / self.backend.manage.RECORD
        record = json.loads(record_path.read_text())
        record['launcher'].pop('proxy_name')
        record['launcher'].pop('proxy_history')
        record['files'] = [entry for entry in record['files'] if entry['path'] != 'amdnr-linux/proxy.json']
        record['installed'].remove('amdnr-linux/proxy.json')
        (self.game / 'amdnr-linux/proxy.json').unlink()
        original = b'legacy original winmm helper'
        backup = self.game / self.backend.manage.BACKUP / 'winmm.dll'
        backup.write_bytes(original)
        entry = next(item for item in record['files'] if item['path'] == 'winmm.dll')
        entry.update(existed=True, backup_sha256=self.backend.manage.sha(backup))
        record_path.write_text(json.dumps(record))
        self.assertEqual(self.backend.inspect(self.game)['proxy_name'], 'winmm.dll')
        self.backend.switch_proxy(self.game)
        self.assertEqual((self.game / 'winmm.dll').read_bytes(), original)
        self.assertFalse(backup.exists())
        self.backend.uninstall(self.game)
        expected = dict(self.initial, **{'winmm.dll': ('file', original)})
        self.assertEqual(snapshot(self.game), expected)

    def test_failed_switch_rolls_back_alias_config_record_and_receipt(self):
        self.backend.install(self.game, self.a)
        before, receipt_before = snapshot(self.game), self.backend.operation_receipt(self.game)
        apply = self.backend._apply

        def fail(operation):
            apply(operation)
            if operation['path'] == self.game / 'amdnr-linux/proxy.json':
                raise OSError('interrupted proxy configuration')

        with patch.object(self.backend, '_apply', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'interrupted proxy'):
                self.backend.switch_proxy(self.game)
        self.assertEqual(snapshot(self.game), before)
        self.assertEqual(self.backend.operation_receipt(self.game), receipt_before)

    def test_foreign_case_collision_created_before_commit_is_preserved(self):
        self.backend.install(self.game, self.a)
        commit = self.backend._commit
        before = snapshot(self.game)

        def concurrent_file(game, operations):
            (self.game / 'VERSION.DLL').write_bytes(b'new unrelated file')
            return commit(game, operations)

        with patch.object(self.backend, '_commit', side_effect=concurrent_file):
            with self.assertRaisesRegex(ConflictError, 'became occupied'):
                self.backend.switch_proxy(self.game, proxy='version.dll')
        expected = dict(before, **{'VERSION.DLL': ('file', b'new unrelated file')})
        self.assertEqual(snapshot(self.game), expected)

    def test_switch_refuses_modified_wrapper_configuration_and_invalid_metadata(self):
        self.backend.install(self.game, self.a)
        config = self.game / 'amdnr-linux/proxy.json'
        config.write_text('{"proxy":"dbghelp.dll"}')
        before = snapshot(self.game)
        with self.assertRaisesRegex(ConflictError, 'Repair'):
            self.backend.switch_proxy(self.game)
        self.assertEqual(snapshot(self.game), before)
        self.backend.repair(self.game, self.a)
        record_path = self.game / self.backend.manage.RECORD
        original_record = json.loads(record_path.read_text())
        for metadata in ({'proxy_name': 'd3d12.dll'}, {'proxy_history': ['winmm.dll', 'winmm.dll']},
                         {'proxy_name': 'version.dll', 'proxy_history': ['version.dll']}):
            record = json.loads(json.dumps(original_record))
            record['launcher'].update(metadata)
            record_path.write_text(json.dumps(record))
            with self.subTest(metadata=metadata), self.assertRaises(ConflictError):
                self.backend.switch_proxy(self.game)
            self.assertEqual(self.backend.inspect(self.game)['status'], 'conflict')

    @unittest.skipUnless(hasattr(os, 'fork'), 'Linux crash-recovery test')
    def test_interrupted_switch_is_recovered_as_one_transaction(self):
        self.backend.install(self.game, self.a)
        before, receipt_before = snapshot(self.game), self.backend.operation_receipt(self.game)
        child = os.fork()
        if child == 0:
            apply = self.backend._apply

            def interrupted(operation):
                apply(operation)
                if operation['path'] == self.game / 'amdnr-linux/proxy.json':
                    os._exit(91)

            self.backend._apply = interrupted
            self.backend.switch_proxy(self.game)
            os._exit(92)
        _, status = os.waitpid(child, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 91)
        self.assertTrue(self.backend.inspect(self.game)['recovery_needed'])
        self.backend.recover(self.game)
        self.assertEqual(snapshot(self.game), before)
        self.assertEqual(self.backend.operation_receipt(self.game), receipt_before)

    def test_legacy_wrapper_without_proxy_support_requires_repair_before_switch(self):
        (self.a / 'amdnr-linux/proxies.py').unlink()
        self.backend.install(self.game, self.a)
        before = snapshot(self.game)
        self.assertIsNone(self.backend.inspect(self.game)['proxy_next'])
        self.assertFalse(self.backend.inspect(self.game)['proxy_switch_supported'])
        self.assertIn('Repair', self.backend.inspect(self.game)['proxy_switch_issue'])
        with self.assertRaisesRegex(ConflictError, 'Repair'):
            self.backend.switch_proxy(self.game)
        self.assertEqual(snapshot(self.game), before)
        self.backend.repair(self.game, self.b)
        self.assertEqual(self.backend.switch_proxy(self.game)['proxy_name'], 'version.dll')

    def test_redundant_install_refused_and_repair_restores_damaged_files(self):
        self.backend.install(self.game, self.a)
        before = snapshot(self.game)
        with self.assertRaisesRegex(ConflictError, "already"):
            self.backend.install(self.game, self.a)
        self.assertEqual(snapshot(self.game), before)
        (self.game / "d3d12.dll").unlink()
        (self.game / "winmm.dll").write_bytes(b"damaged")
        (self.game / "OptiScaler.ini").write_bytes(b"keep user settings")
        status = self.backend.inspect(self.game)
        self.assertEqual(status["missing"], ["d3d12.dll"])
        self.assertIn("winmm.dll", status["modified"])
        self.assertTrue(status["config_modified"])
        self.backend.repair(self.game, self.a)
        self.assertEqual((self.game / "winmm.dll").read_bytes(), b"A winmm.dll")
        self.assertEqual((self.game / "OptiScaler.ini").read_bytes(), b"keep user settings")

    def test_file_health_does_not_claim_runtime_and_checks_wrapper_permissions(self):
        result = self.backend.install(self.game, self.a)
        self.assertEqual(result["installation_health"], "healthy")
        self.assertEqual(result["runtime_status"], "unverified")
        self.assertEqual(result["health_issues"], [])
        wrapper = self.game / "amdnr-linux/run.sh"
        wrapper.chmod(0o644)
        result = self.backend.inspect(self.game)
        self.assertEqual(result["status"], "needs_repair")
        self.assertEqual(result["modified"], [])  # permissions are not content hashes
        self.assertIn("not executable", result["health_issues"][0])
        self.backend.repair(self.game, self.a)
        self.assertEqual(self.backend.inspect(self.game)["installation_health"], "healthy")

    def test_durable_python_missing_invalid_and_non_executable_are_reported(self):
        runtime = self.root / "durable Python"
        runtime.write_bytes(b"fixture only; never execute")
        runtime.chmod(0o755)
        (self.a / "amdnr-linux/python-runtime.path").write_text(str(runtime) + "\n")
        self.backend.install(self.game, self.a)
        self.assertEqual(self.backend.inspect(self.game)["health_issues"], [])
        runtime.chmod(0o644)
        self.assertIn("cannot run", self.backend.inspect(self.game)["health_issues"][0])
        runtime.unlink()
        result = self.backend.inspect(self.game)
        self.assertEqual(result["status"], "needs_repair")
        self.assertIn("missing", result["health_issues"][0])
        pointer = self.game / "amdnr-linux/python-runtime.path"
        for value in ("relative/python\n", "/valid/path\n/unexpected/line\n", "x" * 4097):
            pointer.write_text(value)
            self.assertTrue(any("path is invalid" in issue for issue in self.backend.inspect(self.game)["health_issues"]))

    def test_inspection_checks_missing_and_changed_owned_prefix_files(self):
        self.backend.install(self.game, self.a)
        _, compiler = self.prefix()
        compiler.unlink()
        result = self.backend.inspect(self.game)
        self.assertEqual(result["status"], "needs_repair")
        self.assertTrue(any(str(compiler) in issue for issue in result["health_issues"]))
        self.backend.repair(self.game, self.a)
        self.assertEqual(self.backend.inspect(self.game)["installation_health"], "healthy")
        compiler.write_bytes(b"another installation's compiler")
        result = self.backend.inspect(self.game)
        self.assertEqual(result["status"], "conflict")
        self.assertTrue(any("modified outside" in issue for issue in result["conflicts"]))

    def test_startup_diagnostics_are_bounded_historical_and_do_not_follow_links(self):
        self.backend.install(self.game, self.a)
        empty = self.backend.diagnostics(self.game)
        self.assertEqual(empty["startup_evidence"], [])
        self.assertIn("No supported startup logs", empty["startup_summary"])
        private = self.root / "private.txt"
        private.write_text("must never be included")
        (self.game / "amd_bridge.log").symlink_to(private)
        for index in range(12):
            (self.game / f"OptiScaler.previous-{index}.Fixture.log").write_text("old successful frame\n" * 2000)
        (self.game / "OptiScaler.log").write_text("prior session loaded and rendered\n")
        framework_log = "old REFramework startup\n" * 2000 + "Waiting for startup\n"
        (self.game / "re2_framework_log.txt").write_text(framework_log)
        os.utime(self.game / "OptiScaler.log", (1, 1))
        os.mkfifo(self.game / "lmxxf_backend.log")
        before = snapshot(self.game)
        report = self.backend.diagnostics(self.game)
        self.assertEqual(snapshot(self.game), before)
        self.assertEqual(report["runtime_status"], "unverified")
        self.assertIn("historical", report["startup_summary"])
        self.assertLessEqual(len(report["startup_evidence"]), 8)
        self.assertTrue(any(entry["truncated"] for entry in report["startup_evidence"]))
        framework = next(entry for entry in report["startup_evidence"] if entry["name"] == "re2_framework_log.txt")
        self.assertEqual(framework["tail"], framework_log[-16384:])
        self.assertTrue(framework["truncated"])
        self.assertTrue(all(len(entry["tail"]) <= 16384 for entry in report["startup_evidence"]))
        self.assertTrue(all("must never be included" not in entry["tail"] for entry in report["startup_evidence"]))
        self.assertTrue(any("amd_bridge.log" in error for error in report["startup_errors"]))
        self.assertFalse(any(entry["name"] == "lmxxf_backend.log" for entry in report["startup_evidence"]))

    def test_legacy_cli_install_is_adopted_without_changing_original_backups(self):
        self.backend.manage.install(self.a, self.game)
        original = (self.game / self.backend.manage.BACKUP / "d3d12.dll").read_bytes()
        (self.game / "winmm.dll").unlink()
        self.backend.repair(self.game, self.b)
        record = json.loads((self.game / self.backend.manage.RECORD).read_text())
        self.assertEqual(record["launcher"]["version"], "B")
        self.assertEqual((self.game / self.backend.manage.BACKUP / "d3d12.dll").read_bytes(), original)
        self.backend.uninstall(self.game)
        self.assertEqual(snapshot(self.game), self.initial)

    def test_failed_initial_install_rolls_back_all_files(self):
        apply = self.backend._apply
        calls = 0

        def fail(operation):
            nonlocal calls
            apply(operation)
            calls += 1
            if calls == 5:
                raise OSError("simulated failed write")

        with patch.object(self.backend, "_apply", side_effect=fail):
            with self.assertRaisesRegex(OSError, "simulated"):
                self.backend.install(self.game, self.a)
        self.assertEqual(snapshot(self.game), self.initial)
        self.assertFalse(self.backend._transaction_dir(self.game).exists())
        self.assertFalse((self.game / self.backend.manage.BACKUP).exists())

    def test_failed_update_rolls_back_game_and_prefix_together(self):
        self.backend.install(self.game, self.a)
        prefix, compiler = self.prefix()
        game_before, prefix_before = snapshot(self.game), snapshot(prefix)
        receipt_before = self.backend.operation_receipt(self.game)
        apply = self.backend._apply

        def fail(operation):
            apply(operation)
            if operation["path"] == compiler:
                raise OSError("prefix write failed")

        with patch.object(self.backend, "_apply", side_effect=fail):
            with self.assertRaisesRegex(OSError, "prefix write"):
                self.backend.update(self.game, self.b)
        self.assertEqual(snapshot(self.game), game_before)
        self.assertEqual(snapshot(prefix), prefix_before)
        self.assertEqual(self.backend.operation_receipt(self.game), receipt_before)

    @unittest.skipUnless(hasattr(os, "fork"), "Linux crash-recovery test")
    def test_process_interruption_recovers_durable_transaction(self):
        self.backend.install(self.game, self.a)
        before = snapshot(self.game)
        child = os.fork()
        if child == 0:
            apply = self.backend._apply

            def interrupted(operation):
                apply(operation)
                os._exit(91)

            self.backend._apply = interrupted
            self.backend.update(self.game, self.b)
            os._exit(92)
        _, status = os.waitpid(child, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 91)
        self.assertTrue(self.backend.inspect(self.game)["recovery_needed"])
        with self.backend._lock(self.game):
            pass
        self.assertEqual(snapshot(self.game), before)
        self.assertFalse(self.backend.inspect(self.game)["recovery_needed"])

    def test_prefix_dlls_are_updated_and_original_prefix_backup_preserved(self):
        self.backend.install(self.game, self.a)
        prefix, compiler = self.prefix()
        backups = snapshot(self.game / "amdnr-linux/prefix-backups")
        self.backend.update(self.game, self.b)
        self.assertEqual(compiler.read_bytes(), b"B d3dcompiler_47.dll")
        self.assertEqual((compiler.parent / "amdhip64_7.dll").read_bytes(), b"B amdnr-linux/x86_64-windows/amdhip64_7.dll")
        self.assertEqual(snapshot(self.game / "amdnr-linux/prefix-backups"), backups)
        self.backend.uninstall(self.game)
        self.assertEqual(compiler.read_bytes(), b"original prefix compiler")
        self.assertFalse((compiler.parent / "amdhip64_7.dll").exists())

    def test_modified_prefix_is_not_overwritten(self):
        self.backend.install(self.game, self.a)
        _, compiler = self.prefix()
        compiler.write_bytes(b"user replacement compiler")
        before = snapshot(self.game)
        with self.assertRaisesRegex(ConflictError, "modified outside"):
            self.backend.update(self.game, self.b)
        self.assertEqual(snapshot(self.game), before)
        self.assertEqual(compiler.read_bytes(), b"user replacement compiler")

    def test_obsolete_files_only_removed_if_owned_and_unchanged(self):
        (self.a / "obsolete.dll").write_bytes(b"old addon")
        (self.game / "obsolete.dll").write_bytes(b"original unrelated file")
        self.backend.install(self.game, self.a)
        (self.game / "obsolete.dll").write_bytes(b"later edit")
        with self.assertRaisesRegex(ConflictError, "Obsolete"):
            self.backend.update(self.game, self.b)
        (self.game / "obsolete.dll").write_bytes(b"old addon")
        self.backend.update(self.game, self.b)
        self.assertEqual((self.game / "obsolete.dll").read_bytes(), b"original unrelated file")
        self.assertFalse((self.game / self.backend.manage.BACKUP / "obsolete.dll").exists())

    def test_new_unowned_collision_blocks_update(self):
        self.backend.install(self.game, self.a)
        (self.b / "new.dll").write_bytes(b"new addon")
        (self.game / "new.dll").write_bytes(b"unrelated user mod")
        before = snapshot(self.game)
        with self.assertRaisesRegex(ConflictError, "unrelated"):
            self.backend.update(self.game, self.b)
        self.assertEqual(snapshot(self.game), before)

    def test_symlink_and_traversal_rejected_before_mutation(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.game / "amdnr-linux").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self.backend.install(self.game, self.a)
        self.assertEqual(list(outside.iterdir()), [])
        (self.game / "amdnr-linux").unlink()
        self.backend.install(self.game, self.a)
        record = self.game / self.backend.manage.RECORD
        data = json.loads(record.read_text())
        data["files"][0]["path"] = "../outside/file"
        record.write_text(json.dumps(data))
        with self.assertRaises(ConflictError):
            self.backend.repair(self.game, self.b)
        self.assertEqual(list(outside.iterdir()), [])

    def test_package_symlink_and_running_game_refused(self):
        (self.a / "linked.dll").symlink_to(self.game / "winmm.dll")
        with self.assertRaisesRegex(ConflictError, "symlink"):
            self.backend.install(self.game, self.a)
        (self.a / "linked.dll").unlink()
        with patch.object(self.backend, "_running", return_value=[12345]):
            with self.assertRaises(GameRunning):
                self.backend.install(self.game, self.a)
        self.assertEqual(snapshot(self.game), self.initial)

    def test_changed_original_backup_blocks_update_and_uninstall(self):
        self.backend.install(self.game, self.a)
        (self.game / self.backend.manage.BACKUP / "d3d12.dll").write_bytes(b"changed backup")
        with self.assertRaisesRegex(ConflictError, "backup was modified"):
            self.backend.update(self.game, self.b)
        with self.assertRaisesRegex(ConflictError, "backup was modified"):
            self.backend.uninstall(self.game)

    def test_package_preparation_discovers_native_compiler_in_order(self):
        first, second = self.root / "first game", self.root / "second game"
        first.mkdir()
        second.mkdir()
        (first / "d3dcompiler_47.dll").write_bytes(compiler_data(b"Wine builtin DLL"))
        native = second / "d3dcompiler_47.dll"
        native.write_bytes(compiler_data())
        before = snapshot(self.game)

        def prepare(compiler, archive, output, cache):
            self.assertEqual(compiler, native)
            self.assertEqual(cache, self.backend.cache_dir / "downloads")
            output.mkdir()
            return output

        with patch.object(self.backend.setup, "prepare", side_effect=prepare):
            prepared = self.backend.prepare_package(None, [first, second])
        self.assertTrue(prepared.is_dir())
        self.assertEqual(snapshot(self.game), before)
        with self.assertRaises(CompilerNeeded):
            self.backend.prepare_package(None, [first])

    def test_backend_module_roots_are_isolated(self):
        release = self.root / "other release"
        (release / "linux").mkdir(parents=True)
        for name in ("manage.py", "launch.py", "setup.py"):
            shutil.copy2(ROOT / name, release / "linux" / name)
        other = LauncherBackend(release, self.root / "cache2", self.root / "state2")
        self.assertEqual(other.setup.ROOT, release / "linux")
        self.assertEqual(other.manage.ROOT, release / "linux")
        self.assertIs(other.setup.manage, other.manage)
        self.assertEqual(self.backend.setup.ROOT, self.release / "linux")
        self.assertEqual(self.backend.manage.ROOT, self.release / "linux")

    def test_preview_manifest_install_upgrade_and_downgrade_protection(self):
        (self.release / 'RELEASE-MANIFEST.json').write_text(json.dumps({'release': 'linux-v0.2.2-preview'}))
        loaded = LauncherBackend(self.release, self.root / 'cache', self.root / 'state')
        self.assertEqual(loaded.addon_version, '0.2.2-preview')
        self.backend.addon_version = loaded.addon_version
        self.backend.install(self.game, self.a)
        self.assertEqual(self.backend.inspect(self.game)['addon_version'], '0.2.2-preview')
        self.backend.addon_version = '0.2.2-preview.2'
        self.backend.update(self.game, self.b)
        self.assertEqual(self.backend.inspect(self.game)['addon_version'], '0.2.2-preview.2')
        self.backend.addon_version = '0.2.2'
        self.backend.update(self.game, self.b)
        before = snapshot(self.game)
        self.backend.addon_version = '0.2.2-preview.10'
        with self.assertRaisesRegex(ConflictError, 'downgrade'):
            self.backend.update(self.game, self.a)
        self.assertEqual(snapshot(self.game), before)

    def test_release_versions_reject_downgrade_and_repair_version_change(self):
        self.backend.addon_version = "0.2.0"
        self.backend.install(self.game, self.a)
        info = self.backend.inspect(self.game)
        self.assertEqual(info["addon_version"], "0.2.0")
        self.assertEqual(info["host_version"], "A")
        self.assertEqual(info["version"], "0.2.0")
        for older in ("0.1.0", None):
            self.backend.addon_version = older
            with self.assertRaisesRegex(ConflictError, "downgrade"):
                self.backend.update(self.game, self.b)
        self.backend.addon_version = "0.3.0"
        with self.assertRaisesRegex(ConflictError, "Repair requires"):
            self.backend.repair(self.game, self.b)
        self.backend.update(self.game, self.b)
        self.assertEqual(self.backend.inspect(self.game)["addon_version"], "0.3.0")

    def test_receipt_tracks_install_repair_and_uninstall_tokens(self):
        self.assertIsNone(self.backend.operation_receipt(self.game))
        self.backend.operation_id = "install-token"
        self.backend.install(self.game, self.a)
        self.assertEqual(self.backend.operation_receipt(self.game)["operation_id"], "install-token")
        self.backend.operation_id = "repair-token"
        self.backend.repair(self.game, self.a)
        receipt = self.backend.operation_receipt(self.game)
        self.assertEqual(receipt["operation_id"], "repair-token")
        self.assertEqual(receipt["action"], "repair")
        record = json.loads((self.game / self.backend.manage.RECORD).read_text())
        self.assertEqual(record["launcher"]["operation_id"], "repair-token")
        self.backend.operation_id = "uninstall-token"
        self.backend.uninstall(self.game)
        self.assertEqual(self.backend.operation_receipt(self.game)["operation_id"], "uninstall-token")
        self.assertEqual(self.backend.operation_receipt(self.game)["action"], "uninstall")
        self.assertFalse(self.backend.inspect(self.game)["installed"])

    def test_failure_after_receipt_write_rolls_it_back_with_installation(self):
        self.backend.install(self.game, self.a)
        before, receipt_before = snapshot(self.game), self.backend.operation_receipt(self.game)
        apply = self.backend._apply

        def fail(operation):
            apply(operation)
            if operation["path"] == self.backend._receipt_path(self.game):
                raise OSError("commit interrupted after receipt replacement")

        self.backend.operation_id = "failed-uninstall"
        with patch.object(self.backend, "_apply", side_effect=fail):
            with self.assertRaisesRegex(OSError, "receipt replacement"):
                self.backend.uninstall(self.game)
        self.assertEqual(snapshot(self.game), before)
        self.assertEqual(self.backend.operation_receipt(self.game), receipt_before)

    def test_original_prefix_symlink_restored_on_uninstall(self):
        self.backend.install(self.game, self.a)
        prefix = self.root / "prefix with original link"
        compiler = prefix / "drive_c/windows/system32/d3dcompiler_47.dll"
        compiler.parent.mkdir(parents=True)
        original = self.root / "Wine compiler.dll"
        original.write_bytes(b"original external library")
        compiler.symlink_to(original)
        self.backend.launch.prepare_prefix(self.game / "amdnr-linux", prefix)
        self.backend.update(self.game, self.b)
        self.backend.uninstall(self.game)
        self.assertTrue(compiler.is_symlink())
        self.assertEqual(compiler.readlink(), original)
        self.assertEqual(original.read_bytes(), b"original external library")

    def test_state_symlink_and_simultaneous_mutation_are_rejected(self):
        outside = self.root / "outside state"
        outside.mkdir()
        self.backend.state_dir.mkdir()
        (self.backend.state_dir / "locks").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self.backend.install(self.game, self.a)
        self.assertEqual(list(outside.iterdir()), [])
        (self.backend.state_dir / "locks").unlink()
        with self.backend._lock(self.game):
            with self.assertRaisesRegex(RuntimeError, "already changing"):
                self.backend.install(self.game, self.a)

    def test_progress_callback_failure_does_not_invalidate_commit(self):
        other = LauncherBackend(self.release, self.root / "cache2", self.root / "state2",
                                lambda text: (_ for _ in ()).throw(RuntimeError("UI closed")))
        with patch.object(other, "_running", return_value=[]):
            result = other.install(self.game, self.a)
        self.assertTrue(result["installed"])
        self.assertEqual(other.operation_receipt(self.game)["action"], "install")


if __name__ == "__main__":
    unittest.main()
