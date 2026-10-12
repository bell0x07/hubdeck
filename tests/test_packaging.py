"""packaging/: version naming, zip details, the Windows MSI's file list and release file
selection (no network, no build tools)."""

import importlib.util
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

import hubdeck

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


common = _load("common", "packaging/common.py")
macos = _load("macos_build", "packaging/macos/build.py")
windows = _load("windows_build", "packaging/windows/build.py")
release = _load("release", "packaging/release.py")
WIX = "{http://schemas.microsoft.com/wix/2006/wi}"


def test_version_comes_from_the_code():
    assert common.base_version() == hubdeck.__version__


def test_release_tag_builds_the_plain_version():
    assert common.build_version({"CI": "true", "CI_COMMIT_TAG": f"v{hubdeck.__version__}"}) == hubdeck.__version__


def test_tag_must_match_the_code():
    with pytest.raises(SystemExit, match="doesn't match"):
        common.build_version({"CI": "true", "CI_COMMIT_TAG": "v99.0.0"})


def test_branch_builds_are_named_after_the_branch():
    env = {"CI": "true", "CI_COMMIT_REF_NAME": "feat/macos-app", "CI_PIPELINE_IID": "48"}
    assert common.build_version(env) == f"{hubdeck.__version__}-feat-macos-app.48"
    assert common.slug("Feat/Ünicode Branch!") == "feat-nicode-branch"


def test_local_builds_work_without_git(monkeypatch):
    """The Docker builders may have no git: the build still gets a name and a date."""
    def no_git(*a, **kw):
        raise FileNotFoundError("git")
    monkeypatch.setattr(common.subprocess, "run", no_git)
    assert common.build_version({}) == f"{hubdeck.__version__}-unknown-local"
    assert abs(time.mktime(common.commit_time({})) - time.time()) < 60


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need admin or Developer Mode")
def test_zip_is_dated_and_keeps_modes_and_symlinks(tmp_path):
    app = tmp_path / "hubdeck.app"
    exe = app / "Contents/MacOS/hubdeck"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"\xcf\xfa\xed\xfe")
    exe.chmod(0o755)
    (app / "Contents/link").symlink_to("MacOS/hubdeck")
    when = time.strptime("2026-10-11 18:57:12", "%Y-%m-%d %H:%M:%S")
    out = tmp_path / "out.zip"
    common.zip_folder(app, out, when)
    with zipfile.ZipFile(out) as z:
        infos = {i.filename: i for i in z.infolist()}
        assert {i.date_time for i in infos.values()} == {(2026, 10, 11, 18, 57, 12)}
        assert (infos["hubdeck.app/Contents/MacOS/hubdeck"].external_attr >> 16) & 0o777 == 0o755
        link = infos["hubdeck.app/Contents/link"]
        assert (link.external_attr >> 16) & 0o170000 == 0o120000          # a symlink, not a copy
        assert z.read(link) == b"MacOS/hubdeck"


def test_zip_uses_forward_slashes(tmp_path):
    (tmp_path / "hubdeck/python").mkdir(parents=True)
    (tmp_path / "hubdeck/python/python313._pth").write_text("Lib\n")
    out = tmp_path / "out.zip"
    common.zip_folder(tmp_path / "hubdeck", out, time.localtime())
    with zipfile.ZipFile(out) as z:
        assert z.namelist() == ["hubdeck/", "hubdeck/python/", "hubdeck/python/python313._pth"]


def test_commit_time_from_ci():
    t = common.commit_time({"CI_COMMIT_TIMESTAMP": "2026-10-11T16:57:12+00:00"})
    assert time.mktime(t) == pytest.approx(time.mktime(time.strptime("2026-10-11 16:57:12 +0000", "%Y-%m-%d %H:%M:%S %z")), abs=86400)
    assert t.tm_year == 2026


def test_dependencies_follow_the_target_system():
    names = {r.split(">")[0].split("=")[0] for r in common.requirements(windows.MARKERS)}
    assert {"pycaw", "comtypes", "hidapi", "winrt-Windows.Media.Control"} <= names
    assert not any(n.startswith("pyobjc") for n in names)
    mac = common.requirements({"sys_platform": "darwin", "platform_system": "Darwin", "os_name": "posix",
                               "platform_machine": "arm64"})
    assert any(r.startswith("pyobjc-framework-Quartz") for r in mac) and not any(r.startswith("pycaw") for r in mac)
    assert all(";" not in r for r in mac)                  # markers dropped: pip would judge them by this machine


def test_msi_version_is_the_plain_version():
    assert windows.msi_version("1.1.0-feat-windows-app.48") == "1.1.0"
    assert windows.msi_version("1.1.0") == "1.1.0"
    with pytest.raises(SystemExit, match="doesn't fit"):
        windows.msi_version("256.0.0")


def test_launcher_resources_carry_the_version():
    rc = windows.resource_script("hubdeck-tray.exe", "hubdeck", "1.1.0-main.48", 48)
    assert "FILEVERSION 1,1,0,48" in rc and "PRODUCTVERSION 1,1,0,48" in rc
    assert 'VALUE "FileVersion", "1.1.0-main.48"' in rc
    assert 'VALUE "OriginalFilename", "hubdeck-tray.exe"' in rc
    assert '1 ICON "hubdeck.ico"' in rc and '1 24 "hubdeck.manifest"' in rc
    assert "FILEVERSION 1,1,0,65535" in windows.resource_script("hubdeck.exe", "x", "1.1.0", 70000)


def _app(tmp_path):
    app = tmp_path / "hubdeck"
    for rel in ["hubdeck.exe", "hubdeck-tray.exe", "python/LICENSE.txt", "python/python313.dll",
                "python/python313._pth", "python/DLLs/_asyncio.pyd", "python/DLLs/libffi-8.dll",
                "app/ruamel/yaml/main.py", "app/six.py", "app/name with spaces & ünicode.txt"]:
        (app / rel).parent.mkdir(parents=True, exist_ok=True)
        (app / rel).write_bytes(b"x")
    return app


def test_msi_lists_every_file_once(tmp_path):
    app = _app(tmp_path)
    text = windows.files_wxs(app)
    assert text == windows.files_wxs(app)                  # stable from build to build
    root = ET.fromstring(text)
    files = root.findall(f".//{WIX}File")
    assert sorted(Path(f.get("Source")) for f in files) == sorted(p for p in app.rglob("*") if p.is_file())
    assert all(f.get("Name") == Path(f.get("Source")).name for f in files)
    ids = [e.get("Id") for e in root.iter() if e.get("Id") and e.tag != f"{WIX}ComponentRef"]
    assert len(ids) == len(set(ids)) and all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,71}", i) for i in ids)
    components = root.findall(f".//{WIX}Component")
    group = root.find(f".//{WIX}ComponentGroup[@Id='AppFiles']")
    assert {c.get("Id") for c in components} == {r.get("Id") for r in group}
    ruamel = root.find(f".//{WIX}Directory[@Name='ruamel']")
    assert ruamel.find(f"{WIX}Component") is None             # no files of its own: just the folder


def test_msi_key_paths_are_never_versioned_files(tmp_path):
    """Windows Installer would skip a component whose versioned key file is already installed,
    then delete it with the old version: hubdeck.exe went missing after an upgrade."""
    root = ET.fromstring(windows.files_wxs(_app(tmp_path)))
    keys = {}
    for c in root.findall(f".//{WIX}Component"):
        key = [e for e in c if e.get("KeyPath") == "yes"]
        assert len(key) == 1
        keys[c.find(f"{WIX}File").get("Source")] = key[0]
    for source, key in keys.items():
        assert key.tag == f"{WIX}RegistryValue" or Path(key.get("Source")).suffix not in windows.VERSIONED
    by_name = {Path(s).parent.name: k for s, k in keys.items()}
    assert by_name["hubdeck"].tag == f"{WIX}RegistryValue"             # hubdeck.exe and hubdeck-tray.exe only
    assert by_name["DLLs"].tag == f"{WIX}RegistryValue"
    assert by_name["python"].get("Name") == "LICENSE.txt"


def test_release_needs_a_tag_and_matching_files(monkeypatch, tmp_path):
    monkeypatch.delenv("CI_COMMIT_TAG", raising=False)
    with pytest.raises(SystemExit, match="tags only"):
        release.main()
    monkeypatch.setenv("CI_COMMIT_TAG", "v9.9.9")
    monkeypatch.setattr(release, "ROOT", tmp_path)
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "hubdeck-9.9.9-feat-x.3-macos-arm64.zip").write_bytes(b"dev build")
    with pytest.raises(SystemExit, match="nothing to release"):
        release.main()                      # a dev build of another version never gets released
    assert os.environ["CI_COMMIT_TAG"] == "v9.9.9"


def test_release_publishes_both_systems_but_no_dev_builds(monkeypatch, tmp_path):
    monkeypatch.setenv("CI_COMMIT_TAG", "v9.9.9")
    monkeypatch.setattr(release, "ROOT", tmp_path)
    dist = tmp_path / "dist"
    dist.mkdir()
    wanted = ["hubdeck-9.9.9-macos-arm64.zip", "hubdeck-9.9.9-windows-x64.msi", "hubdeck-9.9.9-windows-x64.zip"]
    for name in wanted + ["hubdeck-9.9.9-windows-app.3-windows-x64.msi", "hubdeck-9.9.9-macos-app.3-macos-arm64.zip"]:
        (dist / name).write_bytes(b"x")
    published = []
    monkeypatch.setattr(release, "gitlab", lambda tag, version, files: published.append([f.name for f in files]))
    monkeypatch.setattr(release, "github", lambda tag, version, files: published.append([f.name for f in files]))
    release.main()
    assert published == [wanted, wanted]
    assert {Path(n).suffix for n in wanted} <= set(release.CONTENT_TYPES)


def test_msi_starts_the_installed_tray(tmp_path):
    """hubdeck.wxs's StartHubdeck action points at hubdeck-tray.exe by its File id."""
    root = ET.fromstring(windows.files_wxs(_app(tmp_path)))
    tray = next(f for f in root.iter(f"{WIX}File") if f.get("Name") == "hubdeck-tray.exe")
    assert tray.get("Id") == windows.TRAY_FILE
    wxs = (ROOT / "packaging/windows/hubdeck.wxs").read_text()
    ET.fromstring(wxs)                                 # well-formed: e.g. no "--" inside a comment
    assert 'FileKey="$(var.TrayFile)" ExeCommand="--replace"' in wxs
