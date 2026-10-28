"""Headless end-to-end controller transactions on synthetic game/Steam files."""
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import tempfile
import unittest
import zipfile
from dataclasses import replace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from native_launcher import controller, steam
import test_native_backend as backend_fixture
import test_native_steam as steam_fixture


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = backend_fixture.BackendTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.game_dir = self.fixture.root, self.fixture.game
        release = self.root / "release"
        (release / "linux").mkdir(parents=True)
        for name in ("launch.py", "manage.py", "setup.py", "proxies.py"):
            shutil.copy2(ROOT / name, release / "linux" / name)
        (release / "RELEASE-MANIFEST.json").write_text(json.dumps({"release": "linux-v0.2.0"}))
        self.steam_root = self.root / "Steam"
        self.config = self.steam_root / "userdata/1001/config/localconfig.vdf"
        self.config.parent.mkdir(parents=True)
        self.config.write_bytes(steam_fixture.config("MANGOHUD=1 gamemoderun %command% --original"))
        self.config_before = self.config.read_bytes()
        self.game = steam.SteamGame("123", "Fixture Game", self.game_dir, self.steam_root,
                                    self.root / "library", self.game_dir)
        self.app = controller.Controller(release, self.root / "controller state", self.root / "controller cache",
                                         [self.steam_root], heroic_roots=[], lutris_roots=[])
        self.app.games = [self.game]
        self.package = self.fixture.a
        for replacement in (patch.object(steam, "steam_is_running", return_value=False),
                            patch.object(controller, "available_release", return_value=None),
                            patch.object(self.app.backend, "_running", return_value=[]),
                            patch.object(self.app, "prepare", side_effect=lambda *args: self.package)):
            replacement.start()
            self.addCleanup(replacement.stop)

    def test_complete_lifecycle_restores_launch_options_and_user_files(self):
        self.app.operate("install", self.game)
        self.assertIn(b"amdnr-linux/run.sh", self.config.read_bytes())
        self.assertEqual(self.app.backend.inspect(self.game_dir)["addon_version"], "0.2.0")
        (self.game_dir / "winmm.dll").write_bytes(b"damaged")
        (self.game_dir / "OptiScaler.ini").write_bytes(b"user settings")
        self.app.operate("repair", self.game)
        self.assertEqual((self.game_dir / "winmm.dll").read_bytes(), b"A winmm.dll")
        self.package = self.fixture.b
        self.app.operate("update", self.game)
        self.assertEqual((self.game_dir / "winmm.dll").read_bytes(), b"B winmm.dll")
        self.assertEqual((self.game_dir / "OptiScaler.ini").read_bytes(), b"user settings")
        self.app.operate("uninstall", self.game)
        self.assertEqual(self.config.read_bytes(), self.config_before)
        self.assertEqual(backend_fixture.snapshot(self.game_dir), self.fixture.initial)
        self.assertFalse(self.app.pending_operation(self.game))

    def test_backend_failure_restores_steam_and_game_together(self):
        apply = self.app.backend._apply
        calls = 0

        def fail(operation):
            nonlocal calls
            apply(operation)
            calls += 1
            if calls == 4:
                raise OSError("failed installation")

        with patch.object(self.app.backend, "_apply", side_effect=fail):
            with self.assertRaisesRegex(OSError, "failed installation"):
                self.app.operate("install", self.game)
        self.assertEqual(self.config.read_bytes(), self.config_before)
        self.assertEqual(backend_fixture.snapshot(self.game_dir), self.fixture.initial)
        self.assertFalse(self.app.pending_operation(self.game))
        self.assertIsNone(self.app.backend.operation_receipt(self.game_dir))

    def test_select_switch_repair_and_remove_proxy_without_redownload(self):
        self.app.operate("install", self.game, proxy="version.dll")
        self.assertEqual(self.app.inspect(self.game)["proxy_name"], "version.dll")
        self.assertFalse((self.game_dir / "winmm.dll").exists())
        original_options = self.config.read_bytes()
        with patch.object(self.app, "prepare", side_effect=AssertionError("Unexpected preparation")):
            self.app.operate("switch_proxy", self.game, proxy="dbghelp.dll")
            self.app.operate("switch_proxy", self.game, proxy="next")
        self.assertEqual(self.app.inspect(self.game)["proxy_name"], "winmm.dll")
        self.assertEqual(self.config.read_bytes(), original_options)
        self.assertEqual(self.app.backend.operation_receipt(self.game_dir)["action"], "switch_proxy")
        self.app.operate("repair", self.game)
        self.assertEqual(self.app.inspect(self.game)["proxy_history"], ["version.dll", "dbghelp.dll", "winmm.dll"])
        self.app.operate("uninstall", self.game)
        self.assertEqual(self.config.read_bytes(), self.config_before)
        self.assertEqual(backend_fixture.snapshot(self.game_dir), self.fixture.initial)

    def test_wrapper_failure_is_shown_with_the_game(self):
        self.app.operate("install", self.game)
        self.assertFalse(any("last launch" in item["text"] for item in self.app.inspect(self.game)["notices"]))
        (self.game_dir / "amdnr-linux/launch.log").write_text(
            "2026-10-21T08:12:00+00:00\nLaunch setup failed: A different HIP DLL is already installed: /pfx/amdhip64_7.dll\n")
        notice = self.app.inspect(self.game)["notices"][0]
        self.assertEqual(notice["level"], "warning")
        self.assertRegex(notice["text"], r"^The last launch stopped before the game started \(2026-10-2[01] \d\d:\d\d\): "
                                         r"A different HIP DLL is already installed: /pfx/amdhip64_7\.dll$")
        self.assertEqual(controller.launch_failure_text(dict(time="unreadable", message="error")),
                         "The last launch stopped before the game started (unreadable): error")

    def test_launch_option_follows_steam_after_install(self):
        quoted = shlex.quote(str((self.game_dir / "amdnr-linux/run.sh").resolve()))
        self.assertNotIn("launch_option", self.app.inspect(self.game))
        self.app.operate("install", self.game)
        self.assertEqual(self.app.inspect(self.game)["launch_option"],
                         {"options": f"MANGOHUD=1 gamemoderun {quoted} %command% --original", "state": "current"})
        # Edited in Steam later: the wrapper goes back in front of %command%.
        self.config.write_bytes(steam_fixture.config("MANGOHUD=1 %command%"))
        self.assertEqual(self.app.inspect(self.game)["launch_option"],
                         {"options": f"MANGOHUD=1 {quoted} %command%", "state": "merged"})
        manual = steam.SteamGame("", "Manual game", self.game_dir, Path(), self.game_dir, self.game_dir)
        self.assertEqual(self.app.launch_option(manual),
                         {"options": shlex.quote(str(self.game_dir / "amdnr-linux/run.sh")) + " %command%",
                          "state": "plain"})

    def test_proxy_switch_failure_restores_files_and_launch_options(self):
        self.app.operate("install", self.game)
        before = backend_fixture.snapshot(self.game_dir)
        options = self.config.read_bytes()
        apply = self.app.backend._apply
        calls = 0

        def fail(operation):
            nonlocal calls
            apply(operation)
            calls += 1
            if calls == 2:
                raise OSError("failed proxy switch")

        with patch.object(self.app.backend, "_apply", side_effect=fail):
            with self.assertRaisesRegex(OSError, "failed proxy switch"):
                self.app.operate("switch_proxy", self.game, proxy="version.dll")
        self.assertEqual(backend_fixture.snapshot(self.game_dir), before)
        self.assertEqual(self.config.read_bytes(), options)
        self.assertFalse(self.app.pending_operation(self.game))

    def test_proxy_switch_error_after_commit_preserves_both_layers(self):
        self.app.operate("install", self.game)
        switch = self.app.backend.switch_proxy

        def fail(*args, **kwargs):
            switch(*args, **kwargs)
            raise OSError("post-commit switch error")

        with patch.object(self.app.backend, "switch_proxy", side_effect=fail):
            with self.assertRaisesRegex(OSError, "post-commit switch error"):
                self.app.operate("switch_proxy", self.game, proxy="version.dll")
        self.assertEqual(self.app.inspect(self.game)["proxy_name"], "version.dll")
        self.assertIn(b"amdnr-linux/run.sh", self.config.read_bytes())
        self.assertFalse(self.app.pending_operation(self.game))
        self.app.operate("uninstall", self.game)
        self.assertEqual(self.config.read_bytes(), self.config_before)

    def test_install_error_after_commit_keeps_files_and_steam_consistent(self):
        install = self.app.backend.install

        def committed_then_failed(*args):
            install(*args)
            raise OSError("post-commit installation error")

        with patch.object(self.app.backend, "install", side_effect=committed_then_failed):
            with self.assertRaisesRegex(OSError, "post-commit installation error"):
                self.app.operate("install", self.game)
        self.assertTrue(self.app.backend.inspect(self.game_dir)["installed"])
        self.assertIn(b"amdnr-linux/run.sh", self.config.read_bytes())
        self.assertFalse(self.app.pending_operation(self.game))
        self.assertEqual(self.app.backend.operation_receipt(self.game_dir)["action"], "install")
        # The retained Steam ownership record must still permit clean removal.
        self.app.operate("uninstall", self.game)
        self.assertEqual(self.config.read_bytes(), self.config_before)
        self.assertEqual(backend_fixture.snapshot(self.game_dir), self.fixture.initial)

    def test_uninstall_error_after_commit_keeps_restored_files_and_steam(self):
        self.app.operate("install", self.game)
        uninstall = self.app.backend.uninstall

        def committed_then_failed(*args):
            uninstall(*args)
            raise OSError("post-commit removal error")

        with patch.object(self.app.backend, "uninstall", side_effect=committed_then_failed):
            with self.assertRaisesRegex(OSError, "post-commit removal error"):
                self.app.operate("uninstall", self.game)
        self.assertFalse(self.app.backend.inspect(self.game_dir)["installed"])
        self.assertEqual(self.config.read_bytes(), self.config_before)
        self.assertEqual(backend_fixture.snapshot(self.game_dir), self.fixture.initial)
        self.assertFalse(self.app.pending_operation(self.game))
        self.assertEqual(self.app.backend.operation_receipt(self.game_dir)["action"], "uninstall")

    def _crash(self, action, after_backend_commit):
        child = os.fork()
        if child == 0:
            if after_backend_commit:
                method = getattr(self.app.backend, action)

                def interrupted(*args):
                    method(*args)
                    os._exit(94)

                setattr(self.app.backend, action, interrupted)
            else:
                apply = self.app.backend._apply

                def interrupted(operation):
                    apply(operation)
                    os._exit(93)

                self.app.backend._apply = interrupted
            self.app.operate(action, self.game)
            os._exit(99)
        _, status = os.waitpid(child, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 94 if after_backend_commit else 93)
        self.assertTrue(self.app.pending_operation(self.game))

    @unittest.skipUnless(hasattr(os, "fork"), "Linux process interruption regression")
    def test_crash_before_backend_commit_rolls_back_steam_and_files(self):
        self._crash("install", after_backend_commit=False)
        self.app.recover_operation(self.game)
        self.assertEqual(self.config.read_bytes(), self.config_before)
        self.assertEqual(backend_fixture.snapshot(self.game_dir), self.fixture.initial)
        self.assertFalse(self.app.pending_operation(self.game))

    @unittest.skipUnless(hasattr(os, "fork"), "Linux process interruption regression")
    def test_crash_after_backend_commit_keeps_steam_and_installation(self):
        self._crash("install", after_backend_commit=True)
        self.app.recover_operation(self.game)
        self.assertTrue(self.app.backend.inspect(self.game_dir)["installed"])
        self.assertIn(b"amdnr-linux/run.sh", self.config.read_bytes())
        self.assertFalse(self.app.pending_operation(self.game))
        self.app.operate("uninstall", self.game)
        self.assertEqual(self.config.read_bytes(), self.config_before)

    @unittest.skipUnless(hasattr(os, "fork"), "Linux process interruption regression")
    def test_crash_after_uninstall_commit_keeps_restored_steam_options(self):
        self.app.operate("install", self.game)
        self._crash("uninstall", after_backend_commit=True)
        self.app.recover_operation(self.game)
        self.assertFalse(self.app.backend.inspect(self.game_dir)["installed"])
        self.assertEqual(self.config.read_bytes(), self.config_before)
        self.assertEqual(backend_fixture.snapshot(self.game_dir), self.fixture.initial)

    def test_clean_after_lost_record_restores_files_and_launch_options(self):
        self.package = self.fixture.realistic("C")  # files are recognised by their contents
        self.app.operate("install", self.game)
        (self.game_dir / "OptiScaler.log").write_bytes(b"AMDNR session start\n")
        (self.game_dir / self.app.backend.manage.RECORD).unlink()
        result = self.app.inspect(self.game)
        self.assertFalse(result["installed"])
        self.assertTrue(result["leftovers"]["steam_wrapper"])
        self.assertIn("OptiScaler.log", result["leftovers"]["remove"])
        message = self.app.operate("clean", self.game)["message"]
        self.assertIn("Original game files were put back", message)
        self.assertIn("Steam's launch options no longer", message)
        self.assertEqual(self.config.read_bytes(), self.config_before)
        self.assertEqual(backend_fixture.snapshot(self.game_dir), self.fixture.initial)
        result = self.app.inspect(self.game)
        self.assertEqual((result["leftovers"]["remove"], result["leftovers"]["steam_wrapper"]), ([], False))
        with self.assertRaisesRegex(ValueError, "No leftover"):
            self.app.operate("clean", self.game)
        # A wrapper added by hand is found even with nothing left in the folder.
        wrapper = steam.shlex.quote(str(self.game_dir / "amdnr-linux/run.sh"))
        self.config.write_bytes(steam_fixture.config(f"MANGOHUD=1 gamemoderun {wrapper} %command% --original"))
        self.assertTrue(self.app.inspect(self.game)["leftovers"]["steam_wrapper"])
        self.assertIn("No AMDNR files were left", self.app.operate("clean", self.game)["message"])
        self.assertEqual(self.config.read_bytes(), self.config_before)

    def test_clean_stops_before_files_when_steam_options_cannot_change(self):
        self.app.operate("install", self.game)
        (self.game_dir / self.app.backend.manage.RECORD).unlink()
        before = backend_fixture.snapshot(self.game_dir)
        with patch.object(steam, "remove_wrapper", side_effect=ValueError("Steam settings changed")), \
                patch.object(self.app.backend, "clean") as clean:
            with self.assertRaisesRegex(ValueError, "Steam settings changed"):
                self.app.operate("clean", self.game)
        clean.assert_not_called()
        self.assertEqual(backend_fixture.snapshot(self.game_dir), before)

    def test_steam_error_prevents_backend_mutation(self):
        self.config.write_bytes(steam_fixture.config("%command% %command%"))
        before = self.config.read_bytes()
        with patch.object(self.app.backend, "install") as install:
            with self.assertRaises(ValueError):
                self.app.operate("install", self.game)
        install.assert_not_called()
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(backend_fixture.snapshot(self.game_dir), self.fixture.initial)

    def test_controller_refuses_older_repair_before_preparing_assets(self):
        self.app.backend.addon_version = "0.3.0"
        self.app.backend.install(self.game_dir, self.package)
        with patch.object(self.app, "prepare") as prepare:
            with self.assertRaisesRegex(ValueError, "newer Linux package"):
                self.app.operate("repair", self.game)
        prepare.assert_not_called()
        self.assertEqual(self.config.read_bytes(), self.config_before)

    def test_ambiguous_library_does_not_use_cached_executable_or_install(self):
        game = replace(self.game, library_ambiguous=True, exe_dir=None)
        exe = self.game_dir / "game.exe"
        exe.write_bytes(b"existing executable")
        self.app.settings["executables"] = {self.app.game_key(game): str(exe)}
        with patch.object(steam, "discover_games", return_value=[game]):
            self.assertIsNone(self.app.discover()[0].exe_dir)
        with self.assertRaisesRegex(ValueError, "more than one library"):
            self.app.set_executable(game, exe)
        with patch.object(self.app, "prepare") as prepare:
            with self.assertRaisesRegex(ValueError, "more than one library"):
                self.app.operate("install", game)
        prepare.assert_not_called()
        self.assertEqual(self.config.read_bytes(), self.config_before)

    def test_manual_unreal_launcher_uses_the_shipping_folder_after_rescan(self):
        folder = self.root / "Unreal Game"
        stub, shipping, tool = (folder / "Game.exe", folder / "Game/Binaries/Win64/Game-Win64-Shipping.exe",
                                folder / "Tools/Editor.exe")
        for path in (stub, shipping, tool):
            steam_fixture.executable(path)
        folder, shipping, tool = folder.resolve(), shipping.resolve(), tool.resolve()
        with patch.object(steam, "discover_games", return_value=[]):
            self.app.add_manual(stub)
            game = self.app.discover()[0]
            self.assertEqual((game.name, game.exe_dir), ("Unreal Game", shipping.parent))
            self.assertEqual(self.app.executable(game), shipping)
            # Choose game executable on a manual game also survives a rescan.
            self.app.set_executable(game, tool)
            self.assertEqual(self.app.discover()[0].exe_dir, tool.parent)
            self.app.set_executable(game, stub)
            self.assertEqual(self.app.discover()[0].exe_dir, shipping.parent)

    def test_controller_repairs_nonexecutable_wrapper_before_launching(self):
        self.app.operate("install", self.game)
        wrapper = self.game_dir / "amdnr-linux/run.sh"
        configured = self.config.read_bytes()
        wrapper.chmod(0o644)
        self.assertEqual(self.app.backend.inspect(self.game_dir)["status"], "needs_repair")
        with self.assertRaisesRegex(ValueError, "not executable"):
            steam.configure_launch(self.game, wrapper, self.app.data_dir)
        self.app.operate("repair", self.game)
        self.assertTrue(os.access(wrapper, os.X_OK))
        self.assertEqual(self.app.backend.inspect(self.game_dir)["installation_health"], "healthy")
        self.assertEqual(self.config.read_bytes(), configured)
        self.assertFalse(self.app.pending_operation(self.game))

    def test_log_export_has_diagnostics_without_host_logs_and_can_replace(self):
        destination = self.root / "logs.zip"
        destination.write_bytes(b"old export")
        self.app.collect_logs(self.game, destination)
        with zipfile.ZipFile(destination) as output:
            self.assertIn("diagnostics.json", output.namelist())
            details = json.loads(output.read("diagnostics.json"))
            self.assertEqual(details["runtime_status"], "unverified")
            self.assertIn("startup_summary", details)
        with patch.object(controller.json, "dumps", side_effect=ValueError("export failure")):
            before = destination.read_bytes()
            with self.assertRaises(ValueError):
                self.app.collect_logs(self.game, destination)
            self.assertEqual(destination.read_bytes(), before)

    def test_log_export_never_follows_log_symlinks(self):
        private = self.root / "private.txt"
        private.write_text("unrelated private data")
        (self.game_dir / "OptiScaler.log").symlink_to(private)
        (self.game_dir / "re2_framework_log.txt").symlink_to(private)
        destination = self.root / "logs.zip"
        self.app.collect_logs(self.game, destination)
        with zipfile.ZipFile(destination) as output:
            self.assertNotIn("OptiScaler.log", output.namelist())
            self.assertNotIn("re2_framework_log.txt", output.namelist())
            self.assertNotIn(b"unrelated private data", output.read("diagnostics.json"))

    def test_log_export_includes_reframework_and_bounds_large_logs(self):
        log = self.game_dir / "re2_framework_log.txt"
        data = b"REFramework startup\n" * 2000 + b"Waiting for startup\n"
        log.write_bytes(data)
        destination = self.root / "logs.zip"
        self.app.collect_logs(self.game, destination)
        with zipfile.ZipFile(destination) as output:
            self.assertEqual(output.read(log.name), data)
            details = json.loads(output.read("diagnostics.json"))
            framework = next(entry for entry in details["startup_evidence"] if entry["name"] == log.name)
            self.assertEqual(framework["tail"], data[-16384:].decode())
            self.assertTrue(framework["truncated"])
        # An oversized full log is excluded, but its bounded tail remains useful.
        with log.open("r+b") as stream:
            stream.truncate(50 * 1024**2)
            stream.seek(-len(data), os.SEEK_END)
            stream.write(data)
        self.app.collect_logs(self.game, destination)
        with zipfile.ZipFile(destination) as output:
            self.assertNotIn(log.name, output.namelist())
            details = json.loads(output.read("diagnostics.json"))
            framework = next(entry for entry in details["startup_evidence"] if entry["name"] == log.name)
            self.assertEqual(framework["tail"], data[-16384:].decode())
            self.assertTrue(framework["truncated"])


class PersistentRuntimeTests(unittest.TestCase):
    def test_repair_replaces_partial_and_nonexecutable_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            portable = root / "portable"
            executable = portable / "runtime/python/bin/python3.12"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(0o755)
            with patch.dict(os.environ, {"NRL_PORTABLE_ROOT": str(portable)}):
                target = controller.persistent_python(root / "data")
                for damage in (lambda: target.unlink(), lambda: target.chmod(0o644)):
                    damage()
                    self.assertEqual(controller.persistent_python(root / "data"), target)
                    self.assertTrue(os.access(target, os.X_OK))
                    self.assertEqual(target.read_bytes(), executable.read_bytes())


if __name__ == "__main__":
    unittest.main()
