#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare the prebuilt Linux addon without changing any game installation."""
import argparse
import hashlib
import importlib.util
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

import manage

ROOT = Path(__file__).resolve().parent
RELEASE_VERSION = "0.3.5.6"
RELEASE_SHA256 = next(digest for digest, version in manage.RELEASES.items() if version == RELEASE_VERSION)
ARCHIVE_NAME = f"AMDNR-v{RELEASE_VERSION}.zip"
RELEASE_URL = f"https://github.com/3zwr1/AMD-NR---OptiScaler/releases/download/Alpha{RELEASE_VERSION}/{ARCHIVE_NAME}"
BINARY_PATHS = frozenset({
    "linux/build/d3d12.dll",
    "linux/build/d3d12core.dll",
    "linux/build/x86_64-windows/amdhip64_7.dll",
    "linux/build/x86_64-unix/amdhip64_7.so",
    "linux/build/x86_64-unix/libamdnr_signal_guard.so",
})


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def verify_binaries():
    """Accept exactly the release's five binary paths, never arbitrary paths."""
    release_root = ROOT.parent
    manifest = release_root / "BINARY-SHA256SUMS"
    if not manifest.is_file() or manifest.is_symlink():
        raise ValueError("Missing regular BINARY-SHA256SUMS file. Use the complete prebuilt release archive.")
    expected = {}
    for line in manifest.read_text(encoding="ascii").splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64})  (.+)", line)
        if not match or match[2] not in BINARY_PATHS:
            raise ValueError("Unexpected entry in BINARY-SHA256SUMS.")
        checksum, relative = match.groups()
        if relative in expected:
            raise ValueError(f"Duplicate binary checksum: {relative}")
        expected[relative] = checksum.lower()
    if expected.keys() != BINARY_PATHS:
        raise ValueError("BINARY-SHA256SUMS must list all five prebuilt binaries exactly once.")
    for relative, checksum in expected.items():
        path = release_root / relative
        if not path.is_file() or path.is_symlink() or any(
                parent.is_symlink() for parent in path.parents if parent != release_root and release_root in parent.parents):
            raise ValueError(f"Missing or symlinked prebuilt binary: {relative}")
        if digest(path) != checksum:
            raise ValueError(f"Prebuilt binary checksum mismatch: {relative}")
    print("Verified all five prebuilt Linux addon binaries.", flush=True)


def validate_compiler(path):
    """Reject Wine placeholders/builtins and non-x64 DLLs before downloads."""
    if not path.is_file():
        raise ValueError(f"Shader compiler does not exist: {path}")
    with path.open("rb") as source:
        dos = source.read(512)
        if b"Wine builtin DLL" in dos or b"Wine placeholder DLL" in dos:
            raise ValueError("Use a native Microsoft d3dcompiler_47.dll from your game installation, not Wine's builtin DLL.")
        if len(dos) < 64 or dos[:2] != b"MZ":
            raise ValueError("Shader compiler must be a native Windows x64 DLL.")
        offset = struct.unpack_from("<I", dos, 0x3c)[0]
        if offset < 64 or offset > path.stat().st_size - 26:
            raise ValueError("Shader compiler has an invalid PE header.")
        source.seek(offset)
        header = source.read(26)
        optional_size, flags = struct.unpack_from("<HH", header, 20)
        if (header[:4] != b"PE\0\0" or struct.unpack_from("<H", header, 4)[0] != 0x8664 or
                struct.unpack_from("<H", header, 24)[0] != 0x20b or not flags & 0x2000 or
                optional_size < 112 or offset + 24 + optional_size > path.stat().st_size):
            raise ValueError("Shader compiler must be a native Windows x64 DLL (PE32+, AMD64).")


def fetch_archive(cache):
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / ARCHIVE_NAME
    if archive.is_symlink() or (archive.exists() and not archive.is_file()):
        raise ValueError(f"Archive cache entry must be a regular file: {archive}")
    if archive.is_file() and digest(archive) == RELEASE_SHA256:
        print(f"Using verified cached {ARCHIVE_NAME}.", flush=True)
        return archive
    print(f"Downloading pinned AMDNR {RELEASE_VERSION}.", flush=True)
    pending = None
    try:
        with tempfile.NamedTemporaryFile(prefix=ARCHIVE_NAME + ".", suffix=".partial", dir=cache, delete=False) as target:
            pending = Path(target.name)
            request = urllib.request.Request(RELEASE_URL, headers={"User-Agent": "AMDNR-Linux-setup"})
            with urllib.request.urlopen(request, timeout=60) as source:
                shutil.copyfileobj(source, target)
        if digest(pending) != RELEASE_SHA256:
            raise ValueError("Downloaded AMDNR archive checksum mismatch; package was not prepared.")
        pending.replace(archive)
        return archive
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def fetch_runtime(output, cache):
    spec = importlib.util.spec_from_file_location("amdnr_fetch_rocm", ROOT / "fetch-rocm.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.fetch(output, cache)


def prepare(compiler, archive=None, output=None, cache=None):
    if sys.version_info < (3, 12):
        raise ValueError("Python 3.12 or newer is required.")
    compiler = Path(compiler).expanduser().resolve()
    output = Path(output if output is not None else ROOT / "build/package").expanduser().absolute()
    cache = Path(cache if cache is not None else ROOT / "deps/packages").expanduser().resolve()
    if output.exists() or output.is_symlink():
        raise ValueError(f"Output already exists: {output}")
    output = output.resolve()
    if cache == output or output in cache.parents:
        raise ValueError("Download cache must be outside the output package directory.")
    validate_compiler(compiler)
    verify_binaries()
    if archive is not None:
        archive = Path(archive).expanduser().resolve()
        if not archive.is_file() or digest(archive) != RELEASE_SHA256:
            raise ValueError(f"Expected the pinned, unmodified AMDNR {RELEASE_VERSION} archive; checksum mismatch.")
    else:
        archive = fetch_archive(cache)
    # Always extract a fresh runtime from verified package archives. A mutable
    # runtime from a previous preparation is never silently trusted or reused.
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="amdnr-prepare-", dir=output.parent) as temp:
        runtime = Path(temp) / "rocm"
        fetch_runtime(runtime, cache)
        manage.stage(archive, compiler, output, ROOT / "build", runtime)
    print(f"Prepared {output}\nNo game files were changed. Install with linux/manage.py install.")
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("prepare", help="Verify binaries and prepare a package with pinned upstream assets")
    command.add_argument("--compiler", type=Path, required=True, help="Native 64-bit d3dcompiler_47.dll from your own game installation")
    command.add_argument("--archive", type=Path, help=f"Existing pinned AMDNR {RELEASE_VERSION} archive; downloaded when omitted")
    command.add_argument("--output", type=Path, default=ROOT / "build/package")
    command.add_argument("--cache", type=Path, default=ROOT / "deps/packages")
    args = parser.parse_args(argv)
    prepare(args.compiler, args.archive, args.output, args.cache)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        raise SystemExit(f"AMDNR Linux setup: {error}")
