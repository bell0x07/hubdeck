"""Build hubdeck.app for macOS on Linux or macOS - no Xcode needed.

    python packaging/macos/build.py [--arch arm64|x86_64] [--out dist]

Produces dist/hubdeck-<version>-macos-<arch>.zip holding hubdeck.app: a standalone Python
(python-build-standalone), hubdeck and its dependencies (macOS wheels), and a small launcher
(launcher.c, cross-compiled with zig) so the app runs as hubdeck.app itself.

Needs: Python 3.13 with pip, Pillow and packaging; zig (`pip install ziglang`); rcodesign
(https://github.com/indygreg/apple-platform-rs) for signing - or, on a Mac, codesign.

Signing, from the environment:
  (nothing)                       ad-hoc. macOS forgets the app's permissions on every update.
  MACOS_SIGN_PEM                  sign with this certificate: a PEM file holding certificate and
                                  private key (text, so it fits a GitLab "File" variable). A
                                  self-signed one is enough for permissions to survive updates.
  MACOS_SIGN_P12 / _PASSWORD      the same as a .p12 file and its password.
  MACOS_NOTARY_API_KEY            App Store Connect API key (JSON from `rcodesign
                                  encode-app-store-connect-api-key`): also notarize and staple.
                                  Needs a Developer ID certificate.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))
from hubdeck._bundle import BUNDLE_ID  # noqa: E402

PYTHON = "3.13"
PBS_URL = "https://github.com/astral-sh/python-build-standalone/releases/download/20261009/"
PBS = {   # arch: (file, sha256)
    "arm64": ("cpython-3.13.16+20261009-aarch64-apple-darwin-install_only_stripped.tar.gz",
              "11785155d0f70e634178219c20925d8f20be00a3da4684fc4f5019dd15602ef7"),
    "x86_64": ("cpython-3.13.16+20261009-x86_64-apple-darwin-install_only_stripped.tar.gz",
               "bd085c51e960f00ee33b630e2b13ac90fe90cdc3da25a0f0bc0719b1294920bf"),
}
WHEEL_PLATFORMS = {
    "arm64": ["macosx_11_0_arm64", "macosx_10_9_universal2"],
    "x86_64": ["macosx_10_13_x86_64", "macosx_10_9_x86_64", "macosx_10_9_universal2"],
}
ZIG_TARGET = {"arm64": "aarch64-macos.11.0", "x86_64": "x86_64-macos.11.0"}
MIN_MACOS = "11.0"

# parts of the standalone Python hubdeck never uses
PRUNE = ["bin", "include", "share", "lib/pkgconfig", "lib/itcl*", "lib/tcl*", "lib/tk*", "lib/thread*",
         "lib/libtcl*", "lib/libtk*", "lib/python3.13/test", "lib/python3.13/idlelib", "lib/python3.13/tkinter",
         "lib/python3.13/turtledemo", "lib/python3.13/ensurepip", "lib/python3.13/config-3.13-darwin",
         "lib/python3.13/lib-dynload/_tkinter*", "lib/python3.13/site-packages/*"]

# from the installed packages: scripts, test suites, debug symbols (which also trip up signing)
APP_PRUNE = ["bin", "PyObjCTest", "**/*.dSYM", "**/tests", "**/test"]


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


def project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]


def darwin_requirements() -> list[str]:
    """The project's dependencies for macOS. pip judges markers by the machine it runs on,
    so on Linux it would drop the macOS-only ones: name them explicitly."""
    from packaging.requirements import Requirement
    env = {"sys_platform": "darwin", "platform_system": "Darwin", "os_name": "posix",
           "python_version": PYTHON, "python_full_version": PYTHON + ".0", "implementation_name": "cpython",
           "platform_python_implementation": "CPython", "platform_machine": "arm64"}
    out = []
    for spec in project()["dependencies"]:
        req = Requirement(spec)
        if req.marker is None or req.marker.evaluate(env):
            req.marker = None
            out.append(str(req))
    return out


def install_python(app: Path, arch: str, cache: Path):
    name, sha = PBS[arch]
    archive = fetch(PBS_URL + name, sha, cache)
    log("unpacking Python")
    dest = app / "Contents/Resources"
    with tarfile.open(archive) as tar:
        tar.extractall(dest, filter="tar")          # -> Resources/python
    py = dest / "python"
    for pattern in PRUNE:
        for p in py.glob(pattern):
            shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()


def install_packages(app: Path, arch: str, tmp: Path):
    log("building the hubdeck wheel")
    run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-q", "-w", tmp, ROOT])
    wheel = next(tmp.glob("hubdeck-*.whl"))
    log("installing hubdeck and its macOS dependencies")
    target = app / "Contents/Resources/app"
    cmd = [sys.executable, "-m", "pip", "install", "-q", "--target", target, "--only-binary=:all:",
           "--implementation", "cp", "--python-version", PYTHON, "--abi", "cp" + PYTHON.replace(".", ""),
           "--abi", "abi3", "--abi", "none", "--no-compile"]
    for plat in WHEEL_PLATFORMS[arch]:
        cmd += ["--platform", plat]
    run(cmd + [wheel] + darwin_requirements())
    for pattern in APP_PRUNE:
        for junk in target.glob(pattern):
            shutil.rmtree(junk) if junk.is_dir() else junk.unlink()


def compile_bytecode(app: Path):
    """Precompiled, hash-based .pyc: valid whatever the files' dates after unzipping, so
    nothing is compiled at startup (the signed bundle is never written to)."""
    if sys.version_info[:2] != tuple(map(int, PYTHON.split("."))):
        log(f"skipping bytecode: building with Python {sys.version_info[0]}.{sys.version_info[1]}, need {PYTHON}")
        return
    log("compiling bytecode")
    for p in app.rglob("__pycache__"):
        shutil.rmtree(p)
    run([sys.executable, "-m", "compileall", "-q", "-j0", "--invalidation-mode", "unchecked-hash",
         app / "Contents/Resources"])


def build_launcher(app: Path, arch: str):
    log("compiling the launcher")
    out = app / "Contents/MacOS/hubdeck"
    out.parent.mkdir(parents=True, exist_ok=True)
    zig = [sys.executable, "-m", "ziglang"] if _has_module("ziglang") else [shutil.which("zig") or "zig"]
    run(zig + ["cc", "-target", ZIG_TARGET[arch], "-O2", "-Wall", "-Werror",
               f'-DPYTHON_LIB="libpython{PYTHON}.dylib"', "-o", out, HERE / "launcher.c"])
    out.chmod(0o755)


def _has_module(name: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(name) is not None


def write_icon(app: Path):
    """The tray glyph on a rounded tile, as macOS app icons look."""
    log("drawing the icon")
    from PIL import Image, ImageDraw

    from hubdeck.render import tabler
    size, tile = 1024, 824                         # Apple's grid: an 824 px tile in a 1024 canvas
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    top = Image.new("RGBA", (tile, tile))
    d = ImageDraw.Draw(top)
    for y in range(tile):                          # dark vertical gradient
        t = y / tile
        d.line([(0, y), (tile, y)], fill=(int(46 - 20 * t), int(50 - 22 * t), int(58 - 26 * t), 255))
    mask = Image.new("L", (tile, tile), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, tile - 1, tile - 1), radius=185, fill=255)
    img.paste(top, ((size - tile) // 2,) * 2, mask)
    glyph = chr(tabler.codepoint("layout-grid"))
    ImageDraw.Draw(img).text((size / 2, size / 2), glyph, font=tabler.font(560), fill=(60, 200, 110, 255),
                             anchor="mm")
    img.save(app / "Contents/Resources/hubdeck.icns", format="ICNS")


def write_info_plist(app: Path, version: str, build: str):
    info = {
        "CFBundleName": "hubdeck",
        "CFBundleDisplayName": "hubdeck",
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": "hubdeck",
        "CFBundleIconFile": "hubdeck",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": build,
        "CFBundleInfoDictionaryVersion": "6.0",
        "LSMinimumSystemVersion": MIN_MACOS,
        "LSUIElement": True,                       # menu-bar app: no Dock icon
        "LSApplicationCategoryType": "public.app-category.utilities",
        "NSHighResolutionCapable": True,
        "NSAppleEventsUsageDescription": "hubdeck controls apps for your keys and knobs, e.g. Music's volume.",
        "NSHumanReadableCopyright": "MIT licence - github.com/bell0x07/hubdeck",
    }
    with open(app / "Contents/Info.plist", "wb") as f:
        plistlib.dump(info, f)
    (app / "Contents/PkgInfo").write_text("APPL????")


def sign(app: Path):
    rcodesign = shutil.which("rcodesign")
    pem = os.environ.get("MACOS_SIGN_PEM")
    p12, password = os.environ.get("MACOS_SIGN_P12"), os.environ.get("MACOS_SIGN_P12_PASSWORD", "")
    notary_key = os.environ.get("MACOS_NOTARY_API_KEY")
    certificate = pem or p12
    if not rcodesign:
        if certificate or notary_key or not shutil.which("codesign"):
            sys.exit("rcodesign is needed to sign (https://github.com/indygreg/apple-platform-rs)")
        log("signing ad-hoc with codesign")
        run(["codesign", "--force", "--deep", "--sign", "-", app])
        return
    cmd = [rcodesign, "sign"]
    pw = None
    if pem:
        log("signing with the certificate in MACOS_SIGN_PEM")
        cmd += ["--pem-file", pem]
    elif p12:
        log("signing with the certificate in MACOS_SIGN_P12")
        pw = Path(tempfile.mkstemp()[1])
        pw.write_text(password)
        cmd += ["--p12-file", p12, "--p12-password-file", pw]
    if certificate and notary_key:      # notarization needs the hardened runtime and a timestamp
        cmd += ["--for-notarization", "--code-signature-flags", "runtime",
                "--entitlements-xml-file", HERE / "entitlements.plist"]
    if not certificate:
        log("signing ad-hoc (set MACOS_SIGN_PEM for permissions that survive updates)")
    try:
        run(cmd + [app])
    finally:
        if pw:
            pw.unlink(missing_ok=True)
    if notary_key:
        if not certificate:
            sys.exit("notarizing needs a Developer ID certificate (MACOS_SIGN_PEM or MACOS_SIGN_P12)")
        log("notarizing (this waits for Apple)")
        run([rcodesign, "notary-submit", "--api-key-file", notary_key, "--staple", app])


def zip_app(app: Path, dest: Path):
    """Zip keeping permissions and symlinks, as Finder's Archive Utility expects."""
    log(f"writing {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted([app, *app.rglob("*")]):
            arc = str(path.relative_to(app.parent)) + ("/" if path.is_dir() and not path.is_symlink() else "")
            info = zipfile.ZipInfo(arc)
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arch", choices=sorted(PBS), default="arm64")
    ap.add_argument("--out", type=Path, default=ROOT / "dist")
    ap.add_argument("--cache", type=Path, default=ROOT / "build" / "macos-cache", help="downloads are kept here")
    ap.add_argument("--build-number", default=os.environ.get("CI_PIPELINE_IID", "1"))
    ap.add_argument("--no-zip", action="store_true", help="leave hubdeck.app in the output folder instead")
    args = ap.parse_args()

    version = project()["version"]
    work = ROOT / "build" / f"macos-{args.arch}"
    if work.exists():
        shutil.rmtree(work)
    app = work / "hubdeck.app"
    (app / "Contents/MacOS").mkdir(parents=True)
    with tempfile.TemporaryDirectory() as tmp:
        install_python(app, args.arch, args.cache)
        install_packages(app, args.arch, Path(tmp))
    compile_bytecode(app)
    build_launcher(app, args.arch)
    write_icon(app)
    write_info_plist(app, version, args.build_number)
    for p in app.rglob("*"):                           # no write bits for others; nothing world-writable
        if not p.is_symlink():
            p.chmod(p.stat().st_mode & ~(stat.S_IWGRP | stat.S_IWOTH))
    sign(app)
    if args.no_zip:
        args.out.mkdir(parents=True, exist_ok=True)
        final = args.out / "hubdeck.app"
        if final.exists():
            shutil.rmtree(final)
        shutil.copytree(app, final, symlinks=True)
        log(f"done: {final}")
    else:
        zip_app(app, args.out / f"hubdeck-{version}-macos-{args.arch}.zip")
        log("done")


if __name__ == "__main__":
    main()
