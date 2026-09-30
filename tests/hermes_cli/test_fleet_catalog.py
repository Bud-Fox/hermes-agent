from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from hermes_cli.fleet_catalog import (
    apply_fleet_catalog,
    canonical_profile_config_paths,
    load_fleet_catalog,
    static_catalog_hash,
)


@pytest.fixture
def fleet_root(tmp_path, monkeypatch):
    root = tmp_path / ".hermes"
    root.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(root))
    return root


def _write_catalog(root: Path) -> None:
    (root / "catalog.shared.yaml").write_text(
        yaml.safe_dump({
            "version": 1,
            "providers": {
                "MiXeD/Provider": {
                    "name": "Literal",
                    "models": ["Vendor/Model~Exact", "Case/Sensitive-ID"],
                }
            },
        }, sort_keys=False),
        encoding="utf-8",
    )


def test_catalog_replaces_provider_mapping_and_preserves_literal_ids(fleet_root):
    _write_catalog(fleet_root)
    out = apply_fleet_catalog({
        "model": {"provider": "legacy", "default": "keep-local"},
        "providers": {"legacy": {"models": ["old"]}},
    })
    assert list(out["providers"]) == ["MiXeD/Provider"]
    assert out["providers"]["MiXeD/Provider"]["models"] == [
        "Vendor/Model~Exact", "Case/Sensitive-ID"]
    assert out["model"] == {"provider": "legacy", "default": "keep-local"}


def test_missing_catalog_is_passive(fleet_root):
    config = {"providers": {"legacy": {"models": ["old"]}}}
    assert load_fleet_catalog() is None
    assert apply_fleet_catalog(config) == config


def test_canonical_profile_discovery_has_no_fixed_roster(fleet_root):
    (fleet_root / "config.yaml").write_text("model: default\n", encoding="utf-8")
    profiles = fleet_root / "profiles"
    for name in ("alpha", "surprise-profile"):
        home = profiles / name
        home.mkdir(parents=True)
        (home / "SOUL.md").write_text(name, encoding="utf-8")
    ghost = profiles / "not-a-profile"
    ghost.mkdir(parents=True)

    assert canonical_profile_config_paths() == (
        fleet_root / "config.yaml",
        profiles / "alpha" / "config.yaml",
        profiles / "surprise-profile" / "config.yaml",
    )


def test_new_profile_inherits_identical_static_hash(fleet_root):
    _write_catalog(fleet_root)
    before = static_catalog_hash(apply_fleet_catalog({"model": {"default": "one"}}))
    new_profile = fleet_root / "profiles" / "later"
    new_profile.mkdir(parents=True)
    (new_profile / "config.yaml").write_text("model:\n  default: two\n", encoding="utf-8")
    raw = yaml.safe_load((new_profile / "config.yaml").read_text(encoding="utf-8"))
    assert static_catalog_hash(apply_fleet_catalog(raw)) == before


def test_hash_ignores_health_and_profile_selection(fleet_root):
    _write_catalog(fleet_root)
    a = apply_fleet_catalog({"model": {"default": "a"}})
    b = apply_fleet_catalog({"model": {"default": "b"}})
    b["providers"]["MiXeD/Provider"].update({
        "glyph": "●", "readiness": "ready", "ready_keys": 4,
        "preferred_model": "Vendor/Model~Exact", "health_timestamp": 123,
    })
    assert static_catalog_hash(a) == static_catalog_hash(b)
