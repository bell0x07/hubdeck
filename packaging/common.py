"""What the macOS and Windows builds share: version, downloads, wheels, bytecode, icon, zip.

Imported by packaging/macos/build.py and packaging/windows/build.py, which put this folder on
sys.path. Both run on Linux (Docker, GitLab CI); the Windows one also runs on Windows.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))             # hubdeck itself: the icon glyph, the bundle id

PYTHON = "3.13"                                   # the bundled Python, from python-build-standalone:
PBS_URL = "https://github.com/astral-sh/python-build-standalone/releases/download/20261009/"


def log(msg: str):
    print(f"==> {msg}", flush=True)


def run(cmd: list, **kw):
    print("   $", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def fetch(url: str, sha256: str, cache: Path) -> Path:
    dest = cache / url.rsplit("/", 1)[1]
    if not dest.exists() or hashlib.sha256(dest.read_bytes()).hexdigest() != sha256:
        log(f"downloading {dest.name}")
        cache.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=120) as r:
            data = r.read()
        if hashlib.sha256(data).hexdigest() != sha256:
            sys.exit(f"checksum mismatch for {url}")
        dest.write_bytes(data)
    return dest


def zig() -> list:
    """zig from PyPI (`pip install ziglang`) when installed, else the one on the PATH."""
    import importlib.util
    return [sys.executable, "-m", "ziglang"] if importlib.util.find_spec("ziglang") else [shutil.which("zig") or "zig"]


def remove(folder: Path, patterns: list[str]):
    for pattern in patterns:
        for p in folder.glob(pattern):
            shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()


def project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]


def base_version() -> str:
    """hubdeck.__version__, read without importing (the single source of the version)."""
    text = (ROOT / "src/hubdeck/__init__.py").read_text()
    return re.search(r'^__version__ = "([^"]+)"', text, re.M).group(1)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "unknown"


def git(*args: str) -> str:
    """git's output, or "" without git or a repository (e.g. in a Docker builder)."""
    try:
        return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def build_version(env=os.environ) -> str:
    """X.Y.Z for a release tag, <base>-<branch>.<pipeline> for anything else."""
    base = base_version()
    tag = env.get("CI_COMMIT_TAG")
    if tag:
        if tag.removeprefix("v") != base:
            sys.exit(f"tag {tag} doesn't match hubdeck.__version__ {base}: bump src/hubdeck/__init__.py first")
        return base
    if env.get("CI"):
        return f"{base}-{slug(env.get('CI_COMMIT_REF_NAME', ''))}.{env.get('CI_PIPELINE_IID', '0')}"
    return f"{base}-{slug(git('rev-parse', '--abbrev-ref', 'HEAD'))}-local"


def commit_time(env=os.environ) -> time.struct_time:
    """When the built commit was made: the date files get inside the zip."""
    stamp = env.get("CI_COMMIT_TIMESTAMP")       # ISO 8601
    if stamp:
        from datetime import datetime
        return datetime.fromisoformat(stamp).astimezone().timetuple()
    out = git("log", "-1", "--format=%ct")
    return time.localtime(int(out) if out else time.time())


def requirements(markers: dict) -> list[str]:
    """The project's dependencies for the target system. pip judges markers by the machine it
    runs on, so on Linux it would drop the target's own ones: name them explicitly."""
    from packaging.requirements import Requirement
    env = {"python_version": PYTHON, "python_full_version": PYTHON + ".0", "implementation_name": "cpython",
           "platform_python_implementation": "CPython", **markers}
    out = []
    for spec in project()["dependencies"]:
        req = Requirement(spec)
        if req.marker is None or req.marker.evaluate(env):
            req.marker = None
            out.append(str(req))
    return out


def install_packages(target: Path, platforms: list[str], reqs: list[str], tmp: Path, prune: list[str]):
    """hubdeck and its dependencies into `target`, as wheels for the target system."""
    log("building the hubdeck wheel")
    run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-q", "-w", tmp, ROOT])
    wheel = next(tmp.glob("hubdeck-*.whl"))
    log("installing hubdeck and its dependencies")
    cmd = [sys.executable, "-m", "pip", "install", "-q", "--target", target, "--only-binary=:all:",
           "--implementation", "cp", "--python-version", PYTHON, "--abi", "cp" + PYTHON.replace(".", ""),
           "--abi", "abi3", "--abi", "none", "--no-compile"]
    for plat in platforms:
        cmd += ["--platform", plat]
    run(cmd + [wheel] + reqs)
    remove(target, prune)


def compile_bytecode(folder: Path):
    """Precompiled, hash-based .pyc: valid whatever the files' dates after unzipping or
    installing, so nothing is compiled at startup (the app's folder is never written to)."""
    if sys.version_info[:2] != tuple(map(int, PYTHON.split("."))):
        log(f"skipping bytecode: building with Python {sys.version_info[0]}.{sys.version_info[1]}, need {PYTHON}")
        return
    log("compiling bytecode")
    for p in folder.rglob("__pycache__"):
        shutil.rmtree(p)
    run([sys.executable, "-m", "compileall", "-q", "-j0", "--invalidation-mode", "unchecked-hash", folder])


def draw_icon(size: int, tile: int, radius: int):
    """The tray glyph on a dark rounded tile, `tile` px wide in a `size` px canvas."""
    from PIL import Image, ImageDraw

    from hubdeck.render import tabler
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    top = Image.new("RGBA", (tile, tile))
    d = ImageDraw.Draw(top)
    for y in range(tile):                          # dark vertical gradient
        t = y / tile
        d.line([(0, y), (tile, y)], fill=(int(46 - 20 * t), int(50 - 22 * t), int(58 - 26 * t), 255))
    mask = Image.new("L", (tile, tile), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, tile - 1, tile - 1), radius=radius, fill=255)
    img.paste(top, ((size - tile) // 2,) * 2, mask)
    glyph = chr(tabler.codepoint("layout-grid"))
    ImageDraw.Draw(img).text((size / 2, size / 2), glyph, font=tabler.font(round(tile * 0.68)),
                             fill=(60, 200, 110, 255), anchor="mm")
    return img


def zip_folder(folder: Path, dest: Path, when: time.struct_time):
    """Zip `folder` itself (its name is the top entry) keeping permissions and symlinks, as
    Finder's Archive Utility expects; every entry is dated `when` (zip's own default is
    1 January 1980)."""
    log(f"writing {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted([folder, *folder.rglob("*")]):
            arc = path.relative_to(folder.parent).as_posix() + ("/" if path.is_dir() and not path.is_symlink() else "")
            info = zipfile.ZipInfo(arc, date_time=tuple(when)[:6])
            st = path.lstat()
            info.external_attr = (st.st_mode & 0xFFFF) << 16
            info.create_system = 3                     # unix, so the modes count
            if path.is_symlink():
                z.writestr(info, os.readlink(path))
            elif path.is_dir():
                z.writestr(info, b"")
            else:
                info.compress_type = zipfile.ZIP_DEFLATED
                with open(path, "rb") as f:
                    z.writestr(info, f.read())
