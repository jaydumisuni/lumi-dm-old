#!/usr/bin/env python3
"""Publish Builder output directly to a target GitHub Release.

The access token is read from GITHUB_TOKEN or GH_TOKEN and is never written to
project files. The publisher can create or update a release, replace same-name
assets and generate SHA-256 sidecars for Lumi's verified updater.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


API = "https://api.github.com"


def token() -> str:
    value = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if not value:
        raise RuntimeError("Set GITHUB_TOKEN or GH_TOKEN before publishing")
    return value


def infer_repo() -> str:
    try:
        remote = subprocess.check_output(
            ["git", "config", "--get", "remote.origin.url"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""
    match = re.search(r"github\.com[/:]([^/]+/[^/.]+)(?:\.git)?$", remote)
    return match.group(1) if match else ""


def validate_repo(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise ValueError("Repository must use owner/name format")
    return value


def request_json(method: str, url: str, payload: dict[str, Any] | None = None) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, method=method)
    request.add_header("Authorization", f"Bearer {token()}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("User-Agent", "THETECHGUY-Software-Builder/1.0")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=60) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API {exc.code}: {detail[:1000]}") from exc


def upload_file(upload_url: str, path: Path, label: str = "") -> Any:
    url = upload_url.split("{", 1)[0] + f"?name={quote(path.name)}"
    if label:
        url += f"&label={quote(label)}"
    request = Request(url, data=path.read_bytes(), method="POST")
    request.add_header("Authorization", f"Bearer {token()}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("User-Agent", "THETECHGUY-Software-Builder/1.0")
    request.add_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
    try:
        with urlopen(request, timeout=600) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Asset upload {exc.code}: {detail[:1000]}") from exc


def sha256_sidecar(path: Path) -> Path:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    sidecar = path.with_name(f"{path.name}.sha256")
    sidecar.write_text(f"{digest.hexdigest()}  {path.name}\n", encoding="utf-8")
    return sidecar


def release_by_tag(repo: str, tag: str) -> dict[str, Any] | None:
    try:
        return request_json("GET", f"{API}/repos/{repo}/releases/tags/{quote(tag, safe='')}")
    except RuntimeError as exc:
        if "GitHub API 404" in str(exc):
            return None
        raise


def ensure_release(
    repo: str,
    tag: str,
    title: str,
    notes: str,
    *,
    target: str,
    draft: bool,
    prerelease: bool,
) -> dict[str, Any]:
    existing = release_by_tag(repo, tag)
    payload = {
        "tag_name": tag,
        "target_commitish": target,
        "name": title,
        "body": notes,
        "draft": draft,
        "prerelease": prerelease,
        "generate_release_notes": not bool(notes.strip()),
    }
    if existing:
        return request_json("PATCH", f"{API}/repos/{repo}/releases/{existing['id']}", payload)
    return request_json("POST", f"{API}/repos/{repo}/releases", payload)


def delete_existing_assets(repo: str, release: dict[str, Any], names: set[str]) -> None:
    for asset in release.get("assets") or []:
        if str(asset.get("name") or "") in names:
            request_json("DELETE", f"{API}/repos/{repo}/releases/assets/{asset['id']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish THETECHGUY Builder output to GitHub Releases")
    parser.add_argument("--repo", default="", help="Target GitHub repository in owner/name form")
    parser.add_argument("--tag", required=True, help="Release tag, for example v1.2.0")
    parser.add_argument("--title", default="", help="Release title; defaults to the tag")
    parser.add_argument("--notes", default="", help="Release notes text")
    parser.add_argument("--notes-file", type=Path, help="UTF-8 Markdown release-notes file")
    parser.add_argument("--target", default="main", help="Target branch or commit when the tag is created")
    parser.add_argument("--asset", action="append", type=Path, default=[], help="Build asset; may be repeated")
    parser.add_argument("--draft", action="store_true")
    parser.add_argument("--prerelease", action="store_true")
    parser.add_argument("--replace-assets", action="store_true")
    parser.add_argument("--no-checksums", action="store_true", help="Do not create and upload .sha256 sidecars")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo = validate_repo(args.repo or infer_repo())
    notes = args.notes_file.read_text(encoding="utf-8") if args.notes_file else args.notes
    assets = [path.resolve() for path in args.asset]
    missing = [str(path) for path in assets if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing release assets: " + ", ".join(missing))
    generated: list[Path] = []
    if not args.no_checksums:
        generated = [sha256_sidecar(path) for path in assets]
    all_assets = assets + generated

    release = ensure_release(
        repo,
        args.tag,
        args.title or args.tag,
        notes,
        target=args.target,
        draft=args.draft,
        prerelease=args.prerelease,
    )
    if args.replace_assets:
        delete_existing_assets(repo, release, {path.name for path in all_assets})
    uploaded = [upload_file(release["upload_url"], path) for path in all_assets]
    print(json.dumps({
        "repository": repo,
        "tag": args.tag,
        "release_url": release.get("html_url"),
        "release_id": release.get("id"),
        "assets": [item.get("name") for item in uploaded],
        "draft": release.get("draft"),
        "prerelease": release.get("prerelease"),
    }, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Release publish failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
