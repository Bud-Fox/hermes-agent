"""Lightweight pre-flight health gate for pinned providers before subagent spawn."""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

_CACHE: dict[str, tuple[float, bool]] = {}
DEFAULT_TTL_MINUTES = 5


def _cache_key(provider: Optional[str], model: Optional[str], base_url: Optional[str]) -> str:
    return f"{provider or ''}|{model or ''}|{base_url or ''}"


def provider_health_cached(provider, model=None, base_url=None, ttl_minutes=DEFAULT_TTL_MINUTES):
    key = _cache_key(provider, model, base_url)
    entry = _CACHE.get(key)
    if entry is None:
        return None
    ts, healthy = entry
    if time.time() - ts > ttl_minutes * 60:
        return None
    return healthy


def record_provider_health(provider, model=None, base_url=None, healthy=True):
    key = _cache_key(provider, model, base_url)
    _CACHE[key] = (time.time(), bool(healthy))


def preflight_provider_health(provider, model=None, base_url=None, probe: Optional[Callable] = None,
                             ttl_minutes=DEFAULT_TTL_MINUTES, force=False) -> bool:
    """Return True if provider considered healthy. Uses cache <= ttl; otherwise probe (fast call)."""
    if not force:
        cached = provider_health_cached(provider, model, base_url, ttl_minutes=ttl_minutes)
        if cached is not None:
            return cached
    healthy = True
    if probe is not None:
        try:
            healthy = bool(probe())
        except Exception:
            healthy = False
    # Heuristic dead-pool signs could be passed via probe; default healthy.
    record_provider_health(provider, model, base_url, healthy=healthy)
    return healthy


def is_dead_pool(provider, model=None, base_url=None) -> bool:
    return provider_health_cached(provider, model, base_url) is False


def clear_health_cache():
    _CACHE.clear()
