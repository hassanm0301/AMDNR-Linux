#!/usr/bin/env python3
"""Probe released AMDNR proxy aliases in isolated Proton prefixes, without games.

This validates representative CPU APIs and startup loading only. It does not
establish game compatibility, complete export coverage, or neural rendering.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time

ALIASES = ("winmm", "version", "dbghelp", "wininet", "winhttp")
SOURCE = Path(__file__).with_name("proxy_alias.cpp")


def execute(command, env, cwd, log, timeout=60):
    start = time.monotonic()
    timed_out = False
    with log.open("w") as output:
        process = subprocess.Popen([str(part) for part in command], cwd=cwd, env=env,
                                   stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            process.wait(timeout=timeout)
        except BaseException as error:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=3)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            if not isinstance(error, subprocess.TimeoutExpired):
                raise
            timed_out = True
    text = log.read_text(errors="replace")
    passed = not timed_out and process.returncode == 0 and any(
        line.startswith("PASS:") for line in text.splitlines())
    return {"exit_code": process.returncode, "timed_out": timed_out, "passed": passed,
            "elapsed_seconds": round(time.monotonic() - start, 3), "log": str(log)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--proton", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True, help="New output directory; never a game prefix")
    parser.add_argument("--compiler", default="x86_64-w64-mingw32-g++")
    args = parser.parse_args()
    package, output = args.package.resolve(), args.output.resolve()
    source_host = package / "winmm.dll"
    if not source_host.is_file() or not (package / "OptiScaler.ini").is_file():
        parser.error("package must contain the released winmm.dll host and OptiScaler.ini")
    if output.exists():
        parser.error("output must be a new directory")
    output.mkdir(parents=True)
    binaries = output / "bin"
    binaries.mkdir()
    report = {"created_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "CPU proxy loading and representative API forwarding; no games or rendering",
              "package": str(package), "host_sha256": hashlib.sha256(source_host.read_bytes()).hexdigest(),
              "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(), "runners": []}
    exports = subprocess.check_output(["objdump", "-p", source_host], text=True)
    (output / "host-pe-headers.txt").write_text(exports)
    for kind, alias in enumerate(ALIASES, 1):
        for mode in ("explicit-load", "static-import"):
            command = [args.compiler, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-static",
                       f"-DPROXY_KIND={kind}", str(SOURCE), "-o", str(binaries / f"{alias}-{mode}.exe")]
            if mode == "static-import":
                command += ["-DPROXY_STATIC=1", "-l" + alias]
            subprocess.run(command, check=True, timeout=60)
    config = (package / "OptiScaler.ini").read_text(encoding="utf-8-sig")
    for key, value in (("CheckForUpdate", "false"), ("LogToFile", "true"), ("LogLevel", "1")):
        config, count = re.subn(rf"(?m)^{key}=.*$", key + "=" + value, config)
        if count != 1:
            raise ValueError(f"Expected exactly one {key} setting in released configuration")
    try:
        for index, proton_path in enumerate(args.proton):
            proton = proton_path.resolve()
            work = output / f"runner-{index + 1}"
            work.mkdir()
            compat = work / "compat"
            compat.mkdir()
            prefix = compat / "pfx"
            env = os.environ.copy()
            for name in ("LD_PRELOAD", "WINEDLLOVERRIDES", "WINEPREFIX", "WINESERVER", "WINELOADER",
                         "STEAM_COMPAT_DATA_PATH", "PROTON_LOG"):
                env.pop(name, None)
            env.update(WINEPREFIX=str(prefix), WINESERVER=str(proton / "files/bin/wineserver"),
                       STEAM_COMPAT_DATA_PATH=str(compat), SteamAppId="0", SteamGameId="0",
                       STEAM_COMPAT_CLIENT_INSTALL_PATH=str(Path.home() / ".local/share/Steam"),
                       WINEDEBUG="-all", VKD3D_DEBUG="none", DXVK_LOG_LEVEL="error")
            lib = proton / ("files/lib64" if (proton / "files/lib64").is_dir() else "files/lib")
            env["LD_LIBRARY_PATH"] = str(lib) + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
            runner = {"proton": str(proton), "version": (proton / "version").read_text().strip(), "tests": []}
            report["runners"].append(runner)
            try:
                initialized = execute([proton / "proton", "run", "cmd.exe", "/c", "exit"], env, work,
                                      work / "prefix.log", timeout=60)
                if initialized["exit_code"] or initialized["timed_out"]:
                    raise RuntimeError(f"Prefix initialization failed: {initialized}")
                for alias in ALIASES:
                    for mode in ("explicit-load", "static-import"):
                        case = work / f"{alias}-{mode}"
                        case.mkdir()
                        shutil.copy2(source_host, case / (alias + ".dll"))
                        shutil.copy2(binaries / f"{alias}-{mode}.exe", case / "probe.exe")
                        (case / "OptiScaler.ini").write_text(config)
                        run_env = dict(env, WINEDLLOVERRIDES=";".join(
                            name + ("=n,b" if name == alias else "=b") for name in ALIASES)
                            + ";winedbg=d;winemenubuilder.exe=d")
                        result = execute([proton / "files/bin/wine", case / "probe.exe"], run_env, case,
                                         case / "probe.log", timeout=30)
                        runner["tests"].append(dict(result, alias=alias + ".dll", mode=mode))
                        print(json.dumps(runner["tests"][-1]), flush=True)
                        (output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
            finally:
                # This prefix was created here; cleanup cannot touch Steam/game servers.
                subprocess.run([proton / "files/bin/wineserver", "-k"], env=env, timeout=15, check=False)
                subprocess.run([proton / "files/bin/wineserver", "-w"], env=env, timeout=15, check=True)
                runner["prefix_processes_stopped"] = True
    finally:
        (output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if all(case["passed"] for runner in report["runners"] for case in runner["tests"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
