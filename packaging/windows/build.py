"""Build hubdeck for Windows on Linux or Windows - no Visual Studio or WiX needed.

    python packaging/windows/build.py [--out dist] [--no-msi]

Produces, in dist/:
  hubdeck-<version>-windows-x64.msi   the installer: per user, no admin rights (see hubdeck.wxs)
  hubdeck-<version>-windows-x64.zip   the same app as a folder to unzip anywhere

The app is a standalone Python (python-build-standalone), hubdeck and its dependencies (Windows
wheels), and two small launchers (launcher.c, cross-compiled with zig): hubdeck.exe for the
command line and hubdeck-tray.exe for the tray app.

Needs: Python 3.13 with pip, Pillow and packaging; zig (`pip install ziglang`); for the MSI,
wixl 0.106 or newer (msitools; Debian trixie: `apt install wixl`). There's no wixl for Windows:
build there with --no-msi, or use the Dockerfile.

Version: as for macOS, see packaging/macos/build.py. The MSI itself is versioned X.Y.Z, so a
development build replaces an installed release of the same version and vice versa.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from common import (PBS_URL, PYTHON, ROOT, build_version, commit_time, compile_bytecode, draw_icon,  # noqa: E402
                    fetch, install_packages, log, remove, requirements, run, zig, zip_folder)

PBS = ("cpython-3.13.16+20261009-x86_64-pc-windows-msvc-install_only_stripped.tar.gz",
       "3a7adfb69e3ff02a41ed38ac637808532b438ab3c8619af46ca5dbec5c1f5190")
PYTHON_DLL = f"python{PYTHON.replace('.', '')}.dll"
MARKERS = {"sys_platform": "win32", "platform_system": "Windows", "os_name": "nt", "platform_machine": "AMD64"}
LAUNCHERS = {   # file: (description, e.g. in Task Manager; extra zig flags)
    "hubdeck.exe": ("hubdeck command line", []),
    "hubdeck-tray.exe": ("hubdeck", ["-DTRAY", "-Wl,--subsystem,windows"]),
}
WIXL_MIN = (0, 106)                 # 0.101 (Debian bookworm) can't put hubdeck on the PATH
WIX_NS = "http://schemas.microsoft.com/wix/2006/wi"
VERSIONED = {".exe", ".dll", ".pyd"}     # files that can carry a version resource

# parts of the standalone Python hubdeck never uses
PRUNE = ["Scripts", "include", "libs", "tcl", "pythonw.exe", "DLLs/_tkinter*", "DLLs/tcl*", "DLLs/tk*",
         "Lib/test", "Lib/idlelib", "Lib/tkinter", "Lib/turtledemo", "Lib/ensurepip", "Lib/site-packages/*"]

# from the installed packages: scripts, test suites, and pystray's Linux backend (pip judges the
# dependencies' own markers by the machine it runs on)
APP_PRUNE = ["bin", "Scripts", "**/tests", "**/test", "Xlib", "python_xlib-*.dist-info"]

# python\python313._pth: sys.path relative to python\ (the standard library, then hubdeck and
# its packages). Its presence also makes Python ignore PYTHON* environment variables.
PTH = ["DLLs", "Lib", "..\\app", "import site"]


def msi_version(version: str) -> str:
    """X.Y.Z from a build version; Windows Installer allows at most 255.255.65535."""
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", version)
    if not m or int(m[1]) > 255 or int(m[2]) > 255 or int(m[3]) > 65535:
        sys.exit(f"version {version} doesn't fit an MSI (at most 255.255.65535)")
    return ".".join(m.groups())


def install_python(folder: Path, cache: Path):
    archive = fetch(PBS_URL + PBS[0], PBS[1], cache)
    log("unpacking Python")
    with tarfile.open(archive) as tar:
        tar.extractall(folder, filter="tar")        # -> python\
    remove(folder / "python", PRUNE)
    (folder / "python" / (PYTHON_DLL.removesuffix(".dll") + "._pth")).write_text("\n".join(PTH) + "\n")


def write_icon(work: Path) -> Path:
    log("drawing the icon")
    ico = work / "hubdeck.ico"
    draw_icon(256, 240, 44).save(ico, format="ICO", sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
    return ico


def resource_script(name: str, description: str, version: str, build: int) -> str:
    """A launcher's resources: the icon, the manifest and the version shown in Properties > Details."""
    numbers = ",".join([*msi_version(version).split("."), str(min(build, 65535))])
    strings = {"CompanyName": "bell0x07", "FileDescription": description, "FileVersion": version,
               "InternalName": name.removesuffix(".exe"), "LegalCopyright": "MIT licence - github.com/bell0x07/hubdeck",
               "OriginalFilename": name, "ProductName": "hubdeck", "ProductVersion": version}
    values = "\n".join(f'      VALUE "{k}", "{v}"' for k, v in strings.items())
    return f"""#include <windows.h>
1 ICON "hubdeck.ico"
1 24 "hubdeck.manifest"
1 VERSIONINFO
FILEVERSION {numbers}
PRODUCTVERSION {numbers}
FILEOS VOS_NT_WINDOWS32
FILETYPE VFT_APP
BEGIN
  BLOCK "StringFileInfo"
  BEGIN
    BLOCK "040904B0"
    BEGIN
{values}
    END
  END
  BLOCK "VarFileInfo"
  BEGIN
    VALUE "Translation", 0x409, 1200
  END
END
"""


def build_launchers(folder: Path, work: Path, version: str, build: int):
    log("compiling the launchers")
    shutil.copy(HERE / "hubdeck.manifest", work)            # next to the .rc, like hubdeck.ico
    for name, (description, flags) in LAUNCHERS.items():
        rc = work / f"{name}.rc"
        rc.write_text(resource_script(name, description, version, build))
        run(zig() + ["cc", "-target", "x86_64-windows-gnu", "-O2", "-s", "-Wall", "-Werror", *flags,
                     f'-DPYTHON_DLL=L"{PYTHON_DLL}"', "-o", folder / name, HERE / "launcher.c", rc, "-lshell32"],
            cwd=work)
    remove(folder, ["*.pdb", "*.lib"])


def ident(kind: str, rel: str) -> str:
    """A stable Windows Installer id for a path inside the app (ids are at most 72 characters)."""
    return f"{kind}_{hashlib.sha1(rel.encode()).hexdigest()[:24]}"


def files_wxs(folder: Path) -> str:
    """The AppFiles component group for hubdeck.wxs: every file under `folder`, one component
    per directory.

    A component's key path is never an .exe, .dll or .pyd. Windows Installer skips a component
    whose versioned key file is already installed in a higher version, and wixl leaves the
    versions out of the MSI, so every installed one counts as higher: an upgrade would skip
    e.g. hubdeck.exe, then delete it along with the old version. Folders holding only such
    files get a registry value as key path instead."""
    ET.register_namespace("", WIX_NS)

    def tag(name):
        return f"{{{WIX_NS}}}{name}"

    root = ET.Element(tag("Wix"))
    fragment = ET.SubElement(root, tag("Fragment"))
    group = ET.Element(tag("ComponentGroup"), Id="AppFiles")

    def add(parent: ET.Element, path: Path, rel: str):
        entries = sorted(path.iterdir(), key=lambda p: p.name)
        files = [p for p in entries if p.is_file()]
        if files:
            cid = ident("c", rel)
            component = ET.SubElement(parent, tag("Component"), Id=cid, Guid="*")
            key = next((f for f in files if f.suffix.lower() not in VERSIONED), None)
            for f in files:
                attrs = {"Id": ident("f", f"{rel}/{f.name}"), "Name": f.name, "Source": str(f)}
                if f is key:
                    attrs["KeyPath"] = "yes"
                ET.SubElement(component, tag("File"), attrs)
            if key is None:
                ET.SubElement(component, tag("RegistryValue"), Root="HKCU", Key=r"Software\hubdeck\Installer",
                              Name=cid, Type="integer", Value="1", KeyPath="yes")
            ET.SubElement(component, tag("RemoveFolder"), Id=ident("r", rel), On="uninstall")
            ET.SubElement(group, tag("ComponentRef"), Id=cid)
        for d in (p for p in entries if p.is_dir()):
            sub = f"{rel}/{d.name}"
            add(ET.SubElement(parent, tag("Directory"), Id=ident("d", sub), Name=d.name), d, sub)

    add(ET.SubElement(fragment, tag("DirectoryRef"), Id="INSTALLDIR"), folder, "")
    fragment.append(group)
    ET.indent(root)
    return ET.tostring(root, encoding="unicode", xml_declaration=True) + "\n"


TRAY_FILE = ident("f", "/hubdeck-tray.exe")   # its File id in files.wxs, for the StartHubdeck action


def find_wixl() -> str:
    wixl = shutil.which("wixl")
    if not wixl:
        sys.exit("the MSI needs wixl (msitools): build with the Dockerfile, or pass --no-msi for the zip only")
    out = subprocess.run([wixl, "--version"], capture_output=True, text=True).stdout
    m = re.search(r"(\d+)\.(\d+)", out)
    if not m or (int(m[1]), int(m[2])) < WIXL_MIN:
        sys.exit(f"the MSI needs wixl {'.'.join(map(str, WIXL_MIN))} or newer (Debian trixie), found {out.strip()}")
    return wixl


def build_msi(wixl: str, folder: Path, work: Path, ico: Path, version: str, dest: Path):
    log(f"writing {dest}")
    files = work / "files.wxs"
    files.write_text(files_wxs(folder), encoding="utf-8")
    dest.parent.mkdir(parents=True, exist_ok=True)
    run([wixl, "-a", "x64", "-D", f"Version={msi_version(version)}", "-D", f"BuildVersion={version}",
         "-D", f"Icon={ico}", "-D", f"TrayFile={TRAY_FILE}", "-o", dest, HERE / "hubdeck.wxs", files])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "dist")
    ap.add_argument("--cache", type=Path, default=ROOT / "build" / "windows-cache", help="downloads are kept here")
    ap.add_argument("--build-number", type=int, default=int(os.environ.get("CI_PIPELINE_IID", "0")))
    ap.add_argument("--no-msi", action="store_true", help="only the zip (there's no wixl for Windows)")
    args = ap.parse_args()

    wixl = None if args.no_msi else find_wixl()
    version = build_version()
    log(f"building hubdeck {version} for Windows x64")
    work = ROOT / "build" / "windows-x64"
    if work.exists():
        shutil.rmtree(work)
    folder = work / "hubdeck"
    folder.mkdir(parents=True)
    with tempfile.TemporaryDirectory() as tmp:
        install_python(folder, args.cache)
        install_packages(folder / "app", ["win_amd64"], requirements(MARKERS), Path(tmp), APP_PRUNE)
    compile_bytecode(folder)
    ico = write_icon(work)
    build_launchers(folder, work, version, args.build_number)
    name = f"hubdeck-{version}-windows-x64"
    zip_folder(folder, args.out / f"{name}.zip", commit_time())
    if wixl:
        build_msi(wixl, folder, work, ico, version, args.out / f"{name}.msi")
    log("done")


if __name__ == "__main__":
    main()
