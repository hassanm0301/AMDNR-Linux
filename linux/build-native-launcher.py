#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build a relocatable native Linux launcher from an assembled release addon.

Outputs a .tar.gz portable folder or a single executable .AppImage.
Build dependencies: Python >= 3.12, binutils/readelf, zstd, network access on the
first build. Users need none of these tools to start the resulting application.
No system Python packages or shared libraries are copied into the distribution.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

HERE = Path(__file__).resolve().parent
PYTHON_VERSION = "3.12.15"
PYTHON_BUILD = "20261013"
QT_VERSION = "6.12.0"
PYTHON_ARCHIVE = "cpython-3.12.15+20261013-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
PYTHON_FULL = "cpython-3.12.15+20261013-x86_64-unknown-linux-gnu-pgo+lto-full.tar.zst"
PYSIDE_WHEEL = "pyside6_essentials-6.12.0-cp310-abi3-manylinux_2_34_x86_64.whl"
SHIBOKEN_WHEEL = "shiboken6-6.12.0-cp310-abi3-manylinux_2_34_x86_64.whl"
ZSTANDARD_WHEEL = "zstandard-0.25.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl"
ZSTANDARD_SOURCE = "zstandard-0.25.0.tar.gz"
QT_SOURCE = "https://download.qt.io/official_releases/qt/6.12/6.12.0/submodules/"
PYTHON_RELEASE = "https://github.com/astral-sh/python-build-standalone/releases/download/20261013/"
# Hashes come from the official GitHub release digest, PyPI JSON, Qt .sha256,
# or the exact versioned source file. A changed upstream file fails closed.
DOWNLOADS = {
    PYTHON_ARCHIVE: (PYTHON_RELEASE + PYTHON_ARCHIVE.replace("+", "%2B"),
                     "178d3d83b6e8e99285a65d3fce9c16b87952cc5efdb4d1a0e3ff3be18ff0fc9c"),
    PYTHON_FULL: (PYTHON_RELEASE + PYTHON_FULL.replace("+", "%2B"),
                  "dfb784f8d467e0c64c16d67a151c9145e64a0a11caa0ff699bc92899898cbb52"),
    PYSIDE_WHEEL: ("https://files.pythonhosted.org/packages/7b/3c/7597d8dcb36aa8315427635ec6ec9d850ffbad4ce78b9118ac854889f2d3/" + PYSIDE_WHEEL,
                   "16f4b5e41daf49235ae048458243da9efe5f3d8040f9cc5aa454ce69893d3bd9"),
    SHIBOKEN_WHEEL: ("https://files.pythonhosted.org/packages/49/c2/a04fa003b4360e99d8e3fc2829abacf37396ace63ea1cd616402209ec7c6/" + SHIBOKEN_WHEEL,
                     "3bde565bb0890044b5c7f63808adddede5abc241e9395a2bfed61919884d6240"),
    ZSTANDARD_WHEEL: ("https://files.pythonhosted.org/packages/53/60/7be26e610767316c028a2cbedb9a3beabdbe33e2182c373f71a1c0b88f36/" + ZSTANDARD_WHEEL,
                      "5a56ba0db2d244117ed744dfa8f6f5b366e14148e00de44723413b2f3938a902"),
    ZSTANDARD_SOURCE: ("https://files.pythonhosted.org/packages/fd/aa/3e0508d5a5dd96529cdc5a97011299056e14c6505b678fd58938792794b1/" + ZSTANDARD_SOURCE,
                       "7713e1179d162cf5c7906da876ec2ccb9c3a9dcbdffef0cc7f70c3667a205f0b"),
    "pyside-setup-everywhere-src-6.12.0.tar.xz": (
        "https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.12.0-src/pyside-setup-everywhere-src-6.12.0.tar.xz",
        "099c1a597cf33c1b000e11cf68a0b82d216fb802bf81726efc720394f10c7751"),
    "qtbase-everywhere-src-6.12.0.tar.xz": (QT_SOURCE + "qtbase-everywhere-src-6.12.0.tar.xz",
        "a951bd163c7b80fc6b8c88d7668fb56abf91c152373e13c10666763238131307"),
    "qtsvg-everywhere-src-6.12.0.tar.xz": (QT_SOURCE + "qtsvg-everywhere-src-6.12.0.tar.xz",
        "e4ab39534ec97987b1b9b60ef7f4d3253d5912a69f8c713a01753174a4029331"),
    "qtwayland-everywhere-src-6.12.0.tar.xz": (QT_SOURCE + "qtwayland-everywhere-src-6.12.0.tar.xz",
        "ae14d9bcf1bb3c300a3ab2e6a53f50284e5c6c966a4ac4cc5aedcb1bc74e4d02"),
    "LICENSE.ICU-73.2.txt": ("https://raw.githubusercontent.com/unicode-org/icu/release-73-2/icu4c/LICENSE",
        "f3005e195ff74d8812cc1f182a1c446fab678d70a10e3dada497585befee5416"),
    "LICENSE.zlib-ng.txt": ("https://raw.githubusercontent.com/zlib-ng/zlib-ng/2.2.4/LICENSE.md",
        "6c9f0d975b41afaa34d22f55bb8986ce69e5cb7ad327cb2b28820cd425edf5ee"),
    "python-build-standalone-downloads.json": (
        "https://raw.githubusercontent.com/astral-sh/python-build-standalone/20261013/pythonbuild/downloads.json",
        "0a176490488b79448ef66087e480bf0966c8450849ff0c168a9a26cd1756abea"),
}
APPIMAGETOOL = "appimagetool-1.9.1-x86_64.AppImage"
APPIMAGE_RUNTIME = "appimage-runtime-20251108-x86_64"
APPIMAGE_DOWNLOADS = {
    APPIMAGETOOL: (
        "https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage",
        "ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"),
    APPIMAGE_RUNTIME: (
        "https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64",
        "2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d"),
    "LICENSE.AppImage-runtime.txt": (
        "https://raw.githubusercontent.com/AppImage/type2-runtime/20251108/LICENSE",
        "aa154fc9070614bbe7921f89db11efd1dba7a1f3a41685958110e2230f9c0ca1"),
}
MODULES = {"QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtSvg", "QtSvgWidgets", "QtTest", "QtDBus"}
PLUGIN_DIRS = {"platformthemes", "platforminputcontexts", "wayland-decoration-client",
               "wayland-graphics-integration-client", "wayland-shell-integration", "xcbglintegrations"}
PLUGIN_FILES = {"platforms/libqoffscreen.so", "platforms/libqminimal.so", "platforms/libqwayland.so",
                "platforms/libqxcb.so", "imageformats/libqgif.so", "imageformats/libqico.so",
                "imageformats/libqjpeg.so", "imageformats/libqsvg.so", "iconengines/libqsvgicon.so"}
BINARIES = {"linux/build/d3d12.dll", "linux/build/d3d12core.dll",
            "linux/build/x86_64-windows/amdhip64_7.dll", "linux/build/x86_64-unix/amdhip64_7.so",
            "linux/build/x86_64-unix/libamdnr_signal_guard.so"}
REQUIRED_FILES = ("RELEASE-MANIFEST.json", "LICENSE", "linux/launcher.py", "linux/setup.py",
                  "linux/manage.py", "linux/launch.py", "linux/proxies.py", "linux/runtimes.py",
                  "linux/fetch-rocm.py", "linux/run.sh",
                  "linux/native_launcher/app.py", "linux/native_launcher/controller.py",
                  "linux/native_launcher/backend.py", "linux/native_launcher/steam.py",
                  "linux/native_launcher/artwork.py", "linux/native_launcher/artwork_widgets.py",
                  "linux/native_launcher/versions.py", "linux/native_launcher/compatibility.py",
                  "linux/native_launcher/stores.py",
                  "linux/native_launcher/__init__.py", "linux/native_launcher/requirements.txt",
                  "main/AMDNR-source/optiscaler/dlssnr/amd/TemporalStability.h",
                  "main/AMDNR-source/optiscaler/dlssnr/amd/SystemCompiler.h",
                  "main/AMDNR-source/optiscaler/dlssnr/lmxxf/runtime/LmxxfNrApi.h",
                  "main/AMDNR-source/optiscaler/dlssnr/lmxxf/runtime/LICENSE.lmxxf")


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fetch(name, cache, downloads=DOWNLOADS):
    url, expected = downloads[name]
    target = cache / name
    if target.exists():
        if target.is_symlink() or not target.is_file() or sha(target) != expected:
            raise ValueError(f"Invalid cached download: {target}")
        return target
    print(f"Downloading {name}", flush=True)
    fd, temporary = tempfile.mkstemp(prefix=".download-", dir=cache)
    try:
        with os.fdopen(fd, "wb") as output, urllib.request.urlopen(url, timeout=120) as response:
            shutil.copyfileobj(response, output, 1024 * 1024)
        if sha(Path(temporary)) != expected:
            raise ValueError(f"Download checksum mismatch: {name}")
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return target


def validate_release(root):
    for name in REQUIRED_FILES:
        if not (root / name).is_file():
            raise ValueError(f"Not an assembled launcher release: missing {name}")
    if (root / ".git").exists():
        raise ValueError("Use an assembled release addon, not a repository checkout")
    found = set()
    for line in (root / "BINARY-SHA256SUMS").read_text().splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  (.+)", line)
        if not match or match[2] not in BINARIES or match[2] in found:
            raise ValueError("Unexpected binary checksum manifest")
        path = root / match[2]
        if path.is_symlink() or not path.is_file() or sha(path) != match[1]:
            raise ValueError(f"Binary checksum mismatch: {match[2]}")
        found.add(match[2])
    if found != BINARIES:
        raise ValueError("Incomplete binary checksum manifest")
    # Release input must not follow symlinks into game folders, caches, or homes.
    if any(path.is_symlink() for path in root.rglob("*")):
        raise ValueError("Assembled addon must contain regular files, not symlinks")


def run(*args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def python_notices(full, runtime):
    """The install-only archive omits notices present in the full official build."""
    notices = runtime / "licenses"
    notices.mkdir()
    with subprocess.Popen(["zstd", "-dc", str(full)], stdout=subprocess.PIPE) as process:
        with tarfile.open(fileobj=process.stdout, mode="r|") as tar:
            for member in tar:
                name = PurePosixPath(member.name)
                if member.isfile() and name.parent == PurePosixPath("python/licenses"):
                    with tar.extractfile(member) as src, (notices / name.name).open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                elif member.isfile() and member.name == "python/PYTHON.json":
                    (runtime / "PYTHON-BUILD.json").write_bytes(tar.extractfile(member).read())
        if process.wait() != 0:
            raise RuntimeError("Could not read Python build notices")
    if not (notices / "LICENSE.cpython.txt").is_file():
        raise ValueError("Missing Python copyright notices")


def needed(path):
    result = subprocess.check_output(["readelf", "-d", str(path)], text=True)
    return set(re.findall(r"\(NEEDED\).*\[(.*?)\]", result))


def trim_qt(site):
    """Keep Widgets and desktop plugins, closing dependencies only within wheel."""
    pyside = site / "PySide6"
    qt = pyside / "Qt"
    retained = []
    for path in pyside.glob("libpyside6qml.*"):
        path.unlink()
    for path in pyside.glob("Qt*.abi3.so"):
        if path.name.split(".", 1)[0] not in MODULES:
            path.unlink()
        else:
            retained.append(path)
    for path in (qt / "plugins").rglob("*.so"):
        rel = path.relative_to(qt / "plugins").as_posix()
        keep = rel in PLUGIN_FILES or path.parent.name in PLUGIN_DIRS
        # Virtual keyboard and compositor shell plugins pull in QtQuick/QML.
        if "virtualkeyboard" in path.name or path.name == "libqt-shell.so":
            keep = False
        if keep:
            retained.append(path)
        else:
            path.unlink()
    for name in ("qml", "libexec", "translations", "resources"):
        shutil.rmtree(qt / name, ignore_errors=True)
    # Remove bundled development programs; their Python entry points are unused.
    for path in pyside.iterdir():
        if path.is_file() and not path.suffix and path.name != "py.typed":
            path.unlink()
    libraries = {path.name: path for path in (qt / "lib").iterdir() if path.is_file()}
    names = set()
    pending = list(retained)
    while pending:
        for name in needed(pending.pop()):
            if name in libraries and name not in names:
                names.add(name)
                pending.append(libraries[name])
    # A newly pinned wheel must not silently expand the source/license scope.
    permitted = {"Core", "Gui", "Widgets", "Network", "DBus", "Test", "Svg", "SvgWidgets",
                 "WaylandClient", "WlShellIntegration", "XcbQpa", "OpenGL"}
    for name, path in libraries.items():
        if name not in names:
            path.unlink()
        elif name.startswith("libQt6") and name.removeprefix("libQt6").split(".so")[0] not in permitted:
            raise ValueError(f"Unexpected Qt module needs source coverage: {name}")
    return sorted(names)


def qt_notices(sources, notices):
    """Include license texts outside compressed sources for convenient reading."""
    for source in sources:
        prefix = source.name.removesuffix(".tar.xz")
        with tarfile.open(source) as tar:
            for member in tar:
                path = PurePosixPath(member.name)
                if member.isfile() and len(path.parts) >= 2 and path.parts[1] == "LICENSES":
                    target = notices / prefix / Path(*path.parts[1:])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(tar.extractfile(member).read())


START = '''#!/bin/sh
set -eu
NRL_PORTABLE_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
export NRL_PORTABLE_ROOT
# Keep plugins from unrelated system Qt installations out of this Qt runtime.
unset QT_PLUGIN_PATH QT_QPA_PLATFORM_PLUGIN_PATH QML2_IMPORT_PATH QML_IMPORT_PATH
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
# Qt selects Wayland/X11 from the desktop; QT_QPA_PLATFORM can override it.
exec "$NRL_PORTABLE_ROOT/runtime/python/bin/python3.12" -E -s -B \\
    "$NRL_PORTABLE_ROOT/addon/linux/launcher.py" --release-root "$NRL_PORTABLE_ROOT/addon" "$@"
'''


def archive(root, output, epoch):
    with output.open("xb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as zipped:
        with tarfile.open(fileobj=zipped, mode="w") as tar:
            for path in sorted(root.rglob("*")):
                info = tar.gettarinfo(str(path), arcname=str(Path(root.name) / path.relative_to(root)))
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = epoch
                if info.issym() and not path.resolve().is_relative_to(root.resolve()):
                    raise ValueError(f"Escaping bundle symlink: {path}")
                if info.isfile():
                    with path.open("rb") as stream:
                        tar.addfile(info, stream)
                elif info.isdir() or info.issym():
                    tar.addfile(info)
                else:
                    raise ValueError(f"Unexpected bundle file: {path}")


def appimage(root, output, cache, version, epoch):
    """Package the same relocatable tree with a pinned type-2 runtime.

    Both the packaging tool and the application support extraction without FUSE.
    The supplied runtime prevents appimagetool from fetching a moving release.
    """
    for path in root.rglob("*"):
        if path.is_symlink() and not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Escaping bundle symlink: {path}")
    for name in APPIMAGE_DOWNLOADS:
        fetch(name, cache, APPIMAGE_DOWNLOADS)
    tool, runtime = cache / APPIMAGETOOL, cache / APPIMAGE_RUNTIME
    tool.chmod(0o755)
    # AppRun uses its own location, as does Start.sh, so extracted copies work too.
    (root / "AppRun").write_text(START)
    (root / "AppRun").chmod(0o755)
    for name in ("amdnr-linux-manager.desktop", "amdnr-linux-manager.svg"):
        shutil.copy2(HERE / "appimage" / name, root / name)
    (root / ".DirIcon").symlink_to("amdnr-linux-manager.svg")
    shutil.copy2(cache / "LICENSE.AppImage-runtime.txt", root / "runtime/licenses")
    metadata_path = root / "RUNTIME-MANIFEST.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["appimage"] = {
        "appimagetool": "1.9.1", "runtime": "20251108",
        "runtime_source": "https://github.com/AppImage/type2-runtime/tree/20251108",
        "downloads": {name: {"url": url, "sha256": digest}
                      for name, (url, digest) in APPIMAGE_DOWNLOADS.items()},
    }
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    readme = root / "README.txt"
    readme.write_text(
        "AMDNR Linux Manager AppImage\n\n"
        "Make the .AppImage executable in your file manager, then open it.\n"
        "From a terminal: chmod +x <file>.AppImage; ./<file>.AppImage\n"
        "If FUSE is unavailable: ./<file>.AppImage --appimage-extract-and-run\n"
        "To modify bundled libraries: ./<file>.AppImage --appimage-extract\n"
        "then edit squashfs-root/ and run squashfs-root/AppRun.\n\n"
        + readme.read_text())
    env = dict(os.environ, ARCH="x86_64", VERSION=version, SOURCE_DATE_EPOCH=str(epoch),
               APPIMAGE_EXTRACT_AND_RUN="1")
    # Avoid machine-local signing/update settings affecting local builds.
    for key in ("APPIMAGETOOL_SIGN_PASSPHRASE", "APPIMAGETOOL_RUNTIME_FILE"):
        env.pop(key, None)
    run(tool, "--appimage-extract-and-run", "--no-appstream", "--runtime-file", runtime,
        "--comp", "zstd", root, output, env=env)
    with output.open("rb") as stream:
        header = stream.read(20)
    if header[:11] != b"\x7fELF\x02\x01\x01\x00AI\x02" or header[18:20] != b"\x3e\x00":
        raise ValueError("Packaging tool did not produce an x86_64 type-2 AppImage")
    output.chmod(0o755)


def build(release, output, cache, version, keep_tree=None):
    validate_release(release)
    # Explicit prerelease suffixes permit scratch builds from an older assembled
    # addon while the UI changes. Published stable bundles must agree throughout.
    if "-" not in version:
        manifest_version = json.loads((release / "RELEASE-MANIFEST.json").read_text()).get("release")
        app_source = (release / "linux/native_launcher/__init__.py").read_text()
        app_version = re.search(r'^VERSION\s*=\s*[\'"]([^\'"]+)[\'"]\s*$', app_source, re.MULTILINE)
        if manifest_version != "linux-v" + version or not app_version or app_version[1] != version:
            raise ValueError("Stable bundle, addon manifest, and launcher versions must match")
    if output.exists() or (keep_tree is not None and keep_tree.exists()):
        raise ValueError("Output already exists")
    for path in (output, cache, keep_tree):
        if path is not None and path.is_relative_to(release):
            raise ValueError("Build outputs and cache must be outside the assembled addon")
    expected_requirements = (HERE / "native_launcher/requirements.txt").read_bytes()
    if (release / "linux/native_launcher/requirements.txt").read_bytes() != expected_requirements:
        raise ValueError("Release requirements differ from this builder's pinned wheels")
    for tool in ("readelf", "zstd"):
        if not shutil.which(tool):
            raise ValueError(f"Build tool missing: {tool}")
    cache.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda name: fetch(name, cache), DOWNLOADS))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".nr-linux-build-", dir=output.parent) as temporary:
        work = Path(temporary)
        bundle = work / f"AMDNR-Linux-Manager-v{version}-x86_64"
        bundle.mkdir()
        shutil.copytree(release, bundle / "addon", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        runtime = bundle / "runtime/python"
        runtime.parent.mkdir()
        with tarfile.open(cache / PYTHON_ARCHIVE) as tar:
            tar.extractall(runtime.parent, filter="data")
        python_notices(cache / PYTHON_FULL, runtime)
        for name in ("LICENSE.zlib-ng.txt", "python-build-standalone-downloads.json"):
            shutil.copy2(cache / name, runtime / "licenses" / name)
        python = runtime / "bin/python3.12"
        run(python, "-I", "-m", "pip", "install", "--no-index", "--no-deps", "--no-compile",
            "--no-cache-dir", "--disable-pip-version-check", "--require-hashes", "--find-links", cache,
            "-r", bundle / "addon/linux/native_launcher/requirements.txt")
        libraries = trim_qt(runtime / "lib/python3.12/site-packages")
        zstandard = runtime / "lib/python3.12/site-packages/zstandard"
        # Only the CPython C backend is needed; the optional CFFI backend would
        # require a separate cffi package and has its own generated bindings.
        for path in zstandard.glob("_cffi.*.so"):
            path.unlink()
        (zstandard / "backend_cffi.py").unlink()
        # Pip's developer-tool scripts have build-directory shebangs and invoke
        # tools deliberately omitted from this Widgets-only runtime.
        for path in (runtime / "bin").glob("pyside6-*"):
            path.unlink()
        sources_dir = bundle / "sources"
        sources_dir.mkdir()
        sources = []
        for name in DOWNLOADS:
            if name.endswith("-src-6.12.0.tar.xz"):
                target = sources_dir / name
                shutil.copy2(cache / name, target)
                sources.append(target)
        notices = bundle / "runtime/licenses"
        notices.mkdir()
        qt_notices(sources, notices)
        shutil.copy2(cache / ZSTANDARD_SOURCE, sources_dir / ZSTANDARD_SOURCE)
        with tarfile.open(cache / ZSTANDARD_SOURCE) as tar:
            for name, target in (("LICENSE", "LICENSE.python-zstandard.txt"),
                                 ("zstd/LICENSE", "LICENSE.Zstandard.txt")):
                (notices / target).write_bytes(tar.extractfile("zstandard-0.25.0/" + name).read())
        shutil.copy2(cache / "LICENSE.ICU-73.2.txt", notices)
        shutil.copy2(HERE / "native_launcher/RUNTIME-LICENSES.md", bundle / "RUNTIME-LICENSES.md")
        (bundle / "Start.sh").write_text(START)
        (bundle / "Start.sh").chmod(0o755)
        metadata = {
            "name": "AMDNR Linux Manager", "version": version, "platform": "linux-x86_64",
            "python": PYTHON_VERSION, "python_build": PYTHON_BUILD, "qt": QT_VERSION,
            "python_zstandard": "0.25.0",
            "minimum_glibc": "2.34", "qt_libraries": libraries,
            "downloads": {name: {"url": url, "sha256": digest} for name, (url, digest) in DOWNLOADS.items()},
        }
        (bundle / "RUNTIME-MANIFEST.json").write_text(json.dumps(metadata, indent=2) + "\n")
        # Runtime identity follows the official Python build, independent of GUI updates.
        (runtime / "NRL-RUNTIME-ID").write_text(f"cpython-{PYTHON_VERSION}-{PYTHON_BUILD}-x86_64\n")
        (bundle / "README.txt").write_text(
            "AMDNR Linux Manager\n\nExtract the whole folder, then double-click Start.sh or run ./Start.sh.\n"
            "The interface runs natively on Linux; Python and Qt are included.\n"
            "Requires x86_64 Linux, glibc 2.34+, and an X11 or Wayland desktop.\n"
            "The system C++ runtime must provide GLIBCXX_3.4.29 (GCC 11 or newer).\n"
            "Your desktop's OpenGL, fontconfig, XKB and XCB/Wayland libraries are used.\n"
            "On Debian/Ubuntu an X11 desktop may additionally need libxcb-cursor0.\n\n"
            "For installation requirements and rollback see addon/README.md.\n"
            "For runtime notices, included source and replacement instructions see RUNTIME-LICENSES.md.\n"
            "AMDNR by 3zwr1 - https://github.com/3zwr1/AMD-NR---OptiScaler\n")
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        for key in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH", "QML_IMPORT_PATH"):
            env.pop(key, None)
        run(python, "-I", "-B", "-c",
            "import sys,ssl,sqlite3,lzma,zstandard; from PySide6 import QtCore,QtGui,QtWidgets; "
            "assert sys.version_info[:3] == (3,12,15); assert QtCore.__version__ == '6.12.0'; "
            "assert zstandard.ZstdDecompressor().decompress(zstandard.ZstdCompressor().compress(b'NR Linux')) == b'NR Linux'; "
            "app=QtWidgets.QApplication([]); w=QtWidgets.QLabel('AMDNR Linux Manager'); "
            "w.show(); app.processEvents(); assert not w.grab().isNull(); print('Portable Qt Widgets smoke: PASS')",
            env=env)
        # Keep the archive free of machine-local bytecode and temporary build paths.
        for path in bundle.rglob("__pycache__"):
            if path.is_dir():
                shutil.rmtree(path)
        epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
        staged_archive = work / output.name
        if output.name.endswith(".AppImage"):
            appimage(bundle, staged_archive, cache, version, epoch)
        else:
            archive(bundle, staged_archive, epoch)
        if keep_tree is not None:
            keep_tree.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(bundle, keep_tree, symlinks=True)
        staged_archive.replace(output)
    print(f"{sha(output)}  {output.name}")
    print(f"Portable launcher: {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New .tar.gz archive or .AppImage executable")
    parser.add_argument("--cache", type=Path, default=HERE / "build/native-launcher-cache")
    parser.add_argument("--version", default="0.3.1-preview")
    parser.add_argument("--keep-tree", type=Path, help="Also keep an extracted tree for desktop testing")
    args = parser.parse_args()
    if sys.version_info < (3, 12) or sys.platform != "linux" or platform.machine() != "x86_64":
        parser.error("Build on x86_64 Linux with Python 3.12 or newer")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.]+)?", args.version):
        parser.error("Invalid version")
    if not args.output.name.endswith((".tar.gz", ".AppImage")):
        parser.error("--output must name a .tar.gz archive or .AppImage executable")
    try:
        build(args.release_root.resolve(), args.output.resolve(), args.cache.resolve(), args.version,
              args.keep_tree.resolve() if args.keep_tree else None)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Launcher build failed: {error}\n")


if __name__ == "__main__":
    main()
