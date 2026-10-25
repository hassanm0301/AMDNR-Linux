"""Optional components through launcher transactions; synthetic files only, no GPU."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from native_launcher.backend import (BackendError, ConflictError, LauncherBackend, _components, host_key,
                                     ini_value, ini_with)
from native_launcher import compatibility
from native_launcher.steam import SteamGame


def sha(data):
    return hashlib.sha256(data).hexdigest()


INI = b"; release comment\r\n[DlssNr]\r\nEnabled=true\r\nNrBackend=lmxxf\r\n\r\n[Plugins]\r\nLoadAsiPlugins=auto\r\n"


class IniTests(unittest.TestCase):
    def test_existing_keys_keep_line_endings_and_comments(self):
        result = ini_with(INI, {("DlssNr", "NrBackend"): "daniel"})
        self.assertEqual(result, INI.replace(b"NrBackend=lmxxf", b"NrBackend=daniel"))
        self.assertEqual(ini_value(result, "DlssNr", "NrBackend"), "daniel")

    def test_missing_keys_join_their_section_and_missing_sections_are_appended(self):
        data = b"[A]\nx=1\n; trailing comment\n[B]\ny=2"
        result = ini_with(data, {("A", "new"): "a", ("B", "z"): "b", ("C", "c"): "c"})
        self.assertEqual(result, b"[A]\nx=1\nnew=a\n; trailing comment\n[B]\ny=2\nz=b\n[C]\nc=c\n")

    def test_reader_ignores_comments_and_other_sections(self):
        data = "﻿[DlssNr]\n;NrBackend=daniel\nNrBackend = lmxxf \n[Other]\nNrBackend=daniel\n".encode()
        self.assertEqual(ini_value(data, "DlssNr", "NrBackend"), "lmxxf")
        self.assertIsNone(ini_value(data, "Missing", "NrBackend"))
        undecodable = b"[DlssNr]\nName=\xff\xfe\nNrBackend=lmxxf\n"
        self.assertEqual(ini_with(undecodable, {("DlssNr", "NrBackend"): "daniel"}),
                         undecodable.replace(b"lmxxf", b"daniel"))


class ComponentTransactionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="native components ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.game = self.root / "Game"
        self.game.mkdir()
        (self.game / "save.dat").write_bytes(b"save")
        self.release = self.root / "release"
        (self.release / "linux").mkdir(parents=True)
        for name in ("launch.py", "manage.py", "setup.py", "proxies.py", "runtimes.py"):
            shutil.copy2(ROOT / name, self.release / "linux" / name)
        self.backend = LauncherBackend(self.release, self.root / "cache", self.root / "state")
        running = patch.object(self.backend, "_running", return_value=[])
        running.start()
        self.addCleanup(running.stop)
        self.runtimes = self.backend.runtimes

    def package(self, label, daniel=False, optipatcher=False, ini=INI, host=False, model_e=False):
        package = self.root / ("package " + label)
        for name in ("winmm.dll", "d3d12.dll", "d3d12core.dll", "d3dcompiler_47.dll", "amdnr-linux/run.sh",
                     "amdnr-linux/launch.py", "amdnr-linux/proxies.py", "amdnr-linux/x86_64-unix/amdhip64_7.so",
                     "amdnr-linux/x86_64-windows/amdhip64_7.dll", "amdnr-linux/x86_64-unix/libamdnr_signal_guard.so"):
            path = package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"{label} {name}".encode())
        (package / "amdnr-linux/run.sh").chmod(0o755)
        (package / "amdnr-linux/SOURCE.txt").write_text(
            "Host/runtime: OptiScaler AMD pre-SR 1.9.1-alpha (independent OptiScaler fork), fixture\n" if host
            else f"Host/runtime: AMDNR {label}, fixture\n")
        backend = b"daniel" if daniel else b"lmxxf"
        (package / "OptiScaler.ini").write_bytes(ini.replace(b"NrBackend=lmxxf", b"NrBackend=" + backend))
        components = {"neural_runtime": "daniel" if daniel else "lmxxf", "daniel": None, "optipatcher": None}
        if daniel:
            for name in self.runtimes.DANIEL_PASSES:
                (package / name).write_bytes(b"pass")
            (package / self.runtimes.DANIEL_WEIGHTS).write_bytes(b"weights")
            components["daniel"] = {"version": "0.6.0", "url": "fixture", "sha256": sha(b"zip"), "pass_sha256": sha(b"pass")}
        if optipatcher:
            (package / self.runtimes.OPTIPATCHER_PATH).parent.mkdir(parents=True, exist_ok=True)
            (package / self.runtimes.OPTIPATCHER_PATH).write_bytes(b"asi")
            components["optipatcher"] = {"version": "0.41", "url": "fixture", "sha256": sha(b"asi")}
        if model_e:
            folder = package / self.runtimes.MODEL_E_FOLDER
            folder.mkdir()
            for name in self.runtimes.MODEL_E_DLLS:
                (folder / name).write_bytes(b"helixsr built here")
            (folder / self.runtimes.MODEL_E_RECEIPT).write_text("receipt")
            components["model_e"] = {"version": "1.8.1", "url": "fixture", "sha256": sha(b"helixsr built here")}
        if host:
            # Alternate hosts read their own settings; model that with a distinct INI.
            (package / "OptiScaler.ini").write_bytes(b"; pre-SR\r\n[DlssNr]\r\nEnabled=true\r\nNrBackend=daniel\r\n")
            components["host"] = {"build": "presr", "version": "1.9.1-alpha", "url": "fixture", "sha256": sha(b"host")}
        (package / "amdnr-linux/components.json").write_text(json.dumps(components))
        return package

    def ini(self):
        return (self.game / "OptiScaler.ini").read_bytes()

    def test_components_install_record_and_uninstall_cleanly(self):
        result = self.backend.install(self.game, self.package("A", daniel=True, optipatcher=True))
        self.assertEqual(result["status"], "installed")
        self.assertEqual(result["components"]["daniel"]["version"], "0.6.0")
        self.assertEqual(result["ini_runtime"], "daniel")
        self.assertTrue((self.game / "dlssnr_amd_pass3.dll").is_file())
        self.assertTrue((self.game / "OptiScaler/plugins/OptiPatcher.asi").is_file())
        self.backend.uninstall(self.game)
        self.assertEqual(sorted(path.name for path in self.game.iterdir()), ["save.dat"])

    def test_changing_runtime_rewrites_only_its_key_in_the_users_ini(self):
        self.backend.install(self.game, self.package("A"))
        edited = self.ini().replace(b"Enabled=true", b"Enabled=true\r\nUserSetting=kept")
        (self.game / "OptiScaler.ini").write_bytes(edited)
        self.backend.update(self.game, self.package("B", daniel=True, optipatcher=True))
        self.assertEqual(self.ini(), edited.replace(b"NrBackend=lmxxf", b"NrBackend=daniel")
                         .replace(b"LoadAsiPlugins=auto", b"LoadAsiPlugins=true"))
        self.backend.update(self.game, self.package("C"))
        self.assertEqual(self.ini(), edited)
        self.assertFalse((self.game / "dlssnr_amd_pass1.dll").exists())
        self.assertFalse((self.game / "OptiScaler/plugins/OptiPatcher.asi").exists())

    def test_in_game_runtime_choice_survives_repair_while_files_exist(self):
        package = self.package("A", daniel=True)
        self.backend.install(self.game, package)
        chosen = self.ini().replace(b"NrBackend=daniel", b"NrBackend=lmxxf")
        (self.game / "OptiScaler.ini").write_bytes(chosen)
        (self.game / "dlssnr_amd_pass2.dll").write_bytes(b"damaged")
        self.backend.repair(self.game, package)
        self.assertEqual(self.ini(), chosen)
        self.assertEqual((self.game / "dlssnr_amd_pass2.dll").read_bytes(), b"pass")

    def test_missing_runtime_files_never_leave_daniel_selected(self):
        self.backend.install(self.game, self.package("A"))
        (self.game / "OptiScaler.ini").write_bytes(self.ini().replace(b"NrBackend=lmxxf", b"NrBackend=daniel"))
        self.backend.repair(self.game, self.package("A"))
        self.assertEqual(ini_value(self.ini(), "DlssNr", "NrBackend"), "lmxxf")

    def test_seeds_apply_to_a_fresh_install_only(self):
        seeds = {("Menu", "OverlayMenu"): "false"}
        self.backend.install(self.game, self.package("A"), seeds=seeds)
        self.assertEqual(ini_value(self.ini(), "Menu", "OverlayMenu"), "false")
        (self.game / "OptiScaler.ini").write_bytes(self.ini().replace(b"OverlayMenu=false", b"OverlayMenu=true"))
        self.backend.update(self.game, self.package("B"))
        self.assertEqual(ini_value(self.ini(), "Menu", "OverlayMenu"), "true")

    def test_switching_host_build_starts_from_its_ini_and_keeps_the_old_one(self):
        self.backend.install(self.game, self.package("A"))
        edited = self.ini().replace(b"Enabled=true", b"Enabled=true\r\nUserSetting=kept")
        (self.game / "OptiScaler.ini").write_bytes(edited)
        seeds = {("Menu", "OverlayMenu"): "false"}
        result = self.backend.update(self.game, self.package("B", daniel=True, host=True), seeds=seeds)
        self.assertEqual(result["components"]["host"]["build"], "presr")
        self.assertEqual(result["host_version"], "presr-1.9.1-alpha")
        self.assertTrue(self.ini().startswith(b"; pre-SR"))
        self.assertEqual(ini_value(self.ini(), "Menu", "OverlayMenu"), "false")
        saved = self.root / "state/configs" / (self.backend._key(self.game) + ".ini")
        self.assertEqual(saved.read_bytes(), edited)
        # On the same host, the user's INI is kept as before.
        mine = self.ini() + b"; mine\r\n"
        (self.game / "OptiScaler.ini").write_bytes(mine)
        self.backend.repair(self.game, self.package("B", daniel=True, host=True), seeds=seeds)
        self.assertEqual(self.ini(), mine)
        result = self.backend.update(self.game, self.package("C"))
        self.assertNotIn("host", result["components"])
        self.assertTrue(self.ini().startswith(b"; release comment"))
        self.assertEqual(saved.read_bytes(), mine)
        self.backend.uninstall(self.game)
        self.assertEqual(sorted(path.name for path in self.game.iterdir()), ["save.dat"])

    def test_model_e_switches_the_provider_keys_and_back(self):
        self.backend.install(self.game, self.package("A"))
        edited = self.ini().replace(b"Enabled=true", b"Enabled=true\r\nUserSetting=kept")
        (self.game / "OptiScaler.ini").write_bytes(edited)
        result = self.backend.update(self.game, self.package("B", model_e=True))
        self.assertEqual(result["components"]["model_e"]["version"], "1.8.1")
        self.assertEqual(ini_value(self.ini(), "Upscalers", "FfxSRProvider"), "helix_e")
        self.assertEqual(ini_value(self.ini(), "Libraries", "HelixSRPath"), "AMDNR-SR-E")
        self.assertIn(b"UserSetting=kept", self.ini())
        for name in self.runtimes.MODEL_E_DLLS:
            self.assertTrue((self.game / "AMDNR-SR-E" / name).is_file())
        self.backend.update(self.game, self.package("C"))
        self.assertEqual(ini_value(self.ini(), "Upscalers", "FfxSRProvider"), "auto")
        self.assertEqual(ini_value(self.ini(), "Libraries", "HelixSRPath"), "")
        # The emptied folder goes at uninstall, like OptiScaler/plugins.
        self.assertEqual(list((self.game / "AMDNR-SR-E").iterdir()), [])
        self.backend.update(self.game, self.package("D", model_e=True))
        # HelixSR writes its log beside its DLL; uninstall keeps it and everything it did not install.
        (self.game / "AMDNR-SR-E/helixsr.log").write_text("network running")
        self.backend.uninstall(self.game)
        self.assertEqual(sorted(path.relative_to(self.game).as_posix() for path in self.game.rglob("*")),
                         ["AMDNR-SR-E", "AMDNR-SR-E/helixsr.log", "OptiScaler.ini", "save.dat"]
                         if (self.game / "OptiScaler.ini").exists() else ["AMDNR-SR-E", "AMDNR-SR-E/helixsr.log", "save.dat"])

    def test_model_e_files_must_match_their_description(self):
        package = self.package("A", model_e=True)
        (package / "AMDNR-SR-E/amd_fidelityfx_upscaler_dx12.dll").write_bytes(b"other build")
        with self.assertRaisesRegex(ConflictError, "Model E files do not match"):
            self.backend.install(self.game, package)
        undeclared = self.package("B")
        (undeclared / "AMDNR-SR-E").mkdir()
        (undeclared / "AMDNR-SR-E/amd_fidelityfx_dx12.dll").write_bytes(b"helixsr")
        with self.assertRaisesRegex(ConflictError, "undeclared Model E"):
            self.backend.install(self.game, undeclared)
        self.assertEqual(sorted(path.name for path in self.game.iterdir()), ["save.dat"])
        base = {"neural_runtime": "lmxxf", "daniel": None, "optipatcher": None}
        self.assertTrue(_components(dict(base, model_e={"version": "1.8.1", "sha256": sha(b"x")}))["model_e"])
        host = {"build": "presr", "version": "1.9.1-alpha", "sha256": sha(b"host")}
        for bad in (dict(base, model_e=None), dict(base, model_e={"version": "1.8.1", "sha256": "x"}),
                    dict(base, neural_runtime="daniel", daniel={"version": "0.3.1", "sha256": sha(b"zip")},
                         host=host, model_e={"version": "1.8.1", "sha256": sha(b"x")})):
            with self.subTest(bad), self.assertRaises(ValueError):
                _components(bad)

    def test_model_e_is_passed_to_releases_that_support_it(self):
        seen = []

        def prepare(compiler, archive=None, output=None, cache=None, daniel=None, optipatcher=None, host=None,
                    model_e=False, dlss=None, accept_dlss_license=False, progress=None):
            seen.append((daniel, model_e, accept_dlss_license, callable(progress)))
            raise RuntimeError("stop before staging")
        with patch.object(self.backend, "_find_compiler", return_value=self.root / "compiler.dll"), \
                patch.object(self.backend.setup, "prepare", prepare):
            self.assertTrue(self.backend.supports_model_e())
            with self.assertRaises(RuntimeError):
                self.backend.prepare_package(components={"daniel": None, "model_e": True}, accept_dlss_license=True)
            with self.assertRaises(RuntimeError):
                self.backend.prepare_package(components={"daniel": "0.6.0", "model_e": False})
        self.assertEqual(seen, [(None, True, True, True), ("0.6.0", False, False, False)])

        def older(compiler, archive=None, output=None, cache=None, daniel=None, optipatcher=None, host=None):
            raise AssertionError("must not run")
        with patch.object(self.backend.setup, "prepare", older):
            self.assertFalse(self.backend.supports_model_e())
            self.assertFalse(self.backend.model_e_built())
            with self.assertRaisesRegex(BackendError, "cannot add Model E"):
                self.backend.prepare_package(components={"model_e": True})

    def test_component_metadata_names_only_alternate_hosts(self):
        base = {"neural_runtime": "daniel", "daniel": {"version": "0.3.1", "sha256": sha(b"zip")}, "optipatcher": None}
        host = {"build": "presr", "version": "1.9.1-alpha", "sha256": sha(b"host")}
        self.assertEqual(host_key(_components(dict(base, host=host))), "presr")
        self.assertEqual(host_key(_components(base)), "amdnr")
        self.assertEqual(host_key(None), "amdnr")
        for bad in (dict(base, host=None), dict(base, host=dict(host, build="amdnr")),
                    dict(base, host=dict(host, build="../x")), dict(base, host=dict(host, sha256="x")),
                    dict(base, neural_runtime="lmxxf", daniel=None, host=host), dict(base, extra=1)):
            with self.subTest(bad), self.assertRaises(ValueError):
                _components(bad)

    def test_alternate_host_is_passed_to_releases_that_support_it(self):
        seen = []

        def prepare(compiler, archive=None, output=None, cache=None, daniel=None, optipatcher=None, host=None):
            seen.append((daniel, optipatcher, host))
            raise RuntimeError("stop before staging")
        with patch.object(self.backend, "_find_compiler", return_value=self.root / "compiler.dll"), \
                patch.object(self.backend.setup, "prepare", prepare):
            self.assertTrue(self.backend.supports_host_builds())
            for components in ({"daniel": "0.3.1", "optipatcher": None, "host": "presr"},
                               {"daniel": "0.6.0", "optipatcher": None, "host": "amdnr"}):
                with self.assertRaises(RuntimeError):
                    self.backend.prepare_package(components=components)
        self.assertEqual(seen, [("0.3.1", None, "presr"), ("0.6.0", None, None)])

        def older(compiler, archive=None, output=None, cache=None, daniel=None, optipatcher=None):
            raise AssertionError("must not run")
        with patch.object(self.backend.setup, "prepare", older):
            self.assertFalse(self.backend.supports_host_builds())
            with self.assertRaisesRegex(BackendError, "another host build"):
                self.backend.prepare_package(components={"daniel": "0.3.1", "host": "presr"})

    def test_packages_must_match_their_component_description(self):
        package = self.package("A", daniel=True)
        (package / "dlssnr_amd_pass1.dll").write_bytes(b"other build")
        with self.assertRaisesRegex(ConflictError, "do not match"):
            self.backend.install(self.game, package)
        undeclared = self.package("B")
        (undeclared / "dlssnr_amd_pass1.dll").write_bytes(b"pass")
        with self.assertRaisesRegex(ConflictError, "undeclared"):
            self.backend.install(self.game, undeclared)
        self.assertEqual(sorted(path.name for path in self.game.iterdir()), ["save.dat"])

    def test_old_release_scripts_refuse_components_before_downloading(self):
        def prepare(compiler, archive=None, output=None, cache=None):
            raise AssertionError("must not run")
        with patch.object(self.backend.setup, "prepare", prepare):
            self.assertFalse(self.backend.supports_components())
            with self.assertRaisesRegex(BackendError, "Update the launcher"):
                self.backend.prepare_package(components={"daniel": "0.6.0"})


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="compatibility ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def game(self, appid="", files=()):
        install = self.root / (appid or "manual")
        exe = install / "bin"
        exe.mkdir(parents=True)
        for name in files:
            path = install / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
        return SteamGame(appid, "Game", install, self.root, self.root, exe)

    def test_unsupported_games_and_anti_cheat_ask_for_confirmation(self):
        levels = [item["level"] for item in compatibility.notices(self.game("1245620"))]
        self.assertEqual(levels, ["confirm"])
        protected = compatibility.notices(self.game("1", ["EasyAntiCheat/EasyAntiCheat_x64.dll"]))
        self.assertEqual([item["level"] for item in protected], ["confirm"])
        self.assertIn("Easy Anti-Cheat", protected[0]["text"])
        self.assertEqual(compatibility.notices(self.game("2")), [])

    def test_foreign_installations_warn_unless_the_files_are_ours(self):
        game = self.game("3", ["bin/OptiScaler.ini", "bin/dlssnr_on_amd.ini", "bin/ReShade.ini", "bin/dinput8.dll"])
        levels = [item["level"] for item in compatibility.notices(game)]
        self.assertEqual(levels, ["warning", "warning", "warning", "info"])
        self.assertEqual([item["level"] for item in compatibility.notices(game, managed=True)], ["warning", "info"])
        # An earlier AMDNR installation's INI, which Remove leftover AMDNR files takes away.
        leftover = compatibility.notices(game, leftovers=["OptiScaler.ini"])
        self.assertEqual([item["level"] for item in leftover], ["warning", "warning", "info"])
        self.assertFalse(any("Another OptiScaler" in item["text"] for item in leftover))

    def test_seeds_by_app_id_and_executable(self):
        self.assertEqual(compatibility.seeds(self.game("3240220")), {("Spoofing", "Dxgi"): "false"})
        manual = self.game()
        self.assertEqual(compatibility.seeds(manual, manual.exe_dir / "RE9.exe"),
                         {("Menu", "OverlayMenu"): "false", ("DlssNr", "AmdAdapterList"): "false"})
        self.assertEqual(compatibility.seeds(manual), {})



class ControllerComponentTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import test_native_controller
        self.fixture = test_native_controller.ControllerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.app, self.game = self.fixture.app, self.fixture.game
        rdna4 = {"gfx": "gfx1201", "generation": "rdna4", "apu": False, "simd_count": 128}
        gpu = patch.object(self.app.backend.runtimes, "detect_gpu", return_value=rdna4)
        gpu.start()
        self.addCleanup(gpu.stop)

    def test_choices_follow_gpu_and_host_and_selection_persists(self):
        choices = self.app.inspect(self.game)["component_choices"]
        self.assertTrue(choices["supported"])
        self.assertEqual(choices["runtimes"], ["0.6.0", "0.5.1", "0.5.0", "0.4.3", "0.4.1", "0.4.0"])
        self.assertEqual(choices["selected"], {"daniel": None, "optipatcher": None})
        self.app.set_components(self.game, daniel="0.6.0", optipatcher="0.41")
        self.assertEqual(self.app.components(self.game), {"daniel": "0.6.0", "optipatcher": "0.41"})
        self.app._gpu = {"gfx": "gfx1030", "generation": "rdna2", "apu": False}
        with self.assertRaisesRegex(ValueError, "not offered"):
            self.app.set_components(self.game, daniel="0.5.1")
        self.app._gpu = {"gfx": "gfx1103", "generation": "rdna3", "apu": True}
        self.assertEqual(self.app.component_choices(self.game)["runtimes"], [])

    def test_install_passes_selection_to_preparation(self):
        seen = []
        self.app.set_components(self.game, daniel="0.6.0")
        with patch.object(self.app, "prepare", side_effect=lambda game, backend, components: seen.append(components)
                          or self.fixture.package):
            self.app.operate("install", self.game)
        self.assertEqual(seen, [{"daniel": "0.6.0", "optipatcher": None}])
        self.app._gpu = {"gfx": "gfx1030", "generation": "rdna2", "apu": False}
        self.app.settings["components"][self.app.game_key(self.game)]["daniel"] = "0.5.1"
        with patch.object(self.app, "prepare") as prepare, self.assertRaisesRegex(ValueError, "does not offer"):
            self.app.operate("repair", self.game)
        prepare.assert_not_called()

    def test_alternate_host_offers_only_its_runtimes_and_is_remembered(self):
        choices = self.app.inspect(self.game)["component_choices"]
        self.assertEqual([host["key"] for host in choices["hosts"]], ["amdnr", "presr"])
        self.assertEqual(choices["host_runtimes"]["presr"], ["0.3.1"])
        self.assertNotIn("0.3.1", choices["host_runtimes"]["amdnr"])
        self.assertNotIn("host", choices["selected"])
        with self.assertRaisesRegex(ValueError, "danielblnc's runtime only"):
            self.app.set_components(self.game, host="presr")
        with self.assertRaisesRegex(ValueError, "not offered"):
            self.app.set_components(self.game, daniel="0.6.0", host="presr")
        with self.assertRaisesRegex(ValueError, "not offered"):
            self.app.set_components(self.game, daniel="0.3.1")
        self.app.set_components(self.game, daniel="0.3.1", host="presr")
        self.assertEqual(self.app.components(self.game), {"daniel": "0.3.1", "optipatcher": None, "host": "presr"})
        self.assertEqual(self.app.component_choices(self.game)["runtimes"], ["0.3.1"])
        self.app.set_components(self.game, daniel="0.6.0", host="amdnr")
        self.assertEqual(self.app.components(self.game), {"daniel": "0.6.0", "optipatcher": None})

    def test_host_change_passes_the_host_and_first_install_settings(self):
        seen, seeds = [], {("Menu", "OverlayMenu"): "false"}
        self.app.set_components(self.game, daniel="0.3.1", host="presr")
        with patch.object(self.app, "prepare", side_effect=lambda game, backend, components: seen.append(components)
                          or self.fixture.package), patch.object(compatibility, "seeds", return_value=seeds):
            self.app.operate("install", self.game)
            # The fixture package is an AMDNR one, so the selected host still differs from the installed one.
            with patch.object(self.app.backend, "repair", wraps=self.app.backend.repair) as repair:
                self.app.operate("repair", self.game)
        self.assertEqual(seen, [{"daniel": "0.3.1", "optipatcher": None, "host": "presr"}] * 2)
        self.assertEqual(repair.call_args.kwargs.get("seeds"), seeds)

    def test_model_e_needs_its_notice_accepted_and_the_amdnr_host(self):
        choice = self.app.inspect(self.game)["component_choices"]["model_e"]
        self.assertEqual((choice["helixsr"], choice["dlss"], choice["accepted"], choice["gpu_supported"]),
                         ("1.8.1", "310.7.0", False, True))
        with self.assertRaisesRegex(ValueError, "accept the Model E notice"):
            self.app.set_components(self.game, model_e=True)
        self.app.accept_model_e()
        self.assertTrue(self.app.component_choices(self.game)["model_e"]["accepted"])
        with self.assertRaisesRegex(ValueError, "needs the AMDNR host"):
            self.app.set_components(self.game, daniel="0.3.1", host="presr", model_e=True)
        self.app.set_components(self.game, daniel="0.6.0", model_e=True)
        self.assertEqual(self.app.components(self.game), {"daniel": "0.6.0", "optipatcher": None, "model_e": True})
        seen = []
        from native_launcher import controller
        real = controller.Controller.prepare
        with patch.object(self.app.backend, "prepare_package",
                          side_effect=lambda *args, **options: seen.append(options) or self.fixture.package), \
                patch.object(self.app, "prepare", side_effect=lambda *args: real(self.app, *args)), \
                patch.object(controller, "persistent_python", return_value=None):
            self.app.operate("install", self.game)
        self.assertEqual(seen[0]["components"], {"daniel": "0.6.0", "optipatcher": None, "model_e": True})
        self.assertIs(seen[0]["accept_dlss_license"], True)
        # A newer HelixSR or NVIDIA DLL pin asks again.
        self.app.settings["model_e_consent"]["helixsr"] = "1.7"
        self.assertFalse(self.app.model_e_accepted())
        with patch.object(self.app, "prepare") as prepare, self.assertRaisesRegex(ValueError, "accept the Model E notice"):
            self.app.operate("repair", self.game)
        prepare.assert_not_called()
        self.app._gpu = {"gfx": "gfx906", "generation": "other", "apu": False}
        self.assertFalse(self.app.component_choices(self.game)["model_e"]["gpu_supported"])

    def test_install_seeds_first_install_settings(self):
        seeds = {("Menu", "OverlayMenu"): "false"}
        with patch.object(compatibility, "seeds", return_value=seeds), \
                patch.object(self.app.backend, "install", wraps=self.app.backend.install) as install:
            self.app.operate("install", self.game)
        self.assertEqual(install.call_args.kwargs.get("seeds"), seeds)


if __name__ == "__main__":
    unittest.main()
