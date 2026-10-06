"""Offline shared delegation policy projection contracts."""
import copy

import pytest
import yaml

from hermes_cli.fleet_catalog import apply_fleet_catalog


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    import hermes_cli.fleet_catalog as fleet
    path = tmp_path / "catalog.shared.yaml"
    monkeypatch.setattr(fleet, "catalog_path", lambda: path)
    return path


def payload():
    return {
        "version": 1,
        "providers": {
            "zai-coding-plan": {"models": ["glm-5.3"], "key_env": "TEST_ZAI_KEY"},
            "Literal/Provider": {"models": ["Vendor/Exact~ID"]},
            "contabo-anthropic": {"models": ["claude"]},
            "anthropic": {"models": ["claude"]},
            "contabo-openrouter": {"models": ["other"]},
            "discovery-only": {"models": [], "discover_models": True},
        },
        "delegation": {"excluded_providers": [
            "contabo-anthropic", "anthropic", "contabo-openrouter"]},
    }


def test_shared_policy_projects_routes_without_changing_local_config(catalog):
    raw = payload()
    catalog.write_text(yaml.safe_dump(raw))
    config = {"delegation": {
        "model": "old", "provider": "custom", "base_url": "https://old.invalid",
        "api_key": "local-test-only", "api_mode": "old", "max_concurrent_children": 3,
        "routes": {"old": {"models": ["old"]}},
    }, "model": {"default": "keep-picker-selection"}}
    before = copy.deepcopy(config)
    out = apply_fleet_catalog(config)
    assert out["delegation"]["routes"] == {
        "zai-coding-plan": {"models": ["glm-5.3"]},
        "Literal/Provider": {"models": ["Vendor/Exact~ID"]},
    }
    for key in ("model", "provider", "base_url", "api_key", "api_mode"):
        assert not out["delegation"].get(key)
    assert out["delegation"]["max_concurrent_children"] == 3
    assert out["providers"] == raw["providers"]
    assert out["model"] == config["model"]
    assert config == before


def test_default_named_and_new_profiles_inherit_same_routes(catalog, monkeypatch):
    catalog.write_text(yaml.safe_dump(payload()))
    routes = []
    for name, limit in (("default", 1), ("alpha", 4), ("new-profile", 7)):
        monkeypatch.setenv("HERMES_HOME", str(catalog.parent / name))
        out = apply_fleet_catalog({"delegation": {"max_concurrent_children": limit}})
        routes.append(out["delegation"]["routes"])
        assert out["delegation"]["max_concurrent_children"] == limit
    assert routes[0] == routes[1] == routes[2]


def test_catalog_without_policy_preserves_legacy_delegation(catalog):
    raw = payload()
    del raw["delegation"]
    catalog.write_text(yaml.safe_dump(raw))
    config = {"delegation": {"provider": "legacy", "model": "old", "routes": {}}}
    assert apply_fleet_catalog(config)["delegation"] == config["delegation"]


@pytest.mark.parametrize("policy", [None, [], "bad", {"excluded_providers": "bad"},
    {"excluded_providers": [None]}, {"excluded_providers": [""]},
    {"excluded_providers": ["anthropic", "anthropic"]}, {"unknown": []}])
def test_invalid_policy_fails_closed(catalog, policy):
    raw = payload()
    raw["delegation"] = policy
    catalog.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="delegation"):
        apply_fleet_catalog({})


def test_catalog_signature_changes_when_policy_changes(catalog):
    from hermes_cli.config import _load_config_cache_sig
    catalog.write_text(yaml.safe_dump(payload()))
    before = _load_config_cache_sig(catalog.parent / "config.yaml")
    raw = payload()
    raw["delegation"]["excluded_providers"] = []
    catalog.write_text(yaml.safe_dump(raw))
    assert _load_config_cache_sig(catalog.parent / "config.yaml") != before
