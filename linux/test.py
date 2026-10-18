#!/usr/bin/env python3
"""Run the real GPU tests in an isolated Proton prefix. Requires an RX 9070-series GPU."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time
from launch import environment, prepare_prefix, restore_prefixes

ROOT = Path(__file__).resolve().parent
DANIEL_FILES = ("dlssnr_amd_pass1.dll", "dlssnr_amd_pass2.dll", "dlssnr_amd_pass3.dll", "dlssnr_on_amd_weights.bin")
LMXXF_FILES = ("LmxxfNrRuntime.dll", "LmxxfNrRuntime.pak")
OPTIMIZATION_ENVIRONMENT = ("AMDNR_L37", "AMDNR_L39", "AMDNR_L41", "AMDNR_C32W",
                            "AMDNR_FASTK", "AMDNR_FAST_HISTORY", "AMDNR_LOOSE_ASSETS",
                            "AMDNR_HIP_TRACE", "AMDNR_SIGNAL_TRACE")

def windows(path):
    return "Z:" + str(path.resolve()).replace("/", "\\")

def write_report(path, report):
    with tempfile.NamedTemporaryFile(mode="w", prefix=".results-", dir=path.parent,
                                     encoding="utf-8", delete=False) as output:
        pending = Path(output.name)
        try:
            json.dump(report, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
            pending.replace(path)
        finally:
            pending.unlink(missing_ok=True)

def run(command, env, directory, log, timeout=180, expect_pass=False, allow_skip=False):
    started = time.monotonic()
    with log.open("w") as output:
        # Wine helpers can outlive their parent. Give each invocation its own
        # process group so a timeout cannot leave a GPU workload behind.
        process = subprocess.Popen([str(x) for x in command], env=env, cwd=directory,
                                   stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            process.wait(timeout=timeout)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
            finally:
                # The group leader may exit before a stubborn child does.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            raise
    text = log.read_text(errors="replace")
    result = {"log": log.name, "exit_code": process.returncode,
              "elapsed_seconds": round(time.monotonic() - started, 6)}
    passes = [line for line in text.splitlines() if line.startswith("PASS:")]
    skips = [line for line in text.splitlines() if line.startswith("SKIP:")]
    if allow_skip and process.returncode == 77 and skips:
        print(skips[-1], flush=True)
        return dict(result, status="skipped", skips=skips)
    if process.returncode or (expect_pass and not passes):
        raise RuntimeError(f"Test failed (exit {process.returncode}): {log}\n" + "\n".join(text.splitlines()[-25:]))
    for skip in skips:
        print(skip, flush=True)
    if expect_pass:
        print(passes[-1], flush=True)
    benchmarks = [json.loads(line.removeprefix("BENCHMARK: "))
                  for line in text.splitlines() if line.startswith("BENCHMARK: ")]
    return dict(result, status="passed_with_skips" if skips else "passed", benchmarks=benchmarks, skips=skips)

def host_config(source, destination, settings):
    """Change only explicit settings in an isolated copy of the release INI."""
    lines = source.read_text(encoding="utf-8-sig").splitlines()
    section, seen = "", set()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
        elif "=" in stripped and not stripped.startswith((";", "#")):
            key = (section, stripped.split("=", 1)[0].strip())
            if key in settings:
                lines[index] = key[1] + "=" + settings[key]
                seen.add(key)
    if seen != settings.keys():
        raise ValueError(f"Host test settings missing from package: {settings.keys() - seen}")
    destination.write_text("\n".join(lines) + "\n")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proton", type=Path, required=True, help="Proton installation directory")
    parser.add_argument("--package", type=Path, required=True, help="Output of manage.py stage")
    parser.add_argument("--build", type=Path, default=ROOT / "build")
    parser.add_argument("--output", type=Path, default=ROOT / "build/test-run")
    parser.add_argument("--frames", type=int, default=60)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=360)
    parser.add_argument("--full-network", action="store_true")
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--controls", action="store_true")
    parser.add_argument("--benchmark", action="store_true",
                        help="Measure serialized neural pipeline + readback, excluding 10 warmup frames; not game FPS")
    parser.add_argument("--neural-only", action="store_true",
                        help="Run only neural tests, for repeated measurements after a full validation pass")
    parser.add_argument("--with-host", action="store_true",
                        help="Also run inference with the released OptiScaler graphics hooks and GPU spoofing")
    parser.add_argument("--host-pipeline", action="store_true",
                        help="Also compare NR off/on through the released host's full NGX/FSR2 pipeline")
    parser.add_argument("--daniel-runtime", type=Path,
                        help="Folder with danielblnc's dlssnr_amd_pass1..3.dll and dlssnr_on_amd_weights.bin; "
                             "adds a host-pipeline run with NrBackend=daniel (implies --host-pipeline)")
    parser.add_argument("--optipatcher", type=Path, metavar="ASI",
                        help="OptiPatcher plugin; host runs load it from OptiScaler/plugins and must log it (implies --host-pipeline)")
    args = parser.parse_args()
    if args.daniel_runtime or args.optipatcher:
        args.host_pipeline = True
    if not 3 <= args.frames <= 10000:
        parser.error("--frames must be between 3 and 10000")
    if args.benchmark and args.frames < 30:
        parser.error("--benchmark requires at least 30 frames")
    if not 64 <= args.width <= 1920 or not 64 <= args.height <= 1080:
        parser.error("Image size must be between 64x64 and 1920x1080")
    package, build, work, proton = (p.resolve() for p in (args.package, args.build, args.output, args.proton))
    # Alternate host packages (pre-SR) carry no lmxxf runtime; they are tested with danielblnc's.
    args.lmxxf = all((package / name).is_file() for name in LMXXF_FILES)
    if not args.lmxxf and args.with_host:
        parser.error("--with-host needs a package with the lmxxf runtime")
    if not args.lmxxf and args.host_pipeline and not args.daniel_runtime:
        parser.error("This package has no lmxxf runtime; --host-pipeline needs --daniel-runtime")
    if args.daniel_runtime:
        args.daniel_runtime = args.daniel_runtime.resolve()
        missing = [name for name in DANIEL_FILES if not (args.daniel_runtime / name).is_file()]
        if missing:
            parser.error("--daniel-runtime is missing " + ", ".join(missing))
    if args.optipatcher:
        args.optipatcher = args.optipatcher.resolve()
        if not args.optipatcher.is_file():
            parser.error("--optipatcher must name the plugin file")
    work.mkdir(parents=True, exist_ok=True)
    # A second run in the same directory must not replace evidence or restore
    # prefix files while the first run still owns that isolated environment.
    with (work / ".gpu-test.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another GPU test is using this output directory") from error
        run_suite(args, package, build, work, proton)


def run_suite(args, package, build, work, proton):
    report = {"created_utc": datetime.now(timezone.utc).isoformat(), "status": "running",
              "proton": str(proton), "package": str(package),
              "proton_version": "unknown",
              "settings": {key: value for key, value in vars(args).items() if not isinstance(value, Path)},
              "optimization_environment": {name: os.environ.get(name) for name in OPTIMIZATION_ENVIRONMENT},
              "binaries": {}, "tests": []}
    # Invalidate old passing evidence before any copy, hash, or prefix work.
    write_report(work / "results.json", report)
    compat = work / "compat"
    prefix = compat / "pfx"
    env = os.environ.copy()
    env.pop("AMDNR_TEST_HOST", None)  # Host loading is enabled only by the explicit subtest.
    env.update(STEAM_COMPAT_DATA_PATH=str(compat), SteamAppId="0", SteamGameId="0",
               STEAM_COMPAT_CLIENT_INSTALL_PATH=env.get("STEAM_COMPAT_CLIENT_INSTALL_PATH", str(Path.home() / ".local/share/Steam")),
               WINEDEBUG="-all", VKD3D_DEBUG="none", DXVK_LOG_LEVEL="error", VKD3D_SHADER_CACHE_PATH=str(work))
    # Set the isolated prefix before initialization so even failed startup is
    # cleaned up without touching an unrelated game/server from the parent env.
    env.update(WINEPREFIX=str(prefix), WINESERVER=str(proton / "files/bin/wineserver"))
    def execute(command, run_env, directory, log, **options):
        try:
            result = run(command, run_env, directory, log, **options)
            if args.benchmark and log.name in ("neural_smoke.log", "neural_host.log") and not result.get("benchmarks"):
                raise RuntimeError("Benchmark marker missing; rebuild neural_smoke.exe with linux/build-tests.sh")
        except BaseException as error:
            report["tests"].append({"log": log.name, "status": "failed", "error": str(error)})
            raise
        report["tests"].append(result)
        return result
    wine = proton / "files/bin/wine"
    prefix_started = False
    original_error = None
    try:
        if not (args.with_host or args.host_pipeline) and any(
                (work / name).exists() or (work / name).is_symlink()
                for name in ("winmm.dll", "LmxxfNrRuntime.dll", "LmxxfNrRuntime.pak", "OptiScaler.ini", "OptiScaler",
                             *DANIEL_FILES)):
            raise ValueError("This output directory contains host-test fixtures. Use a fresh --output for tests without the host.")
        bundle = work / "amdnr-linux"
        if bundle.exists():
            restore_prefixes(bundle)
            # Old test bundles used an ELF builtin. Do not retain a fallback that
            # would disable Wine syscall dispatch when testing the PE/Unixlib bridge.
            (bundle / "x86_64-unix/amdhip64_7.dll.so").unlink(missing_ok=True)
        shutil.copytree(package / "amdnr-linux", bundle, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("prefixes.*", "prefix-backups"))
        for name in ("d3d12.dll", "d3d12core.dll", "d3dcompiler_47.dll"):
            shutil.copy2(package / name, work / name)
        if args.with_host or args.host_pipeline:
            for name in ("winmm.dll", *(LMXXF_FILES if args.lmxxf else ())):
                shutil.copy2(package / name, work / name)
            for folder in ("OptiScaler", "experimental_lighting"):
                if (package / folder).is_dir():
                    shutil.copytree(package / folder, work / folder, dirs_exist_ok=True)
            # An explicit NrBackend selects the runtime, so both sets can share the folder.
            for name in DANIEL_FILES if args.daniel_runtime else ():
                shutil.copy2(args.daniel_runtime / name, work / name)
            # Configure only the isolated test copy; force the paths that games use
            # even though the harness has no NGX library for host autodetection.
            settings = {("Log", "LogToFile"): "true", ("Log", "LogLevel"): "1",
                        ("Menu", "OverlayMenu"): "true", ("Spoofing", "Dxgi"): "true",
                        ("Spoofing", "Vulkan"): "true", ("Hotfix", "CheckForUpdate"): "false"}
            if args.optipatcher:
                (work / "OptiScaler/plugins").mkdir(parents=True, exist_ok=True)
                shutil.copy2(args.optipatcher, work / "OptiScaler/plugins/OptiPatcher.asi")
                settings[("Plugins", "LoadAsiPlugins")] = "true"
            host_config(package / "OptiScaler.ini", work / "OptiScaler.ini", settings)
        for name in ("syscall_dispatch.exe", "hip_smoke.exe", "exception_smoke.exe", "exception_context.exe", "compute.hsaco",
                     "hip_registration.exe", "hip_registration_module.dll", "registered.hipfb",
                     "d3d12_interop.exe", "neural_smoke.exe", "host_shaders.exe"):
            shutil.copy2(build / name, work / name)
        if args.host_pipeline:
            shutil.copy2(build / "host_neural.exe", work / "host_neural.exe")
        compat.mkdir(exist_ok=True)
        report["proton_version"] = (proton / "version").read_text().strip() if (proton / "version").is_file() else "unknown"
        for name in (*(LMXXF_FILES if args.lmxxf else ("winmm.dll",)), "d3d12.dll", "d3d12core.dll",
                     "amdnr-linux/x86_64-windows/amdhip64_7.dll", "amdnr-linux/x86_64-unix/amdhip64_7.so",
                     "amdnr-linux/x86_64-unix/libamdnr_signal_guard.so"):
            with (package / name).open("rb") as binary:
                report["binaries"][name] = hashlib.file_digest(binary, "sha256").hexdigest()
        for name in DANIEL_FILES if args.daniel_runtime else ():
            with (args.daniel_runtime / name).open("rb") as binary:
                report["binaries"]["daniel/" + name] = hashlib.file_digest(binary, "sha256").hexdigest()
        if args.optipatcher:
            with args.optipatcher.open("rb") as binary:
                report["binaries"]["OptiScaler/plugins/OptiPatcher.asi"] = hashlib.file_digest(binary, "sha256").hexdigest()
        prefix_started = True
        execute([proton / "proton", "run", "cmd.exe", "/c", "exit"], env, work, work / "prefix.log")
        lib = proton / ("files/lib64" if (proton / "files/lib64").is_dir() else "files/lib")
        env["LD_LIBRARY_PATH"] = str(lib) + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
        env = environment(bundle, env)
        env["AMDNR_TEST_FULL_NETWORK"] = "1" if args.full_network else "0"
        env["AMDNR_TEST_HISTORY"] = "1" if args.history else "0"
        env["AMDNR_TEST_CONTROLS"] = "1" if args.controls else "0"
        env["AMDNR_TEST_BENCHMARK"] = "1" if args.benchmark else "0"
        prepare_prefix(bundle, prefix)
        if not args.neural_only:
            execute([wine, work / "syscall_dispatch.exe"], env, work, work / "syscall_dispatch.log",
                    expect_pass=True, allow_skip=True)
        cases = (
            ("exception_smoke", []),
            ("exception_context", [windows(work / "compute.hsaco")]),
            ("hip_smoke", [windows(work / "compute.hsaco")]),
            ("hip_registration", [windows(work / "registered.hipfb")]),
            ("d3d12_interop", []),
            ("host_shaders", []),
            ("neural_smoke", [windows(package / "LmxxfNrRuntime.dll"), windows(package / "LmxxfNrRuntime.pak"),
                              str(args.frames), str(args.width), str(args.height)]),
        )
        for name, arguments in cases:
            if args.neural_only and name != "neural_smoke":
                continue
            if name == "neural_smoke" and not args.lmxxf:
                skip = "SKIP: neural_smoke needs the lmxxf runtime, which this host package does not carry"
                print(skip, flush=True)
                report["tests"].append({"log": name + ".log", "status": "skipped", "skips": [skip]})
                continue
            execute([wine, work / (name + ".exe"), *arguments], env, work, work / (name + ".log"), expect_pass=True)
        if args.with_host:
            host_env = dict(env, AMDNR_TEST_HOST=windows(work / "winmm.dll"))
            (work / "OptiScaler.log").unlink(missing_ok=True)
            execute([wine, work / "neural_smoke.exe", windows(work / "LmxxfNrRuntime.dll"),
                 windows(work / "LmxxfNrRuntime.pak"), str(args.frames), str(args.width), str(args.height)],
                host_env, work, work / "neural_host.log", expect_pass=True)
            host_log = (work / "OptiScaler.log").read_text(errors="replace")
            devices = re.findall(r"D3D12 device captured: ([0-9a-fA-F]+)", host_log)
            if not devices:
                raise RuntimeError("Host test completed without evidence of active D3D12 hooks; see OptiScaler.log")
            if len(set(devices)) != 1:
                raise RuntimeError("Host captured different D3D12 devices during shared imports; see OptiScaler.log")
            shutil.copy2(work / "OptiScaler.log", work / "OptiScaler-host-hooks.log")
        if args.host_pipeline:
            settings.update({("Upscalers", "Dx12Upscaler"): "fsr21", ("FrameGen", "Enabled"): "false",
                             ("DLSS", "Enabled"): "false", ("OutputScaling", "Enabled"): "false",
                             ("DlssNr", "NrBackend"): "lmxxf"})
            runs = [("baseline", "lmxxf"), ("neural", "lmxxf")] if args.lmxxf else [("baseline", "daniel")]
            if args.daniel_runtime:
                runs.append(("daniel", "daniel"))
            logs = ("OptiScaler.log", "lmxxf_backend.log", "amd_bridge.log", "amd_presr.log", "dlssnr_on_amd.log")
            for label, backend in runs:
                mode = "baseline" if label == "baseline" else "neural"
                settings[("DlssNr", "Enabled")] = "true" if mode == "neural" else "false"
                settings[("DlssNr", "NrBackend")] = backend
                host_config(package / "OptiScaler.ini", work / "OptiScaler.ini", settings)
                for name in logs:
                    (work / name).unlink(missing_ok=True)
                reference = windows(work / "host-baseline.bin") if mode == "neural" else "-"
                execute([wine, work / "host_neural.exe", windows(work / "winmm.dll"), mode,
                     windows(work / f"host-{label}.bin"), reference], env, work,
                    work / f"host-pipeline-{label}.log", expect_pass=True)
                for name in logs:
                    if (work / name).exists():
                        shutil.copy2(work / name, work / f"{Path(name).stem}-host-{label}.log")
                if args.optipatcher:
                    # The released host searches OptiScaler/plugins beside itself.
                    loaded = re.search(r"Loaded: .*OptiScaler[\\/]plugins[\\/]OptiPatcher\.asi",
                                       (work / "OptiScaler.log").read_text(errors="replace"))
                    if not loaded:
                        raise RuntimeError(f"Host did not load OptiScaler/plugins/OptiPatcher.asi during the {label} run; see OptiScaler.log")
                    print(f"PASS: host loaded OptiScaler/plugins/OptiPatcher.asi ({label})", flush=True)
        report["status"] = "passed_with_skips" if any(test["status"] in ("skipped", "passed_with_skips") for test in report["tests"]) else "passed"
    except BaseException as error:
        original_error = error
        report.update(status="failed", error=str(error))
        raise
    finally:
        cleanup_error = None
        if prefix_started:
            # -k requests termination; -w confirms the isolated server actually
            # exits. Both commands are bounded and retained as validation checks.
            for flag, name in (("-k", "wineserver-k.log"), ("-w", "wineserver-w.log")):
                try:
                    execute([proton / "files/bin/wineserver", flag], env, work, work / name, timeout=15)
                except BaseException as error:
                    if cleanup_error is None:
                        cleanup_error = error
            if cleanup_error is not None:
                report.update(status="failed", cleanup_error=str(cleanup_error))
                if original_error is None:
                    report["error"] = str(cleanup_error)
        try:
            write_report(work / "results.json", report)
        except BaseException:
            if original_error is None and cleanup_error is None:
                raise
        if cleanup_error is not None and original_error is None:
            raise cleanup_error
    print(f"GPU test logs: {work}")

if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        raise SystemExit(f"AMDNR tests: {error}")
