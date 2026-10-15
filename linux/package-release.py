#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Package tested Linux binaries and corresponding source without upstream assets."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parent.parent
VKD3D_REVISION = "0bb64c56c8f0dcc11ecc0f6c56e00e4f7bd8d506"
BINARIES = {
    "d3d12.dll": "d3d12.dll",
    "d3d12core.dll": "d3d12core.dll",
    "x86_64-windows/amdhip64_7.dll": "amdnr-linux/x86_64-windows/amdhip64_7.dll",
    "x86_64-unix/amdhip64_7.so": "amdnr-linux/x86_64-unix/amdhip64_7.so",
    "x86_64-unix/libamdnr_signal_guard.so": "amdnr-linux/x86_64-unix/libamdnr_signal_guard.so",
}
HEADERS = ["amd/TemporalStability.h", "amd/SystemCompiler.h",
           "lmxxf/runtime/LmxxfNrApi.h", "lmxxf/runtime/LICENSE.lmxxf"]


def git(directory, *arguments):
    return subprocess.check_output(["git", "-C", str(directory), *arguments])


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def copy(source, target):
    if not source.is_file() or source.is_symlink():
        raise ValueError(f"Expected a regular file: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def validation_metadata(report_path, package):
    """Bind automated evidence to the shipped files without exporting local paths.

    Gameplay coverage is described separately in release notes. A previous
    release's game result must never silently certify newly built binaries.
    """
    if report_path is None:
        return {"status": "not_attached", "scope": "No automated validation report supplied"}
    report = json.loads(report_path.read_text())
    if report.get("status") not in ("passed", "passed_with_skips"):
        raise ValueError("Validation report did not pass")
    hashes = report.get("binaries", {})
    for original in BINARIES.values():
        if hashes.get(original) != sha(package / original):
            raise ValueError(f"Validation report binary mismatch: {original}")
    tests = report.get("tests", [])
    if not tests or any(test.get("status") not in ("passed", "passed_with_skips", "skipped") for test in tests):
        raise ValueError("Validation report has missing or failed test results")
    if not any(test.get("log") == "neural_smoke.log" and test.get("status") == "passed" for test in tests):
        raise ValueError("Validation report has no successful neural test")
    status = "passed_with_skips" if any(test["status"] in ("skipped", "passed_with_skips") for test in tests) else "passed"
    return {"status": status, "scope": "Automated isolated GPU tests; not gameplay validation",
            "checks": [{"log": Path(test["log"]).name, "status": test["status"]} for test in tests],
            "binary_sha256": {name: hashes[name] for name in BINARIES.values()}}


def archive(directory, output, epoch):
    with output.open("xb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as zipped:
        with tarfile.open(fileobj=zipped, mode="w") as tar:
            for path in sorted(directory.rglob("*")):
                if path.is_dir():
                    continue
                name = str(Path(directory.name) / path.relative_to(directory))
                info = tar.gettarinfo(str(path), arcname=name)
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = epoch
                if info.isfile():
                    with path.open("rb") as stream:
                        tar.addfile(info, stream)
                elif info.issym():
                    if Path(info.linkname).is_absolute():
                        raise ValueError(f"Absolute source symlink: {name}")
                    tar.addfile(info)
                else:
                    raise ValueError(f"Unexpected archive entry: {name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--package", type=Path, required=True, help="Exact validated local package")
    parser.add_argument("--validation-report", type=Path, help="results.json from linux/test.py for these exact binaries")
    parser.add_argument("--vkd3d-source", type=Path, required=True, help="Patched checkout with initialized submodules")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.]+)?", args.version):
        parser.error("Invalid release version")
    if git(ROOT, "status", "--porcelain").strip():
        parser.error("Commit all release source/documentation changes before packaging")
    if args.output.exists():
        parser.error("Output directory already exists")
    validation = validation_metadata(args.validation_report, args.package)
    source = args.vkd3d_source.resolve()
    if git(source, "rev-parse", "HEAD").decode().strip() != VKD3D_REVISION:
        parser.error("vkd3d-proton checkout is not at the pinned revision")
    patch = ROOT / "linux/patches/vkd3d-proton-shared-buffers.patch"
    subprocess.run(["git", "-C", str(source), "apply", "--reverse", "--check", str(patch)], check=True)
    # Require exactly the shipped patch, with no extra changes to tracked source.
    with tempfile.TemporaryDirectory(prefix="amdnr-source-check-") as scratch:
        scratch_index = str(Path(scratch) / "index")
        env = dict(os.environ, GIT_INDEX_FILE=scratch_index)
        subprocess.run(["git", "-C", str(source), "read-tree", "HEAD"], env=env, check=True)
        subprocess.run(["git", "-C", str(source), "apply", "--cached", str(patch)], env=env, check=True)
        subprocess.run(["git", "-C", str(source), "diff", "--exit-code"], env=env, check=True)
    submodules = git(source, "submodule", "status", "--recursive").decode()
    if any(line and line[0] != " " for line in submodules.splitlines()):
        parser.error("vkd3d-proton submodule revisions do not match")
    source_paths = [Path(raw.decode()) for raw in git(source, "ls-files", "-z", "--recurse-submodules").split(b"\0") if raw]
    commit = git(ROOT, "rev-parse", "HEAD").decode().strip()
    epoch = int(git(ROOT, "show", "-s", "--format=%ct", "HEAD"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="amdnr-release-", dir=args.output.parent) as temporary:
        work = Path(temporary)
        deliverables = work / "assets"
        deliverables.mkdir()
        addon = work / f"AMDNR-Linux-v{args.version}"
        addon.mkdir()
        for raw in git(ROOT, "ls-files", "-z", "linux").split(b"\0"):
            if raw:
                path = Path(raw.decode())
                copy(ROOT / path, addon / path)
        copy(ROOT / "LICENSE", addon / "LICENSE")
        copy(ROOT / "linux/INSTALL.md", addon / "README.md")
        for name in HEADERS:
            path = Path("main/AMDNR-source/optiscaler/dlssnr") / name
            copy(ROOT / path, addon / path)
        sums = []
        for destination, original in BINARIES.items():
            path = Path("linux/build") / destination
            copy(args.package / original, addon / path)
            sums.append(f"{sha(addon / path)}  {path.as_posix()}")
        for notice in ("COPYING", "LICENSE", "AUTHORS"):
            copy(source / notice, addon / ("linux/build/vkd3d-proton-" + notice))
        # Permissive dependencies also require their notices with the binaries,
        # independently of the separate corresponding-source download.
        for path in source_paths:
            if any(part.upper().startswith(("LICENSE", "COPYING", "NOTICE", "AUTHORS", "COPYRIGHT")) for part in path.parts):
                copy(source / path, addon / "linux/build/vkd3d-licenses" / path)
        (addon / "linux/build/vkd3d-proton-revision").write_text(VKD3D_REVISION + "\n")
        (addon / "BINARY-SHA256SUMS").write_text("\n".join(sums) + "\n")
        metadata = {
            "release": f"linux-v{args.version}", "source_commit": commit,
            "bridge_format": "Wine PE builtin plus native Unixlib",
            "upstream_amdnr": "0.3.5.5R4", "private_rocm": "7.2.4",
            "vkd3d_proton_commit": VKD3D_REVISION,
            "validation": validation,
            "excluded": ["upstream host/runtime/model", "Microsoft shader compiler", "ROCm binaries", "game files", "prefixes and user logs"],
            "binaries": {line.split("  ")[1]: line.split("  ")[0] for line in sums},
        }
        (addon / "RELEASE-MANIFEST.json").write_text(json.dumps(metadata, indent=2) + "\n")
        archive(addon, deliverables / f"{addon.name}.tar.gz", epoch)

        full_source = work / f"AMDNR-Linux-v{args.version}-vkd3d-source"
        for path in source_paths:
            original, target = source / path, full_source / "vkd3d-proton" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if original.is_symlink():
                target.symlink_to(original.readlink())
            else:
                copy(original, target)
        (full_source / "REVISIONS.txt").write_text(VKD3D_REVISION + "\n" + submodules)
        copy(patch, full_source / patch.name)
        (full_source / "BUILDING.md").write_text(
            "# Corresponding source for the shipped vkd3d-proton DLLs\n\n"
            "The complete source and pinned submodules are included. The shared-buffer patch is already applied; do not apply it twice.\n\n"
            "Install Meson, Ninja, MinGW-w64 C/C++ tools, Python, and the dependencies documented in vkd3d-proton/README.md. From this directory:\n\n"
            "```sh\nmeson setup build vkd3d-proton --cross-file vkd3d-proton/build-win64.txt --buildtype release -Denable_tests=false -Denable_extras=false\n"
            "ninja -C build -j 8\n```\n\n"
            "The outputs are build/libs/d3d12/d3d12.dll and build/libs/d3d12core/d3d12core.dll. Compiler versions can change binary hashes.\n"
            "Licenses and notices remain with their source files. See vkd3d-proton/COPYING and each subproject.\n")
        archive(full_source, deliverables / f"{full_source.name}.tar.gz", epoch)
        copy(ROOT / "linux/INSTALL.md", deliverables / "INSTALL.md")
        (deliverables / "SHA256SUMS").write_text("".join(
            f"{sha(path)}  {path.name}\n" for path in sorted(deliverables.iterdir()) if path.is_file()))
        deliverables.rename(args.output)
    print(f"Release assets: {args.output}")


if __name__ == "__main__":
    main()
