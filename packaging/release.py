"""Publish a tagged build: GitLab release (+ package registry) and GitHub release.

Run by the `release` CI job for tags vX.Y.Z, after the build jobs left their zips in dist/.

  GitLab   the zips go to the generic package registry, and the release links to them
           (CI_JOB_TOKEN: nothing to set up).
  GitHub   with GITHUB_TOKEN (a token for the repository with Contents: read and write),
           a release for the same commit with the zips attached. GITHUB_REPOSITORY picks
           the repository (default bell0x07/hubdeck).

Running it again for the same tag replaces the files instead of failing.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "hubdeck"


def request(method: str, url: str, headers: dict, body=None, ok=(200, 201, 204)):
    data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    if isinstance(body, (dict, list)):
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            text = r.read()
            return r.status, json.loads(text) if text else None
    except urllib.error.HTTPError as e:
        if e.code in ok:
            return e.code, None
        detail = e.read().decode(errors="replace")[:500]
        return e.code, {"error": detail}


def notes(version: str) -> str:
    message = os.environ.get("CI_COMMIT_TAG_MESSAGE", "").strip()
    return message or f"hubdeck {version}"


def gitlab(tag: str, version: str, files: list[Path]):
    api, project = os.environ["CI_API_V4_URL"], os.environ["CI_PROJECT_ID"]
    auth = {"JOB-TOKEN": os.environ["CI_JOB_TOKEN"]}
    links = []
    for f in files:
        url = f"{api}/projects/{project}/packages/generic/{PACKAGE}/{version}/{f.name}"
        status, reply = request("PUT", url, auth, f.read_bytes())
        if status not in (200, 201):
            sys.exit(f"GitLab: uploading {f.name} failed ({status}): {reply}")
        links.append({"name": f.name, "url": url, "link_type": "package",
                      "direct_asset_path": f"/{f.name}"})
        print(f"GitLab: uploaded {f.name}")
    release = {"tag_name": tag, "name": f"hubdeck {version}", "description": notes(version),
               "assets": {"links": links}}
    status, reply = request("POST", f"{api}/projects/{project}/releases", auth, release)
    if status == 409:                                  # already released: replace it
        quoted = urllib.parse.quote(tag, safe="")
        request("DELETE", f"{api}/projects/{project}/releases/{quoted}", auth)
        status, reply = request("POST", f"{api}/projects/{project}/releases", auth, release)
    if status not in (200, 201):
        sys.exit(f"GitLab: creating the release failed ({status}): {reply}")
    print(f"GitLab: released {tag}")


def github(tag: str, version: str, files: list[Path]):
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GitHub: skipped (no GITHUB_TOKEN)")
        return
    repo = os.environ.get("GITHUB_REPOSITORY", "bell0x07/hubdeck")
    api = f"https://api.github.com/repos/{repo}"
    auth = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "hubdeck-release"}
    status, rel = request("GET", f"{api}/releases/tags/{urllib.parse.quote(tag)}", auth)
    if status == 404:
        # the mirror may not have pushed the tag yet: GitHub creates it at this exact commit
        status, rel = request("POST", f"{api}/releases", auth, {
            "tag_name": tag, "target_commitish": os.environ["CI_COMMIT_SHA"],
            "name": f"hubdeck {version}", "body": notes(version)})
    if status not in (200, 201):
        sys.exit(f"GitHub: getting/creating the release failed ({status}): {rel}")
    for asset in rel.get("assets", []):
        if asset["name"] in {f.name for f in files}:
            request("DELETE", f"{api}/releases/assets/{asset['id']}", auth)
    upload = rel["upload_url"].split("{", 1)[0]
    for f in files:
        status, reply = request("POST", f"{upload}?name={urllib.parse.quote(f.name)}",
                                {**auth, "Content-Type": "application/zip"}, f.read_bytes())
        if status not in (200, 201):
            sys.exit(f"GitHub: uploading {f.name} failed ({status}): {reply}")
        print(f"GitHub: uploaded {f.name}")
    print(f"GitHub: released {tag} ({rel.get('html_url')})")


def main():
    tag = os.environ.get("CI_COMMIT_TAG")
    if not tag:
        sys.exit("release.py runs for tags only (CI_COMMIT_TAG)")
    version = tag.removeprefix("v")
    files = sorted((ROOT / "dist").glob(f"hubdeck-{version}-macos-*.zip"))   # never a dev build ({version}-<branch>...)
    if not files:
        sys.exit(f"nothing to release: no dist/hubdeck-{version}-macos-*.zip")
    gitlab(tag, version, files)
    github(tag, version, files)


if __name__ == "__main__":
    main()
