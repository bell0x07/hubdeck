"""Build hubdeck.app for macOS on Linux or macOS - no Xcode needed.

    python packaging/macos/build.py [--arch arm64|x86_64] [--out dist]

Produces dist/hubdeck-<version>-macos-<arch>.zip holding hubdeck.app: a standalone Python
(python-build-standalone), hubdeck and its dependencies (macOS wheels), and a small launcher
(launcher.c, cross-compiled with zig) so the app runs as hubdeck.app itself.

Needs: Python 3.13 with pip, Pillow and packaging; zig (`pip install ziglang`); rcodesign
(https://github.com/indygreg/apple-platform-rs) for signing - or, on a Mac, codesign.

Version: a tag vX.Y.Z builds X.Y.Z (it must match hubdeck.__version__); anything else is a
development build named after its branch, e.g. 0.1.0-feat-macos-app.48 in CI (pipeline number)
or 0.1.0-main-local on your machine.

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
import os
import plistlib
import shutil
import stat
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from common import (PBS_URL, PYTHON, ROOT, build_version, commit_time, compile_bytecode,  # noqa: E402
                    draw_icon, fetch, install_packages, log, remove, requirements, run, zig, zip_folder)
from hubdeck._bundle import BUNDLE_ID  # noqa: E402

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


def install_python(app: Path, arch: str, cache: Path):
    name, sha = PBS[arch]
    archive = fetch(PBS_URL + name, sha, cache)
    log("unpacking Python")
    dest = app / "Contents/Resources"
    with tarfile.open(archive) as tar:
        tar.extractall(dest, filter="tar")          # -> Resources/python
    remove(dest / "python", PRUNE)


def install_packages_for(app: Path, arch: str, tmp: Path):
    markers = {"sys_platform": "darwin", "platform_system": "Darwin", "os_name": "posix", "platform_machine": "arm64"}
    install_packages(app / "Contents/Resources/app", WHEEL_PLATFORMS[arch], requirements(markers), tmp, APP_PRUNE)


def build_launcher(app: Path, arch: str):
    log("compiling the launcher")
    out = app / "Contents/MacOS/hubdeck"
    out.parent.mkdir(parents=True, exist_ok=True)
    run(zig() + ["cc", "-target", ZIG_TARGET[arch], "-O2", "-Wall", "-Werror",
               f'-DPYTHON_LIB="libpython{PYTHON}.dylib"', "-o", out, HERE / "launcher.c"])
    out.chmod(0o755)


def write_icon(app: Path):
    """The tray glyph on a rounded tile, as macOS app icons look."""
    log("drawing the icon")
    img = draw_icon(1024, 824, 185)                # Apple's grid: an 824 px tile in a 1024 canvas
    img.save(app / "Contents/Resources/hubdeck.icns", format="ICNS")


def write_info_plist(app: Path, version: str, build: str):
    short = version.split("-")[0]                  # macOS wants plain X.Y.Z here
    info = {
        "CFBundleName": "hubdeck",
        "CFBundleDisplayName": "hubdeck",
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": "hubdeck",
        "CFBundleIconFile": "hubdeck",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": short,
        "CFBundleVersion": build,
        "HubdeckBuildVersion": version,
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arch", choices=sorted(PBS), default="arm64")
    ap.add_argument("--out", type=Path, default=ROOT / "dist")
    ap.add_argument("--cache", type=Path, default=ROOT / "build" / "macos-cache", help="downloads are kept here")
    ap.add_argument("--build-number", default=os.environ.get("CI_PIPELINE_IID", "1"))
    ap.add_argument("--no-zip", action="store_true", help="leave hubdeck.app in the output folder instead")
    args = ap.parse_args()

    version = build_version()
    log(f"building hubdeck {version} for {args.arch}")
    work = ROOT / "build" / f"macos-{args.arch}"
    if work.exists():
        shutil.rmtree(work)
    app = work / "hubdeck.app"
    (app / "Contents/MacOS").mkdir(parents=True)
    with tempfile.TemporaryDirectory() as tmp:
        install_python(app, args.arch, args.cache)
        install_packages_for(app, args.arch, Path(tmp))
    compile_bytecode(app / "Contents/Resources")
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
        zip_folder(app, args.out / f"hubdeck-{version}-macos-{args.arch}.zip", commit_time())
        log("done")


if __name__ == "__main__":
    main()
