#!/usr/bin/env python3
"""dfr-ota-server: aggregate per-repo Zigbee OTA releases into one LAN-served z2m index."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
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


def rewrite_entry_url(entry: dict, base_url: str) -> dict:
    """Return a copy of entry with url repointed to the LAN server."""
    fname = entry["url"].rstrip("/").rsplit("/", 1)[-1]
    return {**entry, "url": f"{base_url.rstrip('/')}/ota/{fname}"}


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
    if target.exists():
        os.rename(target, backup)
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
            fname = entry["url"].rstrip("/").rsplit("/", 1)[-1]
            try:
                blob = fetcher.download(repo, fname)
                verify_blob(blob, entry["sha512"], entry["fileSize"])
            except Exception as ex:            # noqa: BLE001
                log(f"{repo}: {fname} failed: {ex}")
                ok = False
                break
            repo_blobs[fname] = blob
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
