#!/usr/bin/env python3
"""CPU-only regression tests for the native COMGR/Wine signal guard.

The small fixture libraries have the same module names as COMGR and Wine but
need neither runtime. Build products live entirely in a temporary directory.
"""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


LINUX = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "signal_guard"


@unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux ELF interposition")
class SignalGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shlex.split(os.environ.get("CC", "cc"))
        if not compiler or not shutil.which(compiler[0]):
            raise unittest.SkipTest("a native C compiler is required")
        temporary = tempfile.TemporaryDirectory(prefix="amdnr-signal-guard-")
        cls.addClassCleanup(temporary.cleanup)
        cls.build = Path(temporary.name)
        flags = ["-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror"]

        def compile_file(source, output, *extra):
            result = subprocess.run(
                compiler + flags + [str(source), "-o", str(cls.build / output)] + list(extra),
                text=True, capture_output=True, timeout=60,
            )
            if result.returncode:
                raise AssertionError(f"Failed to compile {source.name}:\n{result.stdout}{result.stderr}")

        compile_file(LINUX / "bridge/signal_guard.c", "libamdnr_signal_guard.so",
                     "-shared", "-fPIC", "-ldl", "-lpthread")
        compile_file(FIXTURES / "wine.c", "ntdll.so", "-shared", "-fPIC")
        # The guard intentionally checks the immediate calling module. Keep
        # fixture wrappers as real calls, including their restoration path.
        compile_file(FIXTURES / "comgr.c", "libamd_comgr.so.3", "-shared", "-fPIC",
                     "-fno-optimize-sibling-calls")
        compile_file(FIXTURES / "loader_audit.c", "libloader_audit.so",
                     "-shared", "-fPIC", "-ldl")
        compile_file(FIXTURES / "behavior.c", "behavior",
                     str(cls.build / "libamd_comgr.so.3"), str(cls.build / "ntdll.so"),
                     str(cls.build / "libloader_audit.so"), "-Wl,-rpath,$ORIGIN")

    def run_case(self, name):
        env = os.environ.copy()
        env["LD_PRELOAD"] = ":".join(str(self.build / lib) for lib in
                                   ("libamdnr_signal_guard.so", "libloader_audit.so"))
        env.pop("AMDNR_SIGNAL_TRACE", None)
        result = subprocess.run([str(self.build / "behavior"), name], env=env,
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0,
                         f"{name} failed ({result.returncode}):\n{result.stdout}{result.stderr}")
        self.assertIn("PASS", result.stdout)

    def test_wine_handler_retained_and_normal_restoration(self):
        self.run_case("retention")

    def test_queries_and_invalid_signal_preserve_libc_behavior(self):
        self.run_case("queries")

    def test_unrelated_callers_and_non_wine_handlers_are_unaffected(self):
        self.run_case("scope")

    def test_requested_and_previous_action_can_reuse_storage(self):
        self.run_case("alias")

    def test_signal_handler_restoration_does_not_enter_dynamic_loader(self):
        self.run_case("signal_restore")


if __name__ == "__main__":
    unittest.main()
