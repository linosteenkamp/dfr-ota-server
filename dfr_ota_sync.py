#!/usr/bin/env python3
"""dfr-ota-server: aggregate per-repo Zigbee OTA releases into one LAN-served z2m index."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


class VerifyError(Exception):
    pass


def verify_blob(blob: bytes, expected_sha512: str, expected_size: int) -> None:
    if len(blob) != expected_size:
        raise VerifyError(f"size {len(blob)} != expected {expected_size}")
    got = hashlib.sha512(blob).hexdigest()
    if got.lower() != expected_sha512.lower():
        raise VerifyError("sha512 mismatch")


def parse_repos_conf(text: str) -> list[str]:
    """Parse repos.conf into an ordered, de-duplicated list of 'owner/repo'."""
    repos: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if " " in s or s.count("/") != 1 or s.startswith("/") or s.endswith("/"):
            raise ValueError(f"malformed repo line: {line!r}")
        if s not in repos:
            repos.append(s)
    return repos


def _served_name(entry: dict) -> str:
    """Local serve filename, namespaced by (manufacturerCode, imageType) so assets
    from different repos that share a name (e.g. firmware-1.0.0.ota) don't collide."""
    orig = entry["url"].rstrip("/").rsplit("/", 1)[-1]
    return f'{entry["manufacturerCode"]:04x}-{entry["imageType"]:04x}-{orig}'


def rewrite_entry_url(entry: dict, base_url: str) -> dict:
    """Return a copy of entry with url repointed to the LAN server (namespaced name)."""
    return {**entry, "url": f"{base_url.rstrip('/')}/ota/{_served_name(entry)}"}


def _key(entry: dict) -> tuple:
    return (entry["manufacturerCode"], entry["imageType"])


def merge_indexes(previous: list[dict], updates: list[dict]) -> list[dict]:
    """Merge by (manufacturerCode, imageType); updates overwrite, others retained."""
    merged = {_key(e): e for e in previous}
    for e in updates:
        merged[_key(e)] = e
    return list(merged.values())


class SkipRepo(Exception):
    pass


def _load_index(path: Path) -> list[dict]:
    if path.is_file() and path.read_text().strip():
        return json.loads(path.read_text())
    return []


def _atomic_replace_dir(new_dir: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = target.with_name(target.name + ".old")
    if backup.exists():
        shutil.rmtree(backup)
    # new_dir may live inside target (e.g. staging_root nested under serve_dir);
    # renaming target -> backup silently moves new_dir along with it, so track
    # its new location before doing the final swap.
    target_r = target.resolve()
    nested = target.exists() and target_r in new_dir.resolve().parents
    if target.exists():
        os.rename(target, backup)
        if nested:
            new_dir = backup / new_dir.resolve().relative_to(target_r)
    os.rename(new_dir, target)
    if backup.exists():
        shutil.rmtree(backup)


def _publish(serve_dir: Path, staging_root: Path, merged: list[dict],
             new_blobs: dict) -> None:
    staging_root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(dir=staging_root))
    ota = stage / "ota"
    ota.mkdir()
    src_ota = serve_dir / "ota"
    if src_ota.is_dir():                       # carry over last-good files
        for f in src_ota.iterdir():
            if f.is_file():
                shutil.copy2(f, ota / f.name)
    for fname, blob in new_blobs.items():      # overlay fresh images
        (ota / fname).write_bytes(blob)
    referenced = {e["url"].rstrip("/").rsplit("/", 1)[-1] for e in merged}
    for f in ota.iterdir():                    # prune orphans
        if f.is_file() and f.name not in referenced:
            f.unlink()
    (stage / "index.json").write_text(json.dumps(merged, indent=2) + "\n")
    _atomic_replace_dir(stage, serve_dir)


def sync_once(fetcher, repos, base_url, serve_dir, staging_root, log=print) -> dict:
    serve_dir = Path(serve_dir)
    staging_root = Path(staging_root)
    previous = _load_index(serve_dir / "index.json")
    updates: list[dict] = []
    new_blobs: dict = {}
    failed: list[str] = []

    for repo in repos:
        try:
            entries = fetcher.get_index(repo)
        except SkipRepo:
            log(f"{repo}: no release/index — skipping")
            continue
        except Exception as ex:                # noqa: BLE001 - report & continue
            log(f"{repo}: index fetch failed: {ex}")
            failed.append(repo)
            continue

        repo_updates: list[dict] = []
        repo_blobs: dict = {}
        ok = True
        for entry in entries:
            asset_name = entry["url"].rstrip("/").rsplit("/", 1)[-1]
            try:
                blob = fetcher.download(repo, asset_name)
                verify_blob(blob, entry["sha512"], entry["fileSize"])
            except Exception as ex:            # noqa: BLE001
                log(f"{repo}: {asset_name} failed: {ex}")
                ok = False
                break
            repo_blobs[_served_name(entry)] = blob
            repo_updates.append(rewrite_entry_url(entry, base_url))
        if ok:
            updates.extend(repo_updates)
            new_blobs.update(repo_blobs)
            log(f"{repo}: {len(repo_updates)} image(s) OK")
        else:
            failed.append(repo)

    merged = merge_indexes(previous, updates)
    _publish(serve_dir, staging_root, merged, new_blobs)
    return {"ok": len(merged), "failed": failed}


GITHUB_API = "https://api.github.com"


class _NoAuthRedirect(urllib.request.HTTPRedirectHandler):
    """Strip Authorization on redirect (GitHub asset -> S3 rejects the header)."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new.headers.pop("Authorization", None)
            new.unredirected_hdrs.pop("Authorization", None)
        return new


class GitHubFetcher:
    def __init__(self, token: str):
        self._token = token
        self._opener = urllib.request.build_opener(_NoAuthRedirect())

    def _get(self, path: str, accept: str = "application/vnd.github+json") -> bytes:
        req = urllib.request.Request(
            GITHUB_API + path,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": accept,
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "dfr-ota-sync",
            },
        )
        with self._opener.open(req, timeout=30) as resp:
            return resp.read()

    def get_index(self, repo: str) -> list[dict]:
        try:
            self._get(f"/repos/{repo}/releases/latest")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise SkipRepo(repo)
            raise
        try:
            raw = self._get(f"/repos/{repo}/contents/ota/index.json",
                            accept="application/vnd.github.raw")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise SkipRepo(repo)
            raise
        return json.loads(raw)

    def download(self, repo: str, filename: str) -> bytes:
        rel = json.loads(self._get(f"/repos/{repo}/releases/latest"))
        asset_id = next((a["id"] for a in rel.get("assets", [])
                         if a["name"] == filename), None)
        if asset_id is None:
            raise FileNotFoundError(f"{repo}: asset {filename} not in latest release")
        return self._get(f"/repos/{repo}/releases/assets/{asset_id}",
                         accept="application/octet-stream")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Aggregate DFR-* OTA releases for z2m.")
    ap.add_argument("--repos", default="/etc/dfr-ota/repos.conf")
    ap.add_argument("--token-file", default="/etc/dfr-ota/token")
    # serve-dir MUST be a subdirectory of a dir the service user owns: the atomic
    # swap renames serve-dir itself, which needs write on its PARENT. Serving
    # /var/lib/dfr-ota/pub (parent /var/lib/dfr-ota is owned by dfr-ota) works;
    # serving /var/lib/dfr-ota directly would need write on /var/lib (root-only).
    ap.add_argument("--serve-dir", default="/var/lib/dfr-ota/pub")
    ap.add_argument("--staging-root", default="/var/lib/dfr-ota/.staging")
    ap.add_argument("--base-url", required=True,
                    help="LAN base, e.g. http://192.168.1.10:8091")
    a = ap.parse_args(argv)

    repos = parse_repos_conf(Path(a.repos).read_text())
    token = Path(a.token_file).read_text().strip()
    res = sync_once(GitHubFetcher(token), repos, a.base_url,
                    a.serve_dir, a.staging_root)
    print(f"served {res['ok']} image(s); failed repos: {res['failed'] or 'none'}")
    return 1 if res["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
