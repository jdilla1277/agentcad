#!/usr/bin/env python3
"""Prepare the stable skill from verified released source, without publishing it."""
import argparse
from email.parser import BytesParser
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen
import zipfile

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

API = "https://api.github.com/repos/jdilla1277/agentcad"
PYPI = "https://pypi.org/pypi/agentcad"
SKILL_SOURCES = ("guide.py", "commands/skill.py")


class Client:
    def bytes(self, url):
        headers = {"User-Agent": "agentcad-release-verifier"}
        if urlparse(url).netloc == "api.github.com":
            headers["Accept"] = "application/vnd.github+json"
            token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
            if token:
                headers["Authorization"] = f"Bearer {token}"
        with urlopen(Request(url, headers=headers), timeout=30) as response:
            data = response.read(32 * 1024 * 1024 + 1)
        if len(data) > 32 * 1024 * 1024:
            raise ValueError("Release verification response exceeds 32 MiB")
        return data

    def json(self, url):
        return json.loads(self.bytes(url))


def _commit_for_tag(tag, client):
    obj = client.json(f"{API}/git/ref/tags/{quote(tag, safe='')}")["object"]
    for _ in range(5):
        sha = obj["sha"]
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Release tag has an invalid source revision")
        if obj["type"] == "commit":
            return sha
        if obj["type"] != "tag":
            break
        obj = client.json(f"{API}/git/tags/{sha}")["object"]
    raise ValueError("Release tag does not resolve to a commit")


def resolve_release(tag, client=None):
    """Require a stable release, successful upload job and current PyPI wheel."""
    client = client or Client()
    match = re.fullmatch(r"v(\d+\.\d+\.\d+)", tag)
    if not match:
        raise ValueError("Choose a stable release tag such as v0.7.2")
    version = match[1]
    release = client.json(f"{API}/releases/tags/{quote(tag, safe='')}")
    if release.get("draft") or release.get("prerelease") or release.get("tag_name") != tag:
        raise ValueError("The requested tag is not a published stable GitHub release")
    sha = _commit_for_tag(tag, client)
    query = urlencode({"event": "release", "head_sha": sha, "per_page": 100})
    runs = client.json(f"{API}/actions/workflows/publish.yml/runs?{query}")["workflow_runs"]
    uploaded = False
    for run in runs:
        if run.get("head_sha") != sha or run.get("event") != "release":
            continue
        jobs = client.json(f"{API}/actions/runs/{int(run['id'])}/jobs?filter=all&per_page=100")["jobs"]
        # This sync can still be running, or a prior sync can have failed.
        # Check the package job rather than requiring overall workflow success.
        if any(job.get("name") in {"publish", "Publish package to PyPI"}
               and job.get("conclusion") == "success" for job in jobs):
            uploaded = True
            break
    if not uploaded:
        raise ValueError("No successful package publication exists for this release revision")
    latest = client.json(f"{PYPI}/json")["info"]["version"]
    if latest != version:
        raise ValueError(f"Stable PyPI version is {latest}, not {version}; refusing to advance or downgrade the mirror")
    package = client.json(f"{PYPI}/{version}/json")
    if package["info"]["version"] != version:
        raise ValueError("PyPI returned a different package version")
    filename = f"agentcad-{version}-py3-none-any.whl"
    wheels = [item for item in package["urls"] if item.get("filename") == filename
              and item.get("packagetype") == "bdist_wheel" and not item.get("yanked")]
    if len(wheels) != 1:
        raise ValueError("The released universal wheel is missing or yanked")
    wheel = wheels[0]
    url = urlparse(wheel["url"])
    digest = wheel["digests"]["sha256"]
    if url.scheme != "https" or url.hostname != "files.pythonhosted.org" or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("The PyPI wheel URL or checksum is invalid")
    return {"version": version, "tag": tag, "source_revision": sha,
            "wheel": {"filename": filename, "sha256": digest, "url": wheel["url"]}}


def verify_current(manifest, client=None):
    """Recheck immediately before the external mirror mutation."""
    if resolve_release(manifest["tag"], client) != manifest:
        raise ValueError("Release source or artifact changed since preparation")


def guard_mirror(manifest, mirror):
    """An old retry cannot replace a newer recorded stable publication."""
    provenance = Path(mirror) / "release.json"
    if not provenance.exists():
        return  # Bootstrap the older mirror, which has no provenance record.
    previous = json.loads(provenance.read_text())
    version = previous.get("version", "")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Existing mirror provenance has an invalid version")
    if tuple(map(int, version.split("."))) > tuple(map(int, manifest["version"].split("."))):
        raise ValueError("Refusing to replace a newer stable mirror")
    if version == manifest["version"] and previous.get("source_revision") != manifest["source_revision"]:
        raise ValueError("This mirror version already records a different source revision")


def prepare(source, manifest, output, client=None):
    client = client or Client()
    verify_current(manifest, client)
    source, output = Path(source), Path(output)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    if revision != manifest["source_revision"]:
        raise ValueError("Checked-out source does not match the released revision")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=source, text=True).strip():
        raise ValueError("Released source checkout contains local changes")
    version = tomllib.loads((source / "pyproject.toml").read_text())["project"]["version"]
    if version != manifest["version"]:
        raise ValueError("Released source version does not match PyPI")
    wheel = client.bytes(manifest["wheel"]["url"])
    if hashlib.sha256(wheel).hexdigest() != manifest["wheel"]["sha256"]:
        raise ValueError("Downloaded wheel does not match the PyPI checksum")
    with zipfile.ZipFile(BytesIO(wheel)) as archive:
        metadata = BytesParser().parsebytes(archive.read(f"agentcad-{version}.dist-info/METADATA"))
        if metadata["Name"] != "agentcad" or metadata["Version"] != version:
            raise ValueError("Downloaded wheel metadata does not match the release")
        for name in SKILL_SOURCES:
            if archive.read(f"agentcad/{name}") != (source / "src/agentcad" / name).read_bytes():
                raise ValueError(f"Released skill source {name} differs from the published wheel")
    env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
    generated = subprocess.check_output([sys.executable, str(source.resolve() / "scripts/generate_skill_md.py")], cwd=source, env=env)
    provenance = {key: manifest[key] for key in ("version", "tag", "source_revision")}
    provenance["package_wheel"] = {key: manifest["wheel"][key] for key in ("filename", "sha256")}
    output.mkdir(parents=True, exist_ok=True)
    (output / "SKILL.md").write_bytes(generated)
    (output / "release.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    resolve = sub.add_parser("resolve")
    resolve.add_argument("--tag", required=True)
    resolve.add_argument("--manifest", required=True, type=Path)
    build = sub.add_parser("prepare")
    build.add_argument("--source", required=True, type=Path)
    build.add_argument("--manifest", required=True, type=Path)
    build.add_argument("--output", required=True, type=Path)
    verify = sub.add_parser("verify")
    verify.add_argument("--manifest", required=True, type=Path)
    verify.add_argument("--mirror", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "resolve":
            manifest = resolve_release(args.tag)
            args.manifest.write_text(json.dumps(manifest, indent=2) + "\n")
            if os.environ.get("GITHUB_OUTPUT"):
                with open(os.environ["GITHUB_OUTPUT"], "a") as output:
                    output.write(f"source_revision={manifest['source_revision']}\n")
        else:
            manifest = json.loads(args.manifest.read_text())
            if args.command == "prepare":
                prepare(args.source, manifest, args.output)
            else:
                verify_current(manifest)
                if args.mirror:
                    guard_mirror(manifest, args.mirror)
    except Exception as exc:
        print(f"Skill publication refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
