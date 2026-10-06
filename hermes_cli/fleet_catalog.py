"""Profile-independent provider/model catalog authority."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_ALLOWED_PROVIDER_KEYS = frozenset({
    "name", "base_url", "api_mode", "key_env", "discover_models", "models",
})
_HEALTH_KEYS = frozenset({
    "glyph", "readiness", "ready_keys", "readiness_score", "ready_model_ids",
    "has_million_ready", "preferred_model", "not_ready_collapsed", "picker_models",
    "health_timestamp", "cooldown", "cooldown_remaining", "pool_state",
})


@dataclass(frozen=True)
class FleetCatalog:
    version: int
    providers: dict[str, dict[str, Any]]
    path: Path
    delegation: dict[str, Any] | None = None


def fleet_root() -> Path:
    """Resolve the machine fleet root independently of the active profile."""
    from hermes_constants import get_default_hermes_root
    return get_default_hermes_root()


def catalog_path() -> Path:
    return fleet_root() / "catalog.shared.yaml"


def _validate_provider(provider_id: Any, value: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(provider_id, str) or not provider_id:
        raise ValueError("fleet provider IDs must be non-empty strings")
    if not isinstance(value, dict):
        raise ValueError(f"fleet provider {provider_id!r} must be a mapping")
    unknown = set(value) - _ALLOWED_PROVIDER_KEYS
    if unknown:
        raise ValueError(f"fleet provider {provider_id!r} has unsupported keys: {sorted(unknown)}")
    models = value.get("models", [])
    if not isinstance(models, list) or any(not isinstance(model, str) or not model for model in models):
        raise ValueError(f"fleet provider {provider_id!r} models must be non-empty strings")
    if len(models) != len(set(models)):
        raise ValueError(f"fleet provider {provider_id!r} contains duplicate model IDs")
    for key in ("name", "base_url", "api_mode", "key_env"):
        if key in value and not isinstance(value[key], str):
            raise ValueError(f"fleet provider {provider_id!r}.{key} must be a string")
    if "discover_models" in value and not isinstance(value["discover_models"], bool):
        raise ValueError(f"fleet provider {provider_id!r}.discover_models must be boolean")
    return provider_id, copy.deepcopy(value)


def validate_fleet_catalog_payload(raw: Any, path: Path) -> FleetCatalog:
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError(f"{path} must be a fleet catalog with version: 1")
    providers = raw.get("providers")
    if not isinstance(providers, dict):
        raise ValueError(f"{path} providers must be a mapping")
    validated = dict(_validate_provider(key, value) for key, value in providers.items())
    policy = None
    if "delegation" in raw:
        policy = raw["delegation"]
        if not isinstance(policy, dict) or set(policy) - {"excluded_providers"}:
            raise ValueError("fleet delegation must be a mapping with only excluded_providers")
        excluded = policy.get("excluded_providers", [])
        if (not isinstance(excluded, list)
                or any(not isinstance(item, str) or not item for item in excluded)
                or len(excluded) != len(set(excluded))):
            raise ValueError("fleet delegation.excluded_providers must be unique non-empty strings")
        policy = {"excluded_providers": list(excluded)}
    return FleetCatalog(version=1, providers=validated, path=path, delegation=policy)


def load_fleet_catalog() -> FleetCatalog | None:
    path = catalog_path()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    # Pre-v1 machine files used these keys.  They are not fleet authority and
    # must remain passive until an explicit v1 migration activates a catalog.
    if isinstance(raw, dict) and "version" not in raw and (
            "contabo_models" in raw or "provider_meta" in raw):
        return None
    return validate_fleet_catalog_payload(raw, path)


def apply_fleet_catalog(config: dict) -> dict:
    catalog = load_fleet_catalog()
    if catalog is None:
        return config
    out = copy.deepcopy(config)
    out["providers"] = copy.deepcopy(catalog.providers)
    if catalog.delegation is not None:
        delegation = out.get("delegation")
        if not isinstance(delegation, dict):
            delegation = {}
        excluded = set(catalog.delegation["excluded_providers"])
        delegation["_shared_policy"] = True
        delegation["routes"] = {
            provider: {"models": list(value["models"])}
            for provider, value in catalog.providers.items()
            if provider not in excluded and value.get("models")
        }
        for key in ("model", "provider", "base_url", "api_key", "api_mode"):
            delegation.pop(key, None)
        out["delegation"] = delegation
    return out


def _static_providers(config: dict) -> dict[str, dict[str, Any]]:
    providers = config.get("providers")
    if not isinstance(providers, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for provider_id, value in providers.items():
        if not isinstance(value, dict):
            continue
        out[str(provider_id)] = {
            key: copy.deepcopy(item)
            for key, item in value.items()
            if key not in _HEALTH_KEYS
        }
    return out


def static_catalog_hash(config: dict) -> str:
    canonical = json.dumps(
        {"version": 1, "providers": _static_providers(config)},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def canonical_profile_config_paths() -> tuple[Path, ...]:
    from hermes_cli.profiles import _get_default_hermes_home, _iter_named_profile_dirs
    root = _get_default_hermes_home()
    return (root / "config.yaml", *(home / "config.yaml" for home in _iter_named_profile_dirs()))


def provider_allowlist() -> frozenset[str] | None:
    catalog = load_fleet_catalog()
    return frozenset(catalog.providers) if catalog is not None else None
