# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional components fetched from their publishers' releases, never redistributed.

danielblnc's "DLSS-NR on AMD" runtime and OptiScaler's OptiPatcher plugin are
downloaded only when a user chooses them, verified against pinned SHA-256
digests and extracted without executing anything. The AMDNR host pins the
SHA-256 of every danielblnc pass DLL it can drive, so whether a host drives a
runtime is read from the host binary itself rather than from a separate table.

TheAutomatic's "OptiScaler AMD pre-SR" is an independent OptiScaler fork that
can replace the AMDNR host. It is fetched the same way and drives only
danielblnc's runtime: its own lmxxf backend needs weights it does not publish.

Model E is HelixSR by lonewolf0622. Its release carries no NVIDIA material;
its own setup scripts build the network on the user's PC from NVIDIA's DLSS
DLL. build_model_e runs those scripts here, so the result never leaves this
machine and is never part of a release.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import urllib.request
import zipfile

RELEASES = "https://github.com/3zwr1/AMD-NR---OptiScaler/releases/download"
DANIEL_HOMEPAGE = "https://github.com/danielblnc/DLSS-NR-on-AMD"
DANIEL_PASSES = ("dlssnr_amd_pass1.dll", "dlssnr_amd_pass2.dll", "dlssnr_amd_pass3.dll")
DANIEL_WEIGHTS = "dlssnr_on_amd_weights.bin"
DANIEL_FILES = (*DANIEL_PASSES, DANIEL_WEIGHTS)
# The author's terms ship beside his files and travel with them.
DANIEL_NOTICES = ("danielblnc_ATTRIBUTION.txt", "danielblnc_LICENSE.txt")
# The released host searches <host folder>/OptiScaler/plugins for *.asi.
OPTIPATCHER_PATH = "OptiScaler/plugins/OptiPatcher.asi"
_MAX_MEMBER = 512 * 1024**2
_MAX_HOST = 256 * 1024**2


@dataclass(frozen=True)
class Runtime:
    version: str
    url: str
    sha256: str
    size: int
    pass_sha256: str
    generations: tuple


@dataclass(frozen=True)
class Plugin:
    version: str
    url: str
    sha256: str
    size: int


@dataclass(frozen=True)
class HostBuild:
    """An alternate OptiScaler host archive and how to stage it."""
    key: str
    name: str
    version: str
    url: str
    sha256: str
    size: int
    homepage: str
    author: str
    # Archive members copied into the package; everything else (installer
    # scripts, READMEs, assets for backends this fork cannot use) is left out.
    files: tuple
    folders: tuple

    @property
    def host_version(self):
        """Key into HOST_RUNTIMES, distinct from every AMDNR version."""
        return f"{self.key}-{self.version}"

    @property
    def title(self):
        return f"{self.name} {self.version}"


# Newest first: the first runtime a GPU and host both accept is the default.
# The three pass DLLs inside each archive are identical copies, one per pass.
DANIEL_RUNTIMES = (
    Runtime("0.6.0", f"{RELEASES}/Alpha0.3.5.5R4/v0.6.0-Runtime.zip",
            "d2bb17027b2e13e11e2198dd0bf70d4dc11fa9a6cba6bdd9fe5b44d9cf351f7e", 176366023,
            "195c4a891b6eac4c1cb7671e10ff62bbbe2b17f1dfae1344dc5a6714e4775721", ("rdna4", "rdna3", "rdna2")),
    Runtime("0.5.1", f"{RELEASES}/Alpha0.3.5/v0.5.1-Runtime.zip",
            "ed4c4debeced9a39c438ee992988dd2d3ad550a4d1d749437363d60e84129292", 149298852,
            "493b4a3b80a21f7255109172ab7bb01ba08d35f2941718f441768f1abfc48acd", ("rdna4", "rdna3")),
    Runtime("0.5.0", f"{RELEASES}/Alpha0.3.4.2/v0.5.0-Runtime.zip",
            "7a49ab0e7bea27c4b204c0d254ece305ddd87e88701f761692aa25a5e56ed46f", 149550553,
            "cddfb09e019347957bf7b96c95c0e900e8d3062dfaed697a8a96b0a039aec31a", ("rdna4", "rdna3")),
    Runtime("0.4.3", f"{RELEASES}/Alpha0.3.4.1/v0.4.3-Runtime.zip",
            "07dd777441af4d4f2bfe7a0c2fb1ecff3e03a970c3c3ca85cad2738e1e4b231e", 116484918,
            "d1e320862a8763ac39e7ce194536d4b6c55ba61bae9e8a92753cec32df67a457", ("rdna4", "rdna3")),
    Runtime("0.4.1", f"{RELEASES}/Alpha0.3.4.1/v0.4.1-Runtime.zip",
            "d6aa1ff24f4d1379b2914e31403c8027bf556d59f87ec265ea79502b60e4d492", 112707880,
            "823063eb4c76b1334fd1800c41798873ae61d4016af0406f1f0b9dce57b1d376", ("rdna4", "rdna3")),
    Runtime("0.4.0", f"{RELEASES}/Alpha0.3.4/v0.4.0-Runtime.zip",
            "15ebed60b7946f4eb616a8b89a6c8d8038886a2bfead2f9cf299175d0ec5b973", 113039884,
            "d62be3d8b9fbb3c6c81982c4ddb3dfa00eb9662e3206925cbe5b7e1bc6798b80", ("rdna4", "rdna3")),
    # danielblnc's own 0.3.1 build, the newest the pre-SR host identifies.
    Runtime("0.3.1", f"{RELEASES}/Alpha0.3.3/v0.3.1-Runtime.zip",
            "c026995e7d5ad964d944458b1c2899c6fe6e89228a4e3074433aed0a7d3eb343", 110992515,
            "b108d6407eb7f094a4f9111edd778eee7b978b648d413a9fc7aeedfdd914c154", ("rdna4", "rdna3")),
)

PRESR = HostBuild(
    "presr", "OptiScaler AMD pre-SR", "1.9.1-alpha",
    "https://github.com/TheAutomatic/dlss-5-amd-project/releases/download/v1.9.1-alpha/OptiScaler-AMD-PreSR-1.9.1-alpha.zip",
    "bc0799ee408f50a0766f250118022a6c030f46a3d2bf75e9715266ad2a3c247a", 132549025,
    "https://github.com/TheAutomatic/dlss-5-amd-project", "TheAutomatic",
    files=("OptiScaler.dll", "OptiScaler.ini"), folders=("OptiScaler/", "Licenses/", "experimental_lighting/"))
HOST_BUILDS = (PRESR,)

# Runtimes each pinned host drives, read from the pass digests each host
# binary pins (host_drives). Used to list choices before the host is
# downloaded; staging re-checks the actual binary. AMDNR 0.3.5.5R4, 0.3.5.6 and 0.3.5.8
# also pin 0.3.1, which is offered only with the pre-SR host, where it was tested.
HOST_RUNTIMES = {
    "0.3.4.2": ("0.5.0", "0.4.3", "0.4.1", "0.4.0"),
    "0.3.5.5R4": ("0.6.0", "0.5.1", "0.5.0", "0.4.3", "0.4.1", "0.4.0"),
    "0.3.5.6": ("0.6.0", "0.5.1", "0.5.0", "0.4.3", "0.4.1", "0.4.0"),
    "0.3.5.8": ("0.6.0", "0.5.1", "0.5.0", "0.4.3", "0.4.1", "0.4.0"),
    PRESR.host_version: ("0.3.1",),
}

# OptiScaler's game-patching ASI plugin, loaded from OptiScaler/plugins/ when
# [Plugins] LoadAsiPlugins=true. Newest first.
OPTIPATCHER = (
    Plugin("0.41", "https://github.com/optiscaler/OptiPatcher/releases/download/v0.41/OptiPatcher_v0.41.asi",
           "fb12735bfcc0d47f534f2206d57ec34129dc3d22b6405a1c2ef86745ab48b2eb", 95232),
    Plugin("0.40", "https://github.com/optiscaler/OptiPatcher/releases/download/v0.40/OptiPatcher_v0.40.asi",
           "a4ed60da15ea5ffb16cb94e1b029a4fb97dcc9e36f034e032e05ae0157119d9a", 78336),
)


@dataclass(frozen=True)
class Download:
    name: str
    version: str
    url: str
    sha256: str
    size: int


# Model E. HelixSR's own Linux setup builds with Microsoft's Windows DXC
# through Wine, from NVIDIA's DLSS 310.7.0 DLL; both pins are the ones its
# setup scripts verify. The NVIDIA DLL is fetched only after the user accepts
# NVIDIA's licence, used for one build and deleted.
HELIXSR_HOMEPAGE = "https://github.com/lonewolf0622/HelixSR"
HELIXSR = Download("HelixSR-1.8.1.zip", "1.8.1",
                   "https://github.com/lonewolf0622/HelixSR/releases/download/v1.8.1/HelixSR-1.8.1.zip",
                   "abab32b16acf2536b39663763c355f3cfd35c4876c90297d6b5481bd9ad03a19", 55766339)
DLSS_FOR_HELIXSR = Download("nvngx_dlss.dll", "310.7.0",
                            "https://raw.githubusercontent.com/NVIDIA/DLSS/v310.7.0/lib/Windows_x86_64/rel/nvngx_dlss.dll",
                            "be6e434a94ca32499515eb62ca0e6c274526055d568d0426e4c652dcdfb6ee6e", 58977904)
DLSS_LICENSE = "https://github.com/NVIDIA/DLSS/blob/v310.7.0/LICENSE.txt"
DXC_FOR_HELIXSR = Download("dxc_2026_07_29.zip", "1.9.2607",
                           "https://github.com/microsoft/DirectXShaderCompiler/releases/download/v1.9.2607/dxc_2026_07_29.zip",
                           "a1dfb116ba3eeae6a1582291b53a8e7bf65ad760676bd3194685c8f7367cd241", 41625275)
# AMDNR 0.3.5.6 reads [Libraries] HelixSRPath (absolute, or relative to the
# host DLL's folder) when [Upscalers] FfxSRProvider=helix_e. It loads the
# provider only after the receipt in that folder matches both DLLs: the first
# name as MainSHA256, the second as SR_SHA256. HelixSR needs two separate
# copies, as its own drop-in uses.
MODEL_E_FOLDER = "AMDNR-SR-E"
MODEL_E_RECEIPT = "amdnr-helix-e.ini"
MODEL_E_DLLS = ("amd_fidelityfx_dx12.dll", "amd_fidelityfx_upscaler_dx12.dll")
MODEL_E_PROVIDER = "helix_e"
# HelixSR's terms travel with its DLL.
HELIXSR_NOTICES = ("LICENSE", "THIRD_PARTY_NOTICES.md")
# HelixSR's setup appends the network to its DLL, ending in this footer.
_HELIXSR_NETWORK = b"HXSRNET1"
_HELIXSR_MEMBERS = 256
_BUILD_TIMEOUT = 20 * 60

# APUs: danielblnc's runtime does not run on handheld/integrated graphics, and
# the RDNA 2 APUs (Steam Deck class) have no supported runtime at all.
_APUS = frozenset({"gfx1033", "gfx1035", "gfx1036", "gfx1037", "gfx103c",
                   "gfx1103", "gfx1150", "gfx1151", "gfx1152", "gfx1153"})


def _sha(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def daniel_runtime(version):
    for runtime in DANIEL_RUNTIMES:
        if runtime.version == version:
            return runtime
    raise ValueError("Unknown danielblnc runtime version; choose " + ", ".join(r.version for r in DANIEL_RUNTIMES))


def optipatcher(version=None):
    if version is None:
        return OPTIPATCHER[0]
    for plugin in OPTIPATCHER:
        if plugin.version == version:
            return plugin
    raise ValueError("Unknown OptiPatcher version; choose " + ", ".join(p.version for p in OPTIPATCHER))


def host_build(key):
    """An alternate host by key; None and "amdnr" mean the AMDNR host."""
    if key in (None, "amdnr"):
        return None
    for build in HOST_BUILDS:
        if build.key == key:
            return build
    raise ValueError("Unknown host build; choose amdnr, " + ", ".join(build.key for build in HOST_BUILDS))


def host_build_for(digest):
    return next((build for build in HOST_BUILDS if build.sha256 == digest), None)


def runtime_for_pass(digest):
    return next((runtime for runtime in DANIEL_RUNTIMES if runtime.pass_sha256 == digest), None)


def host_drives(host, runtime):
    """Whether this OptiScaler host pins the runtime's pass DLL digest."""
    return _host_contains(host, bytes.fromhex(runtime.pass_sha256))


def host_loads_model_e(host):
    """Whether this host reads a Model E receipt (AMDNR 0.3.5.6 and later)."""
    return _host_contains(host, MODEL_E_RECEIPT.encode("utf-16-le"))


def _host_contains(host, needle):
    """Reads at most 256 MiB in overlapping chunks and never loads the image."""
    descriptor = os.open(host, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    details = os.fstat(descriptor)
    if not stat.S_ISREG(details.st_mode) or details.st_size > _MAX_HOST:
        os.close(descriptor)
        raise ValueError(f"Host DLL is not a regular file of supported size: {host}")
    with os.fdopen(descriptor, "rb") as source:
        tail = b""
        while chunk := source.read(1024 * 1024):
            if needle in tail + chunk:
                return True
            tail = chunk[-(len(needle) - 1):]
    return False


def _gfx(version):
    major, minor, step = version // 10000, (version // 100) % 100, version % 100
    return f"gfx{major}{minor:x}{step:x}"


def _generation(gfx):
    if gfx.startswith("gfx12"):
        return "rdna4"
    if gfx.startswith("gfx11"):
        return "rdna3"
    if gfx.startswith("gfx103"):
        return "rdna2"
    if gfx.startswith("gfx101"):
        return "rdna1"
    return "other"


def detect_gpu(topology=Path("/sys/class/kfd/kfd/topology/nodes")):
    """Describe the ROCm-visible GPU with the most compute units, or None.

    The bridge matches the game's adapter by PCI address; a discrete card
    beside an integrated GPU is the one games normally render on.
    """
    best = None
    try:
        nodes = sorted(topology.iterdir())
    except OSError:
        return None
    for node in nodes:
        try:
            text = (node / "properties").read_text(errors="replace")[:65536]
        except OSError:
            continue
        values = dict(re.findall(r"^(\w+) (\d+)$", text, re.M))
        version, simds = int(values.get("gfx_target_version", 0)), int(values.get("simd_count", 0))
        if not version or not simds:
            continue  # CPU node
        gfx = _gfx(version)
        if best is None or simds > best["simd_count"]:
            best = dict(gfx=gfx, generation=_generation(gfx), apu=gfx in _APUS, simd_count=simds)
    return best


def runtimes_for(gpu, host=None, host_version=None):
    """Runtimes this GPU may use and that the host (a DLL path, or a pinned
    host version) drives. An undetected GPU (None) is not filtered by GPU."""
    if gpu is not None and (gpu.get("apu") or gpu.get("generation") not in ("rdna4", "rdna3", "rdna2")):
        return []
    generation = gpu and gpu.get("generation")
    offered = [runtime for runtime in DANIEL_RUNTIMES if generation is None or generation in runtime.generations]
    if host_version is not None:
        offered = [runtime for runtime in offered if runtime.version in HOST_RUNTIMES.get(host_version, ())]
    return [runtime for runtime in offered if host is None or host_drives(host, runtime)]


def fetch(url, sha256, cache, name, size=None, user_agent="AMDNR-Linux"):
    """Download once into cache, verifying the pinned digest before use."""
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / name
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ValueError(f"Download cache entry must be a regular file: {target}")
    if target.is_file() and _sha(target) == sha256:
        return target
    pending = None
    try:
        with tempfile.NamedTemporaryFile(prefix=name + ".", suffix=".partial", dir=cache, delete=False) as output:
            pending = Path(output.name)
            request = urllib.request.Request(url, headers={"User-Agent": user_agent})
            with urllib.request.urlopen(request, timeout=60) as source:
                copied = 0
                while chunk := source.read(1024 * 1024):
                    copied += len(chunk)
                    if size is not None and copied > size:
                        raise ValueError(f"Download of {name} is larger than published; nothing was used.")
                    output.write(chunk)
        if _sha(pending) != sha256:
            raise ValueError(f"Downloaded {name} failed checksum verification; nothing was used.")
        pending.replace(target)
        return target
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def extract_daniel(archive, runtime, destination):
    """Extract exactly the runtime's files from a verified archive and check them.

    Members are named, flat and bounded; the three pass DLLs must all equal the
    pinned digest. Nothing from the archive is executed.
    """
    if _sha(archive) != runtime.sha256:
        raise ValueError(f"danielblnc runtime {runtime.version} archive checksum mismatch.")
    destination.mkdir(parents=True, exist_ok=False)
    allowed = set(DANIEL_FILES) | set(DANIEL_NOTICES)
    with zipfile.ZipFile(archive) as bundle:
        members = [entry for entry in bundle.infolist() if not entry.is_dir()]
        names = [entry.filename for entry in members]
        if len(set(names)) != len(names) or not set(DANIEL_FILES) <= set(names) or not set(names) <= allowed:
            raise ValueError(f"danielblnc runtime {runtime.version} archive has unexpected contents.")
        for entry in members:
            if entry.file_size > _MAX_MEMBER:
                raise ValueError(f"danielblnc runtime member is too large: {entry.filename}")
            with bundle.open(entry) as source, (destination / entry.filename).open("wb") as sink:
                shutil.copyfileobj(source, sink)
    for name in DANIEL_PASSES:
        if _sha(destination / name) != runtime.pass_sha256:
            raise ValueError(f"{name} in danielblnc runtime {runtime.version} does not match its pinned digest.")
    return destination


def prepare_daniel(version, cache, destination):
    runtime = daniel_runtime(version)
    archive = fetch(runtime.url, runtime.sha256, cache, f"danielblnc-v{runtime.version}-Runtime.zip", runtime.size)
    return runtime, extract_daniel(archive, runtime, destination)


def prepare_optipatcher(version, cache):
    plugin = optipatcher(version)
    return plugin, fetch(plugin.url, plugin.sha256, cache, f"OptiPatcher_v{plugin.version}.asi", plugin.size)


def prepare_host(key, cache):
    build = host_build(key)
    return build, fetch(build.url, build.sha256, cache, build.url.rsplit("/", 1)[1], build.size)


class ModelEUnavailable(ValueError):
    """This system lacks something HelixSR's setup needs to build Model E."""


class ModelEConsentNeeded(ValueError):
    """Building Model E needs NVIDIA's DLSS DLL, and the user has not accepted its licence."""


def helixsr_built(path):
    """Whether path is HelixSR's DLL carrying the network its setup appends."""
    try:
        with Path(path).open("rb") as source:
            size = os.fstat(source.fileno()).st_size
            if size < 64 or source.read(2) != b"MZ":
                return False
            source.seek(-32, os.SEEK_END)
            footer = source.read(32)
    except OSError:
        return False
    if footer[:8] != _HELIXSR_NETWORK:
        return False
    weights, kernels = int.from_bytes(footer[8:16], "little"), int.from_bytes(footer[16:24], "little")
    return weights > 0 and kernels > 0 and weights + kernels + 32 < size


def model_e_receipt(main_sha256, sr_sha256):
    """The receipt AMDNR checks before it loads HelixSR: one [HelixSR]
    section holding exactly these five keys, digests in lowercase hex."""
    for value in (main_sha256, sr_sha256):
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("Model E receipt digests must be lowercase SHA-256 values.")
    return ("[HelixSR]\nSchema=1\nProvider=HelixSR\nModel=E\n"
            f"MainSHA256={main_sha256}\nSR_SHA256={sr_sha256}\n")


def stage_model_e(built, package):
    """Lay out a built Model E as AMDNR loads it; returns the DLL digest."""
    dll = Path(built) / MODEL_E_DLLS[0]
    if dll.is_symlink() or not helixsr_built(dll):
        raise ValueError(f"Not a built HelixSR provider: {dll}")
    folder = Path(package) / MODEL_E_FOLDER
    folder.mkdir()
    for name in MODEL_E_DLLS:
        shutil.copyfile(dll, folder / name)
    for name in HELIXSR_NOTICES:
        if (Path(built) / name).is_file():
            shutil.copyfile(Path(built) / name, folder / ("HelixSR_" + name))
    digests = [_sha(folder / name) for name in MODEL_E_DLLS]
    if len(set(digests)) != 1:
        raise ValueError("Model E provider copies differ; nothing was staged.")
    (folder / MODEL_E_RECEIPT).write_text(model_e_receipt(*digests))
    return digests[0]


def model_e_folder(cache):
    return Path(cache) / "model-e" / f"HelixSR-{HELIXSR.version}"


def model_e_build(cache):
    """A Model E built earlier into cache, as (folder, details), or None."""
    return load_model_e(model_e_folder(cache))


def load_model_e(folder):
    """A built Model E folder and its build record, or None if it does not check out."""
    folder = Path(folder)
    dll = folder / MODEL_E_DLLS[0]
    try:
        details = json.loads((folder / "build.json").read_text())
    except (OSError, ValueError):
        return None
    if (not isinstance(details, dict) or details.get("helixsr_sha256") != HELIXSR.sha256 or
            dll.is_symlink() or not dll.is_file() or _sha(dll) != details.get("sha256") or not helixsr_built(dll)):
        return None
    return folder, details


def _child_env():
    # The packaged manager runs its own Python; never let its settings reach another interpreter.
    return {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}


def find_numpy_python(candidates=None):
    """A Python 3.8 or newer that can import numpy, which HelixSR's setup needs."""
    for candidate in candidates if candidates is not None else (sys.executable, shutil.which("python3")):
        if not candidate:
            continue
        try:
            probe = subprocess.run([str(candidate), "-c", "import sys, numpy; sys.exit(sys.version_info < (3, 8))"],
                                   env=_child_env(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=120)
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return str(candidate)
    return None


def find_wine(steam_roots=None):
    """A Wine for Microsoft's Windows shader compiler: a Steam Proton, else the system's."""
    home = Path.home()
    roots = steam_roots if steam_roots is not None else (
        home / ".local/share/Steam", home / ".steam/steam", home / ".steam/root",
        home / ".var/app/com.valvesoftware.Steam/data/Steam")
    for root in roots:
        for pattern in ("steamapps/common/Proton*/files/bin/wine", "compatibilitytools.d/*/files/bin/wine"):
            for path in sorted(Path(root).glob(pattern), reverse=True):
                if path.is_file() and os.access(path, os.X_OK):
                    return path
    system = shutil.which("wine")
    return Path(system) if system else None


def _extract_helixsr(archive, destination):
    """Unpack the verified release into destination; returns its HelixSR folder."""
    if _sha(archive) != HELIXSR.sha256:
        raise ValueError("HelixSR archive checksum mismatch.")
    root = f"HelixSR-{HELIXSR.version}"
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        if len(members) > _HELIXSR_MEMBERS:
            raise ValueError("HelixSR archive has unexpected contents.")
        # Every member is checked before anything is written.
        for entry in members:
            path = PurePosixPath(entry.filename)
            if (path.is_absolute() or not path.parts or path.parts[0] != root or ".." in path.parts or
                    "\\" in entry.filename or stat.S_ISLNK(entry.external_attr >> 16) or entry.file_size > _MAX_MEMBER):
                raise ValueError(f"HelixSR archive has an unexpected member: {entry.filename}")
        for entry in members:
            path, mode = PurePosixPath(entry.filename), entry.external_attr >> 16
            target = Path(destination).joinpath(*path.parts)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(entry) as source, target.open("xb") as sink:
                shutil.copyfileobj(source, sink)
            if mode & 0o111:
                target.chmod(0o755)
    return Path(destination) / root


def _extract_dxc(archive, destination):
    """The three files of Microsoft's x64 compiler; nothing else is unpacked."""
    if _sha(archive) != DXC_FOR_HELIXSR.sha256:
        raise ValueError("Shader compiler archive checksum mismatch.")
    wanted = {f"bin\\x64\\{name}": name for name in ("dxc.exe", "dxcompiler.dll", "dxil.dll")}
    Path(destination).mkdir(parents=True)
    with zipfile.ZipFile(archive) as bundle:
        found = {entry.filename: entry for entry in bundle.infolist() if entry.filename in wanted}
        if found.keys() != wanted.keys():
            raise ValueError("Shader compiler archive has unexpected contents.")
        for name, entry in found.items():
            with bundle.open(entry) as source, (Path(destination) / wanted[name]).open("xb") as sink:
                shutil.copyfileobj(source, sink)
    return Path(destination) / "dxc.exe"


def _dxc_wrapper(path, wine, dxc, prefix):
    """HelixSR's setup calls the compiler with Linux paths; Wine needs them as Z: paths."""
    path.write_text(
        "#!/bin/sh\n"
        "n=$#\n"
        "while [ \"$n\" -gt 0 ]; do\n"
        "    a=$1; shift\n"
        "    case $a in /*) a=Z:$(printf '%s' \"$a\" | tr / '\\\\') ;; esac\n"
        "    set -- \"$@\" \"$a\"; n=$((n - 1))\n"
        "done\n"
        f"WINEPREFIX={shlex.quote(str(prefix))} WINEDEBUG=-all WINEDLLOVERRIDES='mscoree,mshtml=' "
        f"exec {shlex.quote(str(wine))} {shlex.quote(str(dxc))} \"$@\"\n")
    path.chmod(0o755)


def _run_setup(command, cwd, env, progress):
    output = []
    with subprocess.Popen([str(part) for part in command], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                          start_new_session=True) as process:
        def stop():
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        # A build that stops writing would otherwise hold the output pipe open forever.
        timer = threading.Timer(_BUILD_TIMEOUT, stop)
        timer.start()
        try:
            for line in process.stdout:
                output.append(line.rstrip())
                del output[:-40]
                # Its drop-in folder is inside the temporary build folder, which is deleted.
                if line.startswith("[helixsr-setup]") and "drop-in" not in line:
                    progress("Model E: " + line.removeprefix("[helixsr-setup]").strip())
            process.wait()
        except BaseException:
            stop()
            raise
        finally:
            timed_out = not timer.is_alive() and process.returncode == -signal.SIGKILL
            timer.cancel()
    if timed_out:
        raise ValueError(f"HelixSR's setup did not finish within {_BUILD_TIMEOUT // 60} minutes; it was stopped.")
    if process.returncode:
        raise ValueError("HelixSR's setup could not build Model E:\n" + "\n".join(output[-15:]))


def build_model_e(cache, dlss=None, accept_dlss_license=False, wine=None, python=None, progress=print):
    """Build Model E with HelixSR's own setup scripts; returns (folder, details).

    The network is built from NVIDIA's DLSS 310.7.0 DLL: a copy the user
    already has (dlss, for example from a game), or NVIDIA's own download
    when accept_dlss_license is True. Either way it is used for this build
    only. The result stays in cache and is reused until HelixSR's pin changes.
    """
    existing = model_e_build(cache)
    if existing is not None:
        return existing
    if dlss is not None:
        dlss = Path(dlss).expanduser().resolve()
        if not dlss.is_file() or _sha(dlss) != DLSS_FOR_HELIXSR.sha256:
            raise ValueError(f"Model E is built from NVIDIA's DLSS {DLSS_FOR_HELIXSR.version} DLL "
                             f"(SHA-256 {DLSS_FOR_HELIXSR.sha256}); {dlss} is a different file.")
    elif not accept_dlss_license:
        raise ModelEConsentNeeded(
            f"Building Model E needs NVIDIA's DLSS {DLSS_FOR_HELIXSR.version} DLL. It is downloaded from NVIDIA's "
            f"GitHub only after you accept NVIDIA's licence ({DLSS_LICENSE}).")
    python = python or find_numpy_python()
    if python is None:
        raise ModelEUnavailable("Building Model E needs Python 3 with numpy. Install your distribution's numpy "
                                "package (python-numpy or python3-numpy) and try again.")
    wine = Path(wine) if wine else find_wine()
    if wine is None or not wine.is_file():
        raise ModelEUnavailable("Building Model E needs Proton or Wine to run Microsoft's shader compiler.")
    progress(f"Model E: downloading HelixSR {HELIXSR.version} ({HELIXSR_HOMEPAGE}) and Microsoft's shader compiler.")
    release = fetch(HELIXSR.url, HELIXSR.sha256, Path(cache), HELIXSR.name, HELIXSR.size)
    compiler = fetch(DXC_FOR_HELIXSR.url, DXC_FOR_HELIXSR.sha256, Path(cache), DXC_FOR_HELIXSR.name, DXC_FOR_HELIXSR.size)
    destination = model_e_folder(cache)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="build-", dir=destination.parent) as temporary:
        work = Path(temporary)
        source = _extract_helixsr(release, work / "release")
        dxc = _extract_dxc(compiler, work / "dxc")
        if dlss is None:
            progress(f"Model E: downloading NVIDIA's DLSS {DLSS_FOR_HELIXSR.version} DLL from NVIDIA's GitHub.")
            dlss = fetch(DLSS_FOR_HELIXSR.url, DLSS_FOR_HELIXSR.sha256, work, DLSS_FOR_HELIXSR.name, DLSS_FOR_HELIXSR.size)
        prefix = work / "wineprefix"
        _dxc_wrapper(work / "dxc.sh", wine, dxc, prefix)
        progress("Model E: building HelixSR's network on this PC (about a minute on a desktop CPU).")
        env = _child_env()
        try:
            _run_setup([python, source / "setup/helixsr_setup.py", source, "--dxc", work / "dxc.sh",
                        "--dlss", dlss, "--yes"], work, env, progress)
        finally:
            server = wine.parent / "wineserver"
            if prefix.exists() and (server.is_file() or shutil.which("wineserver")):
                try:
                    subprocess.run([str(server) if server.is_file() else "wineserver", "-k"], env=dict(env, WINEPREFIX=str(prefix)),
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
                except (OSError, subprocess.SubprocessError):
                    pass
        built = source / MODEL_E_DLLS[0]
        if not helixsr_built(built):
            raise ValueError("HelixSR's setup finished, but its DLL does not carry the network.")
        result = work / "built"
        result.mkdir()
        shutil.copyfile(built, result / MODEL_E_DLLS[0])
        for name in HELIXSR_NOTICES:
            shutil.copyfile(source / name, result / name)
        try:
            setup_record = json.loads((source / "helixsr_setup.json").read_text())
        except (OSError, ValueError):
            setup_record = None
        details = {"helixsr_version": HELIXSR.version, "helixsr_sha256": HELIXSR.sha256,
                   "dlss_version": DLSS_FOR_HELIXSR.version, "dlss_sha256": DLSS_FOR_HELIXSR.sha256,
                   "dxc_sha256": DXC_FOR_HELIXSR.sha256, "sha256": _sha(result / MODEL_E_DLLS[0]),
                   "helixsr_setup": setup_record}
        (result / "build.json").write_text(json.dumps(details, indent=2) + "\n")
        if destination.exists() or destination.is_symlink():
            if destination.is_symlink() or not destination.is_dir():
                raise ValueError(f"Model E cache entry is not a folder: {destination}")
            shutil.rmtree(destination)
        result.rename(destination)
    progress("Model E: built. It contains NVIDIA's network and stays on this PC.")
    return destination, details
