"""Install what build.py needs on Linux x86_64 (the GitLab CI job and the Dockerfile).

    python packaging/macos/install_tools.py [--bin-dir /usr/local/bin]

zig comes from PyPI (ziglang); rcodesign from its GitHub release, checked against a
pinned SHA-256. On Apple Silicon run the container as linux/amd64.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import platform
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

PIP_PACKAGES = ["ziglang==0.16.0", "pillow>=10.1", "packaging>=24"]
RCODESIGN_URL = ("https://github.com/indygreg/apple-platform-rs/releases/download/apple-codesign%2F0.29.0/"
                 "apple-codesign-0.29.0-x86_64-unknown-linux-musl.tar.gz")
RCODESIGN_SHA256 = "dbe85cedd8ee4217b64e9a0e4c2aef92ab8bcaaa41f20bde99781ff02e600002"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin-dir", type=Path, default=Path("/usr/local/bin"))
    args = ap.parse_args()
    if sys.platform != "linux" or platform.machine() != "x86_64":
        sys.exit("install_tools.py is for Linux x86_64 (use --platform linux/amd64 with Docker on a Mac); "
                 "on a Mac, `pip install ziglang` and get rcodesign from its GitHub release")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", *PIP_PACKAGES], check=True)
    with urllib.request.urlopen(RCODESIGN_URL, timeout=120) as r:
        data = r.read()
    if hashlib.sha256(data).hexdigest() != RCODESIGN_SHA256:
        sys.exit("rcodesign download doesn't match its checksum")
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        member = next(m for m in tar.getmembers() if m.name.endswith("/rcodesign"))
        dest = args.bin_dir / "rcodesign"
        dest.write_bytes(tar.extractfile(member).read())
        dest.chmod(0o755)
    print(f"installed {', '.join(PIP_PACKAGES)} and {dest}")


if __name__ == "__main__":
    main()
