import json
import sys
from pathlib import Path

import pytest

import models
from models import (
    NetworkProfile,
    ProfileGroup,
    ProfileStore,
    ProfileStoreError,
    mask_to_prefix,
    parse_ipv4,
)


@pytest.mark.parametrize(
    ("mask", "prefix"),
    [("255.0.0.0", 8), ("255.255.255.0", 24), ("255.255.255.192", 26), ("255.255.255.255", 32)],
)
def test_mask_to_prefix_valid(mask, prefix):
    assert mask_to_prefix(mask) == prefix


@pytest.mark.parametrize(
    "mask", ["255.0.255.0", "255.255.255", "256.0.0.0", "0.255.255.255", "0.0.0.0", "", None, "x"]
)
def test_mask_to_prefix_rejects_invalid(mask):
    with pytest.raises(ValueError):
        mask_to_prefix(mask)


def test_parse_ipv4_normalizes_and_rejects():
    assert parse_ipv4(" 10.0.0.1 ", "IP") == "10.0.0.1"
    for bad in ['1.1.1.1"; Write-Host x; "', "1.2.3", "010.0.0.1", "", None]:
        with pytest.raises(ValueError):
            parse_ipv4(bad, "IP")


def static(**overrides):
    values = dict(name="p", ip_address="10.0.0.5", subnet_mask="255.255.255.0", gateway="10.0.0.1")
    return NetworkProfile(**{**values, **overrides})


def test_validate_accepts_static_and_dns_only():
    assert static().validate() == []
    assert static(gateway=None, dns_primary="1.1.1.1", dns_secondary="8.8.8.8").validate() == []
    assert NetworkProfile(name="dns", dns_primary="1.1.1.1").validate() == []


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        (static(ip_address="10.0.0"), "IP address is not valid"),
        (static(ip_address="127.0.0.1"), "can't be assigned"),
        (static(subnet_mask=""), "subnet mask is required"),
        (static(subnet_mask="255.0.255.0"), "Subnet mask is not valid"),
        (static(gateway="10.0.0.300"), "Gateway is not valid"),
        (static(dns_primary='1.1.1.1"; calc; "'), "Primary DNS is not valid"),
        (static(dns_secondary="8.8.8.8"), "primary DNS is empty"),
        (NetworkProfile(name="empty"), "profile is empty"),
    ],
)
def test_validate_reports_problems(profile, expected):
    assert any(expected in error for error in profile.validate())


def test_dns_only_ignores_leftover_mask_and_gateway():
    profile = NetworkProfile(name="d", subnet_mask="garbage", gateway="x", dns_primary="1.1.1.1")
    assert profile.validate() == []


def test_from_dict_ignores_unknown_keys_and_fills_name():
    profile = NetworkProfile.from_dict({"ip_address": "1.2.3.4", "notes": "from a newer version"})
    assert profile == NetworkProfile(name="Unnamed", ip_address="1.2.3.4")


def test_store_round_trip(store_path):
    groups = [
        ProfileGroup("Site", [static(name="sw"), NetworkProfile("dns", dns_primary="1.1.1.1")])
    ]
    ProfileStore.save(groups)
    assert ProfileStore.load() == groups
    assert not list(store_path.parent.glob("*.tmp")), "temp file left behind"


def test_store_missing_file_is_empty_library(store_path):
    assert ProfileStore.load() == []


def test_store_keeps_profiles_with_unknown_keys(store_path):
    data = [{"name": "G", "profiles": [{"name": "a", "ip_address": "1.2.3.4", "notes": "x"}]}]
    store_path.write_text(json.dumps(data), encoding="utf-8")
    groups = ProfileStore.load()
    assert groups[0].profiles[0].ip_address == "1.2.3.4"


@pytest.mark.parametrize("content", ["{not json", '{"name": "not a list"}', '[{"profiles": 5}]'])
def test_store_bad_file_raises_and_backs_up(store_path, content):
    store_path.write_text(content, encoding="utf-8")
    with pytest.raises(ProfileStoreError, match="Could not read"):
        ProfileStore.load()
    backups = list(store_path.parent.glob("network_profiles.corrupt-*.json"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == content
    assert store_path.read_text(encoding="utf-8") == content, "original must be left untouched"


def test_store_save_error_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(ProfileStore, "FILE_PATH", tmp_path / "missing-dir" / "p.json")
    with pytest.raises(ProfileStoreError, match="Could not save"):
        ProfileStore.save([])


def test_app_dir_is_exe_folder_when_frozen(monkeypatch, tmp_path):
    exe = tmp_path / "dist" / "Network Ed.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert models.app_dir() == exe.parent.resolve()


def test_app_dir_is_project_root_from_source():
    assert models.app_dir() == Path(__file__).resolve().parent.parent
