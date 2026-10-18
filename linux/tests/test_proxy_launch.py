"""Exercise the installed shell/Python wrapper without Wine, games, or a GPU."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import launch
import proxies


def override_map(text):
    """Wine override groups are applied in order; later names win."""
    result = {}
    for group in text.split(";"):
        if "=" not in group:
            continue
        names, mode = group.split("=", 1)
        result.update((name.strip().lower(), mode) for name in names.split(","))
    return result


class ProxyLaunchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="proxy launch with spaces ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.game = self.root / "A Game with Spaces"
        self.bundle = self.game / "amdnr-linux"
        self.bundle.mkdir(parents=True)
        for name in ("launch.py", "proxies.py", "run.sh"):
            shutil.copy2(ROOT / name, self.bundle / name)
        (self.bundle / "run.sh").chmod(0o755)
        for name in ("rocm/lib/libamdhip64.so.7", "x86_64-unix/libamdnr_signal_guard.so",
                     "x86_64-windows/amdhip64_7.dll"):
            path = self.bundle / name
            path.parent.mkdir(parents=True, exist_ok=True)
            # These files are never loaded as code. The Unix loader ignores the
            # fake preload for the capture process, with a captured diagnostic.
            path.write_bytes(b"test fixture, not executable code")
        (self.game / "d3dcompiler_47.dll").write_bytes(b"compiler fixture")
        self.prefix = self.root / "isolated prefix"
        self.capture = self.root / "captured invocation.json"
        self.runner = self.root / "capture runner.py"
        self.runner.write_text(
            "import json, os, pathlib, sys\n"
            "pathlib.Path(sys.argv[1]).write_text(json.dumps({'args': sys.argv[2:], 'env': dict(os.environ)}))\n"
        )
        self.env = os.environ.copy()
        for name in ("LD_PRELOAD", "LD_LIBRARY_PATH", "WINEDLLPATH", "WINEDLLOVERRIDES",
                     "STEAM_COMPAT_DATA_PATH", "WINEPREFIX", "PYTHONPATH", "PYTHONHOME"):
            self.env.pop(name, None)
        self.env["WINEPREFIX"] = str(self.prefix)

    def select(self, proxy):
        (self.bundle / "proxy.json").write_text(json.dumps({"proxy": proxy}))

    def invoke(self, *args):
        return subprocess.run([str(self.bundle / "run.sh"), sys.executable, str(self.runner), str(self.capture), *args],
                              env=self.env, cwd=self.root, capture_output=True, text=True, timeout=10)

    def test_selected_proxy_override_and_existing_settings_are_preserved(self):
        original = {"WINEDLLOVERRIDES": "dinput8=n,b;steam_api64=n;version=b;nvapi,nvapi64=d",
                    "LD_PRELOAD": "user-overlay.so", "LD_LIBRARY_PATH": "/user libraries",
                    "WINEDLLPATH": "/user Wine libraries", "USER_SETTING": "preserve"}
        for selected in proxies.SUPPORTED:
            with self.subTest(proxy=selected):
                self.select(selected)
                before = original.copy()
                configured = launch.environment(self.bundle, original)
                self.assertEqual(original, before)
                self.assertTrue(configured["WINEDLLOVERRIDES"].startswith(original["WINEDLLOVERRIDES"] + ";"))
                effective = override_map(configured["WINEDLLOVERRIDES"])
                self.assertEqual(effective[selected.removesuffix(".dll")], "n,b")
                self.assertEqual(effective["dinput8"], "n,b")
                self.assertEqual(effective["steam_api64"], "n")
                self.assertEqual(effective["nvapi"], "d")
                self.assertEqual(effective["nvapi64"], "d")
                self.assertEqual(effective["dxgi"], "n")
                self.assertEqual(effective["d3d12"], "n,b")
                self.assertEqual(effective["amdhip64_7"], "b")
                self.assertEqual(configured["LD_PRELOAD"], "libamdnr_signal_guard.so:user-overlay.so")
                self.assertTrue(configured["LD_LIBRARY_PATH"].endswith(":" + original["LD_LIBRARY_PATH"]))
                self.assertTrue(configured["WINEDLLPATH"].endswith(":" + original["WINEDLLPATH"]))
                self.assertEqual(configured["USER_SETTING"], "preserve")

    def test_shell_wrapper_preserves_spaces_and_literal_arguments(self):
        self.select("version.dll")
        self.env["WINEDLLOVERRIDES"] = "dinput8=n,b;winmm=b"
        arguments = ("first argument with spaces", "", "literal $HOME; $(not-a-command)", "--setting=value with spaces")
        result = self.invoke(*arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        captured = json.loads(self.capture.read_text())
        self.assertEqual(captured["args"], list(arguments))
        effective = override_map(captured["env"]["WINEDLLOVERRIDES"])
        self.assertEqual(effective["version"], "n,b")
        self.assertEqual(effective["winmm"], "b")
        self.assertEqual(effective["dinput8"], "n,b")
        self.assertEqual(captured["env"]["AMDNR_HIP_LIBRARY"], str(self.bundle / "rocm/lib/libamdhip64.so.7"))
        self.assertEqual(captured["env"]["LD_PRELOAD"], "libamdnr_signal_guard.so")
        self.assertTrue((self.prefix / "drive_c/windows/system32/amdhip64_7.dll").is_file())

    def test_missing_proxy_configuration_preserves_legacy_winmm_behavior(self):
        result = self.invoke("legacy installation")
        self.assertEqual(result.returncode, 0, result.stderr)
        captured = json.loads(self.capture.read_text())
        effective = override_map(captured["env"]["WINEDLLOVERRIDES"])
        self.assertEqual(effective["winmm"], "n,b")
        self.assertNotIn("version", effective)
        self.assertFalse((self.bundle / "proxy.json").exists())

    def assert_rejected_before_execution(self):
        result = self.invoke("must not execute")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid proxy configuration", result.stderr)
        self.assertFalse(self.capture.exists())
        self.assertFalse(self.prefix.exists())
        self.assertFalse((self.bundle / "prefixes.json").exists())
        self.assertFalse((self.bundle / "prefixes.lock").exists())
        self.assertIn("Launch setup failed", (self.bundle / "launch.log").read_text())

    def test_malformed_configuration_fails_before_prefix_changes_or_execution(self):
        for content in (b"", b"not json", b"[]", b"{}", b'{"proxy":null}', b'{"proxy":123}',
                        b'{"proxy":[]}', b'{"proxy":"version.dll","extra":true}',
                        b'{"proxy":"version.dll","proxy":"winmm.dll"}',
                        b'{"proxy":"VERSION.dll"}', b'{"proxy":"version"}', b'\xff', b" " * 4097):
            with self.subTest(content=content[:80]):
                (self.bundle / "proxy.json").write_bytes(content)
                self.assert_rejected_before_execution()

    def test_reserved_or_path_proxy_names_fail_before_execution(self):
        for name in ("dxgi.dll", "d3d12.dll", "d3d12core.dll", "d3d11.dll", "amdhip64_7.dll",
                     "kernel32.dll", "../version.dll", "/tmp/version.dll", "subdir\\winmm.dll"):
            with self.subTest(name=name):
                self.select(name)
                self.assert_rejected_before_execution()

    def test_symlink_and_special_file_configuration_fail_without_blocking(self):
        path = self.bundle / "proxy.json"
        target = self.root / "outside configuration.json"
        target.write_text('{"proxy":"version.dll"}')
        path.symlink_to(target)
        self.assert_rejected_before_execution()
        path.unlink()
        path.mkdir()
        self.assert_rejected_before_execution()
        path.rmdir()
        os.mkfifo(path)
        self.assert_rejected_before_execution()
        path.unlink()
        self.assertEqual(target.read_text(), '{"proxy":"version.dll"}')


if __name__ == "__main__":
    unittest.main()
