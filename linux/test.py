#!/usr/bin/env python3
"""Run the real GPU tests in an isolated Proton prefix. Requires an RX 9070-series GPU."""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
from launch import environment, prepare_prefix, restore_prefixes

ROOT = Path(__file__).resolve().parent

def windows(path):
    return "Z:" + str(path.resolve()).replace("/", "\\")

def run(command, env, directory, log, timeout=180, expect_pass=False, allow_skip=False):
    with log.open("w") as output:
        result = subprocess.run([str(x) for x in command], env=env, cwd=directory,
                                stdout=output, stderr=subprocess.STDOUT, timeout=timeout)
    text = log.read_text(errors="replace")
    if allow_skip and result.returncode == 77 and "SKIP:" in text:
        print(next(line for line in reversed(text.splitlines()) if "SKIP:" in line), flush=True)
        return
    if result.returncode or (expect_pass and "PASS:" not in text):
        raise RuntimeError(f"Test failed (exit {result.returncode}): {log}\n" + "\n".join(text.splitlines()[-25:]))
    if expect_pass:
        print(next(line for line in reversed(text.splitlines()) if "PASS:" in line), flush=True)

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
    parser.add_argument("--with-host", action="store_true",
                        help="Also run inference with the released OptiScaler graphics hooks and GPU spoofing")
    parser.add_argument("--host-pipeline", action="store_true",
                        help="Also compare NR off/on through the released host's full NGX/FSR2 pipeline")
    args = parser.parse_args()
    if not 3 <= args.frames <= 10000:
        parser.error("--frames must be between 3 and 10000")
    if not 64 <= args.width <= 1920 or not 64 <= args.height <= 1080:
        parser.error("Image size must be between 64x64 and 1920x1080")
    package, build, work, proton = (p.resolve() for p in (args.package, args.build, args.output, args.proton))
    work.mkdir(parents=True, exist_ok=True)
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
        for name in ("winmm.dll", "LmxxfNrRuntime.dll", "LmxxfNrRuntime.pak"):
            shutil.copy2(package / name, work / name)
        shutil.copytree(package / "OptiScaler", work / "OptiScaler", dirs_exist_ok=True)
        # Configure only the isolated test copy; force the paths that games use
        # even though the harness has no NGX library for host autodetection.
        settings = {("Log", "LogToFile"): "true", ("Log", "LogLevel"): "1",
                    ("Menu", "OverlayMenu"): "true", ("Spoofing", "Dxgi"): "true",
                    ("Spoofing", "Vulkan"): "true", ("Hotfix", "CheckForUpdate"): "false"}
        host_config(package / "OptiScaler.ini", work / "OptiScaler.ini", settings)
    for name in ("syscall_dispatch.exe", "hip_smoke.exe", "exception_smoke.exe", "exception_context.exe", "compute.hsaco", "d3d12_interop.exe", "neural_smoke.exe", "host_shaders.exe"):
        shutil.copy2(build / name, work / name)
    if args.host_pipeline:
        shutil.copy2(build / "host_neural.exe", work / "host_neural.exe")
    compat = work / "compat"
    compat.mkdir(exist_ok=True)
    prefix = compat / "pfx"
    env = os.environ.copy()
    env.update(STEAM_COMPAT_DATA_PATH=str(compat), SteamAppId="0", SteamGameId="0",
               STEAM_COMPAT_CLIENT_INSTALL_PATH=env.get("STEAM_COMPAT_CLIENT_INSTALL_PATH", str(Path.home() / ".local/share/Steam")),
               WINEDEBUG="-all", VKD3D_DEBUG="none", DXVK_LOG_LEVEL="error", VKD3D_SHADER_CACHE_PATH=str(work))
    run([proton / "proton", "run", "cmd.exe", "/c", "exit"], env, work, work / "prefix.log")
    env.update(WINEPREFIX=str(prefix), WINESERVER=str(proton / "files/bin/wineserver"))
    lib = proton / ("files/lib64" if (proton / "files/lib64").is_dir() else "files/lib")
    env["LD_LIBRARY_PATH"] = str(lib) + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
    env = environment(bundle, env)
    env["AMDNR_TEST_FULL_NETWORK"] = "1" if args.full_network else "0"
    env["AMDNR_TEST_HISTORY"] = "1" if args.history else "0"
    env["AMDNR_TEST_CONTROLS"] = "1" if args.controls else "0"
    prepare_prefix(bundle, prefix)
    wine = proton / "files/bin/wine"
    try:
        run([wine, work / "syscall_dispatch.exe"], env, work, work / "syscall_dispatch.log",
            expect_pass=True, allow_skip=True)
        for name, arguments in (
            ("exception_smoke", []),
            ("exception_context", [windows(work / "compute.hsaco")]),
            ("hip_smoke", [windows(work / "compute.hsaco")]),
            ("d3d12_interop", []),
            ("host_shaders", []),
            ("neural_smoke", [windows(package / "LmxxfNrRuntime.dll"), windows(package / "LmxxfNrRuntime.pak"),
                              str(args.frames), str(args.width), str(args.height)]),
        ):
            run([wine, work / (name + ".exe"), *arguments], env, work, work / (name + ".log"), expect_pass=True)
        if args.with_host:
            host_env = dict(env, AMDNR_TEST_HOST=windows(work / "winmm.dll"))
            (work / "OptiScaler.log").unlink(missing_ok=True)
            run([wine, work / "neural_smoke.exe", windows(work / "LmxxfNrRuntime.dll"),
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
            for mode in ("baseline", "neural"):
                settings[("DlssNr", "Enabled")] = "true" if mode == "neural" else "false"
                host_config(package / "OptiScaler.ini", work / "OptiScaler.ini", settings)
                for name in ("OptiScaler.log", "lmxxf_backend.log", "amd_bridge.log"):
                    (work / name).unlink(missing_ok=True)
                reference = windows(work / "host-baseline.bin") if mode == "neural" else "-"
                run([wine, work / "host_neural.exe", windows(work / "winmm.dll"), mode,
                     windows(work / f"host-{mode}.bin"), reference], env, work,
                    work / f"host-pipeline-{mode}.log", expect_pass=True)
                for name in ("OptiScaler.log", "lmxxf_backend.log", "amd_bridge.log"):
                    if (work / name).exists():
                        shutil.copy2(work / name, work / f"{Path(name).stem}-host-{mode}.log")
    finally:
        subprocess.run([str(proton / "files/bin/wineserver"), "-k"], env=env, check=False)
    print(f"GPU test logs: {work}")

if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        raise SystemExit(f"AMDNR tests: {error}")
