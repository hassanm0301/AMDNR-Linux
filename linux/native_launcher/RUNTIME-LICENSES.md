# AMDNR Linux Manager: portable runtime notices and source

The native launcher is an independent GPL-3.0-or-later Python application. Its
complete editable source is in `addon/linux/launcher.py` and
`addon/linux/native_launcher/`; its license is `addon/LICENSE`. No source from the
upstream Windows launcher is used. The AMDNR integration has separate notices in
`addon/linux/RELEASE-LICENSES.md`.

AMDNR by 3zwr1 - https://github.com/3zwr1/AMD-NR---OptiScaler

Optional components the manager downloads only when a user chooses them are not
part of the portable app: danielblnc's runtime (DLSS-NR on AMD by Daniel Blanco,
all rights reserved, distributed unmodified by AMDNR with his permission), the
OptiScaler project's OptiPatcher plugin, and TheAutomatic's GPL-3.0 "OptiScaler
AMD pre-SR" host. See `addon/linux/RELEASE-LICENSES.md`.

## AppImage builds

AppImage builds contain the same runtime, notices and source directories as the
portable folder. The outer executable uses the
[AppImage type-2 runtime 20251108](https://github.com/AppImage/type2-runtime/tree/20251108),
under the MIT license. Its notice and dependency license links (musl, libfuse,
squashfuse, Zstandard and zlib) are in `runtime/licenses/LICENSE.AppImage-runtime.txt`.
The exact runtime URL, checksum and source reference are recorded in
`RUNTIME-MANIFEST.json`; the upstream tree includes build and source instructions.
The build tool appimagetool 1.9.1 is used only during packaging.

To inspect or modify the bundled files, run `./<file>.AppImage --appimage-extract`,
then use `squashfs-root/AppRun`. This includes replacing the LGPL Qt libraries as
described below. `--appimage-extract-and-run` temporarily extracts and runs the
application on systems without usable FUSE. Installed game wrappers use a
persistent Python copy outside the mounted or temporarily extracted image.

## Python

`runtime/python` contains CPython **3.12.15**, built by
[python-build-standalone, release 20261013](https://github.com/astral-sh/python-build-standalone/releases/tag/20261013).
The official x86_64 GNU/Linux install-only stripped archive is used. Its SHA-256
and download URL are recorded in `RUNTIME-MANIFEST.json`. No Python or C runtime
libraries are copied from the packaging machine.

Python and its bundled dependencies retain their original licenses. The exact
notices from the matching full Python build are included in
`runtime/python/licenses/`; the build metadata is `runtime/python/PYTHON-BUILD.json`.
The CPython license is also in `runtime/python/lib/python3.12/LICENSE.txt`.
The referenced zlib-ng notice is supplied separately because this upstream
install/full archive omits that notice. Pip's notices remain in its `.dist-info`
directory. The bundled Python interpreter does not depend on pip at runtime.

Corresponding upstream source and build information:

- [CPython 3.12.15 source](https://www.python.org/ftp/python/3.12.15/Python-3.12.15.tar.xz).
- [The exact Python build scripts](https://github.com/astral-sh/python-build-standalone/tree/20261013).
- `runtime/python/licenses/python-build-standalone-downloads.json` lists each
  dependency's exact source URL, version and checksum, including Berkeley DB
  6.0.19 under the Sleepycat license. The Python build metadata identifies which
  dependencies are actually linked.

## Qt and PySide

The application uses the open-source **Qt 6.12.0**, **PySide6-Essentials 6.12.0**,
and **shiboken6 6.12.0** distributions. Qt/PySide are available under LGPL-3.0,
GPL-3.0, and other applicable licenses; the original terms and third-party
notices govern each component. See
[Qt for Python licenses](https://doc.qt.io/qtforpython-6/licenses.html) and
[Qt licensing](https://doc.qt.io/qt-6/licensing.html).

This package uses LGPL-3.0 for the Qt libraries. Full GPL-3.0 and LGPL-3.0 texts
and other license texts are included under `runtime/licenses/`. Exact upstream
source archives for PySide/shiboken, QtBase, QtSvg and QtWayland are in `sources/`.
Those archives include their copyright notices, third-party attribution files,
build scripts, and bundled third-party sources. Their official download URLs and
SHA-256 checksums are in `RUNTIME-MANIFEST.json`. These source archives must remain
available with redistributed copies of this portable bundle.

The official PyPI wheels are installed, then unused QtQuick/QML, designer tools,
and unrelated Qt modules/plugins are removed. Retained library binaries are
unmodified; the build script and runtime manifest document the selection.
The application uses Core, Gui, Widgets, Network, DBus, SVG and Test bindings,
plus X11/Wayland desktop plugins. ICU 73 shared libraries from the wheel retain
their Unicode and third-party notices in `runtime/licenses/LICENSE.ICU-73.2.txt`.
System desktop libraries are not distributed with this application.

### Replacing or rebuilding Qt

Qt, PySide and shiboken remain ordinary shared libraries and Python modules at
`runtime/python/lib/python3.12/site-packages/`; they are not embedded into a
sealed executable. You may replace these with modified ABI-compatible builds.
The launcher does not verify their hashes at startup, prohibit modification, or
restrict reverse engineering for debugging modifications to these libraries.
Keep the replacement Qt, PySide and shiboken versions compatible with one another.

To rebuild, extract the matching archives in `sources/`. Follow the included
QtBase/QtWayland/QtSvg CMake instructions to build shared Qt 6.12.0, and the
`pyside-setup-everywhere-src-6.12.0` README/build documentation to build PySide and
shiboken against that Qt installation. Install the replacement modules in the
directory above; preserve the wheel's `PySide6/Qt/lib` and plugin layout or set an
appropriate library search path. The pure Python application needs no relinking.
`addon/linux/build-native-launcher.py` contains the complete packaging procedure.

## Host requirements and persistent Python copy

The portable runtime also contains **python-zstandard 0.25.0**, copyright
Gregory Szorc, and its bundled Zstandard C library, copyright Meta Platforms,
Inc. and affiliates, under their BSD licenses. It reads the upstream ROCm Debian
packages without requiring the user to install a command-line decompressor.
Exact license texts are `runtime/licenses/LICENSE.python-zstandard.txt` and
`runtime/licenses/LICENSE.Zstandard.txt`; the matching complete source archive
is `sources/zstandard-0.25.0.tar.gz`. The optional CFFI backend is omitted; the
CPython C backend is used. See the
[official python-zstandard project](https://github.com/indygreg/python-zstandard/tree/0.25.0).

The Qt wheels target x86_64 Linux with glibc **2.34 or newer** and a system C++
runtime providing **GLIBCXX_3.4.29** (GCC 11 or newer). A working X11 or
Wayland desktop, system graphics drivers, fontconfig, XKB, and the corresponding
XCB/Wayland libraries are required. The launcher respects `QT_QPA_PLATFORM` for
diagnostics; it does not force Wayland. For example, a headless check can use
`QT_QPA_PLATFORM=offscreen ./Start.sh --snapshot /tmp/manager.png`.

The launcher can preserve a Python-only copy in the user's AMDNR Linux Manager data
directory for game launch scripts. That copy excludes Qt and all `site-packages`
but retains the Python standard library, build metadata and license notices.
The original portable folder can then be moved or deleted without breaking the
installed game wrapper. See the launcher source and installation documentation
for the exact data directory and uninstall behavior.
