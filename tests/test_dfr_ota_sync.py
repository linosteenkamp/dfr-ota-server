import sys
from pathlib import Path
import pytest

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
