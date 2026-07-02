import sys
import json
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
    assert out["url"] == "http://pi.lan:8091/ota/fefe-0002-firmware-1.0.1.ota"
    assert out["sha512"] == "ab" and out["manufacturerCode"] == 65278
    assert entry["url"].startswith("https://")  # original not mutated


def test_rewrite_entry_url_strips_trailing_slash_on_base():
    entry = {"url": "https://x/y/fw.ota", "manufacturerCode": 65278, "imageType": 2}
    assert s.rewrite_entry_url(entry, "http://pi.lan:8091/")["url"] == "http://pi.lan:8091/ota/fefe-0002-fw.ota"


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


def _entry(mfg, itype, ver, blob, fname):
    return {"modelId": f"m{itype}", "manufacturerCode": mfg, "imageType": itype,
            "fileVersion": ver, "fileSize": len(blob),
            "sha512": hashlib.sha512(blob).hexdigest(),
            "url": f"https://github.com/o/r/releases/download/v/{fname}"}

class FakeFetcher:
    def __init__(self, data):
        self.data = data  # {repo: {"entries":[...], "blobs":{fname: bytes}}}
    def get_index(self, repo):
        if repo not in self.data:
            raise s.SkipRepo(repo)
        return self.data[repo]["entries"]
    def download(self, repo, filename):
        return self.data[repo]["blobs"][filename]

def test_sync_once_publishes_valid_index(tmp_path):
    b = b"\x01" * 50
    data = {"o/r1": {"entries": [_entry(65278, 2, 101, b, "door.ota")],
                      "blobs": {"door.ota": b}}}
    serve = tmp_path / "serve"
    res = s.sync_once(FakeFetcher(data), ["o/r1"], "http://pi.lan:8091",
                      serve, tmp_path / "stg", log=lambda *a: None)
    assert res == {"ok": 1, "failed": []}
    idx = json.loads((serve / "index.json").read_text())
    assert idx[0]["url"] == "http://pi.lan:8091/ota/fefe-0002-door.ota"
    assert (serve / "ota" / "fefe-0002-door.ota").read_bytes() == b

def test_sync_once_keeps_last_good_when_repo_fails(tmp_path):
    b = b"\x02" * 40
    good = {"o/r1": {"entries": [_entry(65278, 2, 101, b, "door.ota")],
                     "blobs": {"door.ota": b}}}
    serve = tmp_path / "serve"
    s.sync_once(FakeFetcher(good), ["o/r1"], "http://pi.lan:8091",
                serve, tmp_path / "stg", log=lambda *a: None)
    # Next run: same repo now returns a corrupted blob (sha mismatch) -> repo fails
    bad = {"o/r1": {"entries": [_entry(65278, 2, 102, b, "door.ota")],
                    "blobs": {"door.ota": b"\xFF" * 40}}}
    res = s.sync_once(FakeFetcher(bad), ["o/r1"], "http://pi.lan:8091",
                      serve, tmp_path / "stg", log=lambda *a: None)
    assert res["failed"] == ["o/r1"]
    idx = json.loads((serve / "index.json").read_text())
    assert idx[0]["fileVersion"] == 101                 # last-good retained
    assert (serve / "ota" / "fefe-0002-door.ota").read_bytes() == b

def test_sync_once_skips_repo_without_release(tmp_path):
    serve = tmp_path / "serve"
    res = s.sync_once(FakeFetcher({}), ["o/none"], "http://pi.lan:8091",
                      serve, tmp_path / "stg", log=lambda *a: None)
    assert res == {"ok": 0, "failed": []}
    assert json.loads((serve / "index.json").read_text()) == []

def test_sync_once_reports_get_index_error(tmp_path):
    class BoomFetcher:
        def get_index(self, repo):
            raise RuntimeError("boom")
        def download(self, repo, filename):
            raise AssertionError("download should not be called")

    serve = tmp_path / "serve"
    res = s.sync_once(BoomFetcher(), ["o/r1"], "http://pi.lan:8091",
                      serve, tmp_path / "stg", log=lambda *a: None)
    assert res["failed"] == ["o/r1"]
    idx_path = serve / "index.json"
    assert idx_path.exists()
    assert json.loads(idx_path.read_text()) == []

def test_sync_once_nested_staging_root(tmp_path):
    b = b"\x03" * 45
    good = {"o/r1": {"entries": [_entry(65278, 2, 101, b, "door.ota")],
                     "blobs": {"door.ota": b}}}
    serve = tmp_path / "serve"
    staging_root = serve / ".staging"  # nested INSIDE serve_dir
    # First run: good blob
    res = s.sync_once(FakeFetcher(good), ["o/r1"], "http://pi.lan:8091",
                      serve, staging_root, log=lambda *a: None)
    assert res == {"ok": 1, "failed": []}
    idx = json.loads((serve / "index.json").read_text())
    assert idx[0]["fileVersion"] == 101
    assert (serve / "ota" / "fefe-0002-door.ota").read_bytes() == b
    # Second run: corrupted blob (sha mismatch) -> repo fails, last-good retained
    bad = {"o/r1": {"entries": [_entry(65278, 2, 102, b, "door.ota")],
                    "blobs": {"door.ota": b"\xFF" * 45}}}
    res = s.sync_once(FakeFetcher(bad), ["o/r1"], "http://pi.lan:8091",
                      serve, staging_root, log=lambda *a: None)
    assert res["failed"] == ["o/r1"]
    idx = json.loads((serve / "index.json").read_text())
    assert idx[0]["fileVersion"] == 101                 # last-good retained
    assert (serve / "ota" / "fefe-0002-door.ota").read_bytes() == b
    # Verify no leftover .old directory in serve_dir's parent
    assert not (tmp_path / "serve.old").exists()

def test_sync_once_namespaces_colliding_filenames(tmp_path):
    # Two different repos both release an asset named "firmware-1.0.0.ota", but for
    # different device types (imageType). The served filenames must be namespaced by
    # (manufacturerCode, imageType) so neither blob overwrites the other.
    b_door = b"\x11" * 30                 # repo A: door sensor (imageType 2)
    b_soil = b"\x22" * 30                 # repo B: soil sensor (imageType 1)
    data = {
        "o/door-sensor": {"entries": [_entry(65278, 2, 100, b_door, "firmware-1.0.0.ota")],
                           "blobs": {"firmware-1.0.0.ota": b_door}},
        "o/soil-sensor": {"entries": [_entry(65278, 1, 100, b_soil, "firmware-1.0.0.ota")],
                           "blobs": {"firmware-1.0.0.ota": b_soil}},
    }
    serve = tmp_path / "serve"
    res = s.sync_once(FakeFetcher(data), ["o/door-sensor", "o/soil-sensor"],
                      "http://pi.lan:8091", serve, tmp_path / "stg",
                      log=lambda *a: None)
    assert res["ok"] == 2
    assert res["failed"] == []

    ota_dir = serve / "ota"
    door_file = ota_dir / "fefe-0002-firmware-1.0.0.ota"
    soil_file = ota_dir / "fefe-0001-firmware-1.0.0.ota"
    assert door_file.exists() and soil_file.exists()
    assert {f.name for f in ota_dir.iterdir() if f.is_file()} == {door_file.name, soil_file.name}
    assert door_file.read_bytes() == b_door
    assert soil_file.read_bytes() == b_soil

    idx = json.loads((serve / "index.json").read_text())
    urls = {e["imageType"]: e["url"] for e in idx}
    assert urls[2] == "http://pi.lan:8091/ota/fefe-0002-firmware-1.0.0.ota"
    assert urls[1] == "http://pi.lan:8091/ota/fefe-0001-firmware-1.0.0.ota"
    assert len(set(urls.values())) == 2         # distinct urls, no collision
