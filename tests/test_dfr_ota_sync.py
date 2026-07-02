import sys
from pathlib import Path
import pytest
import hashlib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import dfr_ota_sync as s


def test_parse_repos_conf_basic():
    text = "# comment\n\nlinosteenkamp/DFR-DoorSensor\n  owner/repo2  \n"
    assert s.parse_repos_conf(text) == ["linosteenkamp/DFR-DoorSensor", "owner/repo2"]


def test_parse_repos_conf_dedupes_preserving_order():
    text = "a/b\nc/d\na/b\n"
    assert s.parse_repos_conf(text) == ["a/b", "c/d"]


def test_parse_repos_conf_rejects_malformed():
    with pytest.raises(ValueError):
        s.parse_repos_conf("not-a-repo\n")
    with pytest.raises(ValueError):
        s.parse_repos_conf("owner/repo extra\n")


def test_rewrite_entry_url():
    entry = {"modelId": "X", "manufacturerCode": 65278, "imageType": 2,
             "fileVersion": 1, "fileSize": 10, "sha512": "ab",
             "url": "https://github.com/o/r/releases/download/v1.0.1/firmware-1.0.1.ota"}
    out = s.rewrite_entry_url(entry, "http://pi.lan:8091")
    assert out["url"] == "http://pi.lan:8091/ota/firmware-1.0.1.ota"
    assert out["sha512"] == "ab" and out["manufacturerCode"] == 65278
    assert entry["url"].startswith("https://")  # original not mutated


def test_rewrite_entry_url_strips_trailing_slash_on_base():
    entry = {"url": "https://x/y/fw.ota"}
    assert s.rewrite_entry_url(entry, "http://pi.lan:8091/")["url"] == "http://pi.lan:8091/ota/fw.ota"


def test_verify_blob_ok():
    blob = b"hello world"
    s.verify_blob(blob, hashlib.sha512(blob).hexdigest(), len(blob))  # no raise


def test_verify_blob_size_mismatch():
    blob = b"abc"
    with pytest.raises(s.VerifyError):
        s.verify_blob(blob, hashlib.sha512(blob).hexdigest(), 999)


def test_verify_blob_sha_mismatch():
    with pytest.raises(s.VerifyError):
        s.verify_blob(b"abc", "00" * 64, 3)


def _e(mfg, itype, ver, url="https://x/y/f.ota"):
    return {"manufacturerCode": mfg, "imageType": itype, "fileVersion": ver, "url": url}


def test_merge_updates_overwrite_by_key():
    prev = [_e(65278, 1, 100), _e(65278, 2, 100)]
    upd = [_e(65278, 2, 101)]                       # newer door-sensor image
    out = s.merge_indexes(prev, upd)
    by = {(e["manufacturerCode"], e["imageType"]): e for e in out}
    assert by[(65278, 1)]["fileVersion"] == 100     # untouched repo retained
    assert by[(65278, 2)]["fileVersion"] == 101     # overwritten
    assert len(out) == 2


def test_merge_adds_new_device():
    out = s.merge_indexes([_e(65278, 1, 100)], [_e(65278, 9, 1)])
    assert len(out) == 2
