#!/usr/bin/env python3
"""dfr-ota-server: aggregate per-repo Zigbee OTA releases into one LAN-served z2m index."""
from __future__ import annotations

import hashlib


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
