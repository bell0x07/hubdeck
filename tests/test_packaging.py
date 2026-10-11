"""packaging/: version naming, zip details and release file selection (no network)."""

import importlib.util
import os
import time
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


build = _load("macos_build", "packaging/macos/build.py")
release = _load("release", "packaging/release.py")


def test_version_comes_from_the_code():
    assert build.base_version() == hubdeck.__version__


def test_release_tag_builds_the_plain_version():
    assert build.build_version({"CI": "true", "CI_COMMIT_TAG": f"v{hubdeck.__version__}"}) == hubdeck.__version__


def test_tag_must_match_the_code():
    with pytest.raises(SystemExit, match="doesn't match"):
        build.build_version({"CI": "true", "CI_COMMIT_TAG": "v99.0.0"})


def test_branch_builds_are_named_after_the_branch():
    env = {"CI": "true", "CI_COMMIT_REF_NAME": "feat/macos-app", "CI_PIPELINE_IID": "48"}
    assert build.build_version(env) == f"{hubdeck.__version__}-feat-macos-app.48"
    assert build.slug("Feat/Ünicode Branch!") == "feat-nicode-branch"


def test_zip_is_dated_and_keeps_modes_and_symlinks(tmp_path):
    app = tmp_path / "hubdeck.app"
    exe = app / "Contents/MacOS/hubdeck"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"\xcf\xfa\xed\xfe")
    exe.chmod(0o755)
    (app / "Contents/link").symlink_to("MacOS/hubdeck")
    when = time.strptime("2026-10-11 18:57:12", "%Y-%m-%d %H:%M:%S")
    out = tmp_path / "out.zip"
    build.zip_app(app, out, when)
    with zipfile.ZipFile(out) as z:
        infos = {i.filename: i for i in z.infolist()}
        assert {i.date_time for i in infos.values()} == {(2026, 10, 11, 18, 57, 12)}
        assert (infos["hubdeck.app/Contents/MacOS/hubdeck"].external_attr >> 16) & 0o777 == 0o755
        link = infos["hubdeck.app/Contents/link"]
        assert (link.external_attr >> 16) & 0o170000 == 0o120000          # a symlink, not a copy
        assert z.read(link) == b"MacOS/hubdeck"


def test_commit_time_from_ci():
    t = build.commit_time({"CI_COMMIT_TIMESTAMP": "2026-10-11T16:57:12+00:00"})
    assert time.mktime(t) == pytest.approx(time.mktime(time.strptime("2026-10-11 16:57:12 +0000", "%Y-%m-%d %H:%M:%S %z")), abs=86400)
    assert t.tm_year == 2026


def test_release_needs_a_tag_and_matching_zips(monkeypatch, tmp_path):
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
