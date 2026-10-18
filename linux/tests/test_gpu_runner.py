"""CPU-only checks for unattended GPU runner results and process cleanup."""
import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location("amdnr_gpu_runner", Path(__file__).resolve().parents[1] / "test.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class GpuRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="amdnr-runner-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)

    def run_script(self, script, **kwargs):
        return runner.run([sys.executable, "-c", script], os.environ.copy(), self.work,
                          self.work / "test.log", **kwargs)

    def fixtures(self):
        self.package, self.build, self.proton = (self.work / name for name in ('package', 'build', 'proton'))
        for name in ('LmxxfNrRuntime.dll', 'LmxxfNrRuntime.pak', 'd3d12.dll', 'd3d12core.dll',
                     'd3dcompiler_47.dll', 'amdnr-linux/x86_64-windows/amdhip64_7.dll',
                     'amdnr-linux/x86_64-unix/amdhip64_7.so',
                     'amdnr-linux/x86_64-unix/libamdnr_signal_guard.so'):
            path = self.package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
        self.build.mkdir()
        for name in ('syscall_dispatch.exe', 'hip_smoke.exe', 'exception_smoke.exe',
                     'exception_context.exe', 'compute.hsaco', 'hip_registration.exe',
                     'hip_registration_module.dll', 'registered.hipfb', 'd3d12_interop.exe',
                     'neural_smoke.exe', 'host_shaders.exe'):
            (self.build / name).write_bytes(b'not executed')
        self.output = self.work / 'output'

    def invoke(self, effect):
        argv = ['test.py', '--package', str(self.package), '--build', str(self.build),
                '--proton', str(self.proton), '--output', str(self.output), '--neural-only']
        with patch.object(sys, 'argv', argv), patch.object(runner, 'run', side_effect=effect), \
                patch.object(runner, 'environment', side_effect=lambda bundle, env: env), \
                patch.object(runner, 'prepare_prefix'):
            runner.main()

    @staticmethod
    def passed(command, env, directory, log, **options):
        return {'log': log.name, 'status': 'passed', 'exit_code': 0}

    def test_setup_failure_invalidates_previous_pass_before_copying(self):
        self.fixtures()
        self.output.mkdir()
        report = self.output / 'results.json'
        report.write_text(json.dumps({'status': 'passed', 'old_evidence': True}))

        def failed_copy(*args, **kwargs):
            self.assertEqual(json.loads(report.read_text())['status'], 'running')
            raise OSError('setup copy failed')

        with patch.object(runner.shutil, 'copytree', side_effect=failed_copy):
            with self.assertRaisesRegex(OSError, 'setup copy failed'):
                self.invoke(self.passed)
        result = json.loads(report.read_text())
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('old_evidence', result)
        self.assertEqual(result['tests'], [])

    def test_success_records_bounded_shutdown_and_wait_for_isolated_prefix(self):
        self.fixtures()
        cleanup = []

        def execute(command, env, directory, log, **options):
            if log.name.startswith('wineserver-'):
                cleanup.append(command[-1])
                self.assertEqual(options['timeout'], 15)
                self.assertEqual(env['WINEPREFIX'], str(self.output / 'compat/pfx'))
                self.assertEqual(Path(command[0]), self.proton / 'files/bin/wineserver')
            return self.passed(command, env, directory, log, **options)

        self.invoke(execute)
        result = json.loads((self.output / 'results.json').read_text())
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(cleanup, ['-k', '-w'])
        self.assertEqual([item['log'] for item in result['tests']][-2:],
                         ['wineserver-k.log', 'wineserver-w.log'])

    def test_cleanup_failure_fails_report_and_command_and_still_attempts_wait(self):
        self.fixtures()
        for failed_log in ('wineserver-k.log', 'wineserver-w.log'):
            with self.subTest(failed_log=failed_log):
                calls = []

                def execute(command, env, directory, log, **options):
                    calls.append(log.name)
                    if log.name == failed_log:
                        raise subprocess.TimeoutExpired(command, 15)
                    return self.passed(command, env, directory, log, **options)

                with self.assertRaises(subprocess.TimeoutExpired):
                    self.invoke(execute)
                result = json.loads((self.output / 'results.json').read_text())
                self.assertEqual(result['status'], 'failed')
                self.assertIn('cleanup_error', result)
                self.assertEqual(calls[-2:], ['wineserver-k.log', 'wineserver-w.log'])
                self.assertEqual(next(item for item in result['tests'] if item['log'] == failed_log)['status'], 'failed')

    def test_original_failure_preserved_when_cleanup_also_fails(self):
        self.fixtures()

        def execute(command, env, directory, log, **options):
            if log.name == 'neural_smoke.log':
                raise RuntimeError('original neural failure')
            if log.name.startswith('wineserver-'):
                raise OSError('cleanup failure')
            return self.passed(command, env, directory, log, **options)

        with self.assertRaisesRegex(RuntimeError, 'original neural failure'):
            self.invoke(execute)
        result = json.loads((self.output / 'results.json').read_text())
        self.assertEqual(result['error'], 'original neural failure')
        self.assertEqual(result['cleanup_error'], 'cleanup failure')
        self.assertEqual(result['status'], 'failed')

    def test_optional_runtime_and_plugin_imply_host_pipeline_and_are_checked(self):
        self.fixtures()
        daniel = self.work / 'daniel'
        daniel.mkdir()
        argv = ['test.py', '--package', str(self.package), '--build', str(self.build), '--proton', str(self.proton),
                '--output', str(self.output), '--daniel-runtime', str(daniel)]
        with patch.object(sys, 'argv', argv), patch('sys.stderr'), self.assertRaises(SystemExit):
            runner.main()
        self.assertFalse(self.output.exists())
        for name in runner.DANIEL_FILES:
            (daniel / name).write_bytes(name.encode())
        seen = {}

        def capture(args, package, build, work, proton):
            seen.update(host_pipeline=args.host_pipeline, daniel=args.daniel_runtime)
        with patch.object(sys, 'argv', argv), patch.object(runner, 'run_suite', side_effect=capture):
            runner.main()
        self.assertEqual(seen, {'host_pipeline': True, 'daniel': daniel.resolve()})
        with patch.object(sys, 'argv', argv[:-2] + ['--optipatcher', str(self.work / 'missing.asi')]), \
                patch('sys.stderr'), self.assertRaises(SystemExit):
            runner.main()

    def test_package_without_lmxxf_runs_only_danielblnc_host_checks(self):
        self.fixtures()
        for name in runner.LMXXF_FILES:
            (self.package / name).unlink()
        (self.package / 'winmm.dll').write_bytes(b'alternate host')
        (self.package / 'OptiScaler').mkdir()
        (self.package / 'OptiScaler.ini').write_text(
            '[Log]\nLogToFile=true\nLogLevel=2\n[Menu]\nOverlayMenu=auto\n[Spoofing]\nDxgi=auto\nVulkan=auto\n'
            '[Hotfix]\nCheckForUpdate=auto\n[Upscalers]\nDx12Upscaler=ffx\n[FrameGen]\nEnabled=false\n'
            '[DLSS]\nEnabled=auto\n[OutputScaling]\nEnabled=auto\n[DlssNr]\nEnabled=false\nNrBackend=lmxxf\n')
        (self.build / 'host_neural.exe').write_bytes(b'not executed')
        daniel = self.work / 'daniel'
        daniel.mkdir()
        for name in runner.DANIEL_FILES:
            (daniel / name).write_bytes(name.encode())
        base = ['test.py', '--package', str(self.package), '--build', str(self.build), '--proton', str(self.proton),
                '--output', str(self.output)]
        for extra in (['--host-pipeline'], ['--with-host', '--daniel-runtime', str(daniel)]):
            with self.subTest(extra), patch.object(sys, 'argv', base + extra), patch('sys.stderr'), \
                    self.assertRaises(SystemExit):
                runner.main()
        ran = []

        def record(command, env, directory, log, **options):
            if Path(command[1]).name == 'host_neural.exe':
                ini = (directory / 'OptiScaler.ini').read_text()
                ran.append((command[3], re.search(r'(?m)^NrBackend=(\w+)$', ini)[1],
                            re.search(r'(?m)^Dx12Upscaler=(\w+)$', ini)[1]))
            else:
                ran.append(Path(command[1]).name)
            return self.passed(command, env, directory, log, **options)
        with patch.object(sys, 'argv', base + ['--daniel-runtime', str(daniel)]), \
                patch.object(runner, 'run', side_effect=record), \
                patch.object(runner, 'environment', side_effect=lambda bundle, env: env), \
                patch.object(runner, 'prepare_prefix'):
            runner.main()
        self.assertNotIn('neural_smoke.exe', ran)
        self.assertEqual([item for item in ran if isinstance(item, tuple)],
                         [('baseline', 'daniel', 'fsr21'), ('neural', 'daniel', 'fsr21')])
        report = json.loads((self.output / 'results.json').read_text())
        self.assertEqual(report['status'], 'passed_with_skips')
        self.assertIn({'log': 'neural_smoke.log', 'status': 'skipped'},
                      [{key: test[key] for key in ('log', 'status')} for test in report['tests']])
        self.assertIn('winmm.dll', report['binaries'])
        self.assertFalse((self.output / 'LmxxfNrRuntime.dll').exists())

    def test_same_output_cannot_be_reused_while_another_run_holds_lock(self):
        self.fixtures()
        self.output.mkdir()
        report = self.output / 'results.json'
        report.write_text('{"status":"running","owner":"first run"}')
        before = report.read_bytes()
        with (self.output / '.gpu-test.lock').open('a') as lock:
            runner.fcntl.flock(lock, runner.fcntl.LOCK_EX | runner.fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, 'Another GPU test'):
                self.invoke(self.passed)
        self.assertEqual(report.read_bytes(), before)

    def test_inherited_host_is_removed_and_selected_optimization_flags_are_recorded(self):
        self.fixtures()

        def execute(command, env, directory, log, **options):
            self.assertNotIn('AMDNR_TEST_HOST', env)
            self.assertEqual(env['AMDNR_L37'], '1')
            return self.passed(command, env, directory, log, **options)

        with patch.dict(os.environ, {'AMDNR_TEST_HOST': '/private/unintended.dll', 'AMDNR_L37': '1',
                                     'AMDNR_PRIVATE_PATH': '/not-for-report'}):
            self.invoke(execute)
        result = json.loads((self.output / 'results.json').read_text())
        self.assertEqual(result['optimization_environment']['AMDNR_L37'], '1')
        self.assertNotIn('AMDNR_PRIVATE_PATH', result['optimization_environment'])

    def test_host_fixture_reuse_is_refused_without_deleting_previous_files(self):
        self.fixtures()
        self.output.mkdir()
        host = self.output / 'winmm.dll'
        host.write_bytes(b'previous host fixture')
        with self.assertRaisesRegex(ValueError, 'fresh --output'):
            self.invoke(self.passed)
        self.assertEqual(host.read_bytes(), b'previous host fixture')
        self.assertEqual(json.loads((self.output / 'results.json').read_text())['status'], 'failed')

    def test_success_requires_marker_not_just_exit_zero(self):
        with self.assertRaisesRegex(RuntimeError, "Test failed"):
            self.run_script("print('completed without validation')", expect_pass=True)
        result = self.run_script("print('PASS: checked output')", expect_pass=True)
        self.assertEqual(result["status"], "passed")

    def test_skip_is_distinct_and_requires_skip_exit_and_marker(self):
        result = self.run_script("print('SKIP: unsupported ABI'); raise SystemExit(77)",
                                 expect_pass=True, allow_skip=True)
        self.assertEqual(result["status"], "skipped")
        for script in ("raise SystemExit(77)", "print('SKIP: unsupported ABI')"):
            with self.assertRaises(RuntimeError):
                self.run_script(script, expect_pass=True, allow_skip=True)

    def test_benchmark_data_is_collected_only_after_success(self):
        script = 'print(\'BENCHMARK: {"metric":"serialized_pipeline_readback_ms","mean_ms":12.5}\'); print("PASS: output")'
        result = self.run_script(script, expect_pass=True)
        self.assertEqual(result["benchmarks"][0]["mean_ms"], 12.5)
        with self.assertRaises(RuntimeError):
            self.run_script(script + "; raise SystemExit(1)", expect_pass=True)

    def test_subcheck_skip_does_not_masquerade_as_complete_pass(self):
        result = self.run_script("print('SKIP: shared fence rewind unavailable'); print('PASS: remaining checks')",
                                 expect_pass=True)
        self.assertEqual(result['status'], 'passed_with_skips')
        self.assertEqual(result['skips'], ['SKIP: shared fence rewind unavailable'])
        with self.assertRaises(RuntimeError):
            self.run_script("print('diagnostic contains PASS: but no success marker')", expect_pass=True)

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux process groups")
    def test_timeout_terminates_child_even_when_leader_exits_on_term(self):
        child = ("import os, signal, time; from pathlib import Path; "
                 "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                 "Path('child.pid').write_text(str(os.getpid())); time.sleep(60)")
        script = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(60)"
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_script(script, timeout=1)
        pid = int((self.work / "child.pid").read_text())
        for _ in range(50):
            try:
                state = (Path("/proc") / str(pid) / "stat").read_text().split(")", 1)[1].split()[0]
            except FileNotFoundError:
                return
            if state == "Z":
                return  # Reaped by the OS's subreaper; no executable work remains.
            time.sleep(.02)
        self.fail("Timed-out command left its child running")


if __name__ == "__main__":
    unittest.main()
