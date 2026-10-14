#!/usr/bin/env python3
"""Extract a private ROCm runtime. No root, package installation, or maintainer scripts.

Package pins verified against the d4r packaging recipe; all downloads are from
AMD and Ubuntu, with SHA-256 verification before extraction.
"""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

AMD = "https://repo.radeon.com/rocm/apt/7.2.4/pool/main/"
UBUNTU = "https://archive.ubuntu.com/ubuntu/pool/main/"
PACKAGES = [
    (AMD + "h/hip-runtime-amd/hip-runtime-amd_7.2.53211.70204-93~22.04_amd64.deb", "2b0fc15d4456bc35370f33672b202b43f2af1aceac47a42c22d1370b2f46968f"),
    (AMD + "c/comgr/comgr_3.0.0.70204-93~22.04_amd64.deb", "58460d117131369a248d334ba749609abea8591cd05752ac572c9a10f31ac006"),
    (AMD + "h/hsa-rocr/hsa-rocr_1.18.0.70204-93~22.04_amd64.deb", "7fccbb91e38fadd126538f15e02c66e20db717a60eb03f2a045e47497bf67405"),
    (AMD + "r/rocprofiler-register/rocprofiler-register_0.6.0.70204-93~22.04_amd64.deb", "c2780e37a7217329b5d3d40e682ae6979481b1a59b86415a0cdf849b9c142bfd"),
    (UBUNTU + "e/elfutils/libelf1_0.186-1ubuntu0.1_amd64.deb", "2cc781614b045b450a1e073ee3b0902f393ee58ede1d2ea4526ad805fcccf538"),
    (UBUNTU + "n/numactl/libnuma1_2.0.14-3ubuntu2_amd64.deb", "0721c89001fbbd1ada23e89da5d60e762763c1a7b3dc814a2e9a518480a8043d"),
]
LIBRARIES = {
    "libamdhip64.so.7": "opt/rocm-7.2.4/lib/libamdhip64.so.7",
    "libamd_comgr.so.3": "opt/rocm-7.2.4/lib/libamd_comgr.so.3",
    "libhsa-runtime64.so.1": "opt/rocm-7.2.4/lib/libhsa-runtime64.so.1",
    "librocprofiler-register.so.0": "opt/rocm-7.2.4/lib/librocprofiler-register.so.0",
    "libelf.so.1": "usr/lib/x86_64-linux-gnu/libelf.so.1",
    "libnuma.so.1": "usr/lib/x86_64-linux-gnu/libnuma.so.1",
}
LICENSES = {
    "HIP-LICENSE.md": "opt/rocm-7.2.4/share/doc/hip/LICENSE.md",
    "comgr-LICENSE.txt": "opt/rocm-7.2.4/share/doc/amd_comgr/LICENSE.txt",
    "ROCr-LICENSE.md": "opt/rocm-7.2.4/share/doc/hsa-rocr/LICENSE.md",
    "rocprofiler-register-LICENSE.md": "opt/rocm-7.2.4/share/doc/rocprofiler-register/LICENSE.md",
    "libelf-copyright.txt": "usr/share/doc/libelf1/copyright",
    "libnuma-copyright.txt": "usr/share/doc/libnuma1/copyright",
}

def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()

def fetch(output, cache):
    if output.exists():
        raise ValueError(f"Output already exists: {output}")
    cache.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="amdnr-rocm-", dir=output.parent) as temp:
        root = Path(temp) / "extract"
        root.mkdir()
        for url, expected in PACKAGES:
            package = cache / url.rsplit("/", 1)[1]
            if not package.exists() or digest(package) != expected:
                partial = package.with_suffix(".partial")
                print(f"Downloading {package.name}", flush=True)
                with urllib.request.urlopen(url, timeout=60) as source, partial.open("wb") as target:
                    shutil.copyfileobj(source, target)
                if digest(partial) != expected:
                    partial.unlink()
                    raise ValueError(f"Checksum mismatch: {url}")
                partial.replace(package)
            entries = subprocess.check_output(["ar", "t", str(package)], text=True).splitlines()
            data = next(name for name in entries if name.startswith("data.tar"))
            archive = Path(temp) / data
            with archive.open("wb") as target:
                subprocess.run(["ar", "p", str(package), data], stdout=target, check=True)
            with tarfile.open(archive) as tar:
                tar.extractall(root, filter="data")
            archive.unlink()
        stage = Path(temp) / "runtime"
        for directory, items in (("lib", LIBRARIES), ("licenses", LICENSES)):
            (stage / directory).mkdir(parents=True)
            for name, source in items.items():
                shutil.copy2(root / source, stage / directory / name)
        (stage / "packages.sha256").write_text("\n".join(f"{sha}  {url}" for url, sha in PACKAGES) + "\n")
        stage.rename(output)
    print(f"Private ROCm runtime: {output}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--cache", type=Path, default=Path(__file__).resolve().parent / "deps/packages")
    args = parser.parse_args()
    fetch(args.output.resolve(), args.cache.resolve())
