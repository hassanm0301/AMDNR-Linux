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
"""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
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
# downloaded; staging re-checks the actual binary. AMDNR 0.3.5.5R4 also pins
# 0.3.1, which is offered only with the pre-SR host, where it was tested.
HOST_RUNTIMES = {
    "0.3.4.2": ("0.5.0", "0.4.3", "0.4.1", "0.4.0"),
    "0.3.5.5R4": ("0.6.0", "0.5.1", "0.5.0", "0.4.3", "0.4.1", "0.4.0"),
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
    """Whether this OptiScaler host pins the runtime's pass DLL digest.

    Reads at most 256 MiB in overlapping chunks and never loads the image.
    """
    needle = bytes.fromhex(runtime.pass_sha256)
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
