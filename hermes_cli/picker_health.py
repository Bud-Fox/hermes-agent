"""Router /api/stats → picker readiness. $0 public endpoint, no secrets used.

Single source of truth for "will this provider/model answer right now": the router
already computes per-key readiness_score and per-model context. The picker READS this;
it never hides catalog rows (user's standing principle: barriers live in ranking/routing).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import json
import urllib.request

MILLION = 1_000_000


@dataclass
class ModelHealth:
    model_id: str
    context: int
    enabled: bool = True
    def is_million(self) -> bool:
        return self.context >= MILLION


@dataclass
class VendorHealth:
    vendor: str
    ready_keys: int
    total_keys: int
    statuses: dict = field(default_factory=dict)
    min_cooldown_remaining: Optional[float] = None
    models: dict = field(default_factory=dict)
    def is_ready(self) -> bool:
        return self.ready_keys >= 1


def ready_key(k: dict, floor: int) -> bool:
    try:
        return k.get("status") == "active" and float(k.get("readiness_score", 0)) >= floor
    except (TypeError, ValueError):
        return False


def parse_stats(stats: dict, *, readiness_floor: int = 40) -> dict:
    pools = stats.get("pools") or {}
    models_by_vendor = stats.get("models") or {}
    out: dict = {}
    for vendor, keys in pools.items():
        keys = keys or []
        statuses: dict = {}
        cooldowns = []
        ready = 0
        for k in keys:
            st = k.get("status")
            statuses[st] = statuses.get(st, 0) + 1
            if ready_key(k, readiness_floor):
                ready += 1
            cr = k.get("cooldown_remaining")
            if isinstance(cr, (int, float)) and cr > 0:
                cooldowns.append(float(cr))
        mh = {}
        for m in models_by_vendor.get(vendor, []) or []:
            mid = m.get("id")
            if not mid:
                continue
            mh[mid] = ModelHealth(mid, int(m.get("context") or 0), bool(m.get("enabled", True)))
        out[vendor] = VendorHealth(
            vendor=vendor, ready_keys=ready, total_keys=len(keys),
            statuses=statuses, min_cooldown_remaining=min(cooldowns) if cooldowns else None,
            models=mh)
    return out


def fetch_health(base_url: str, *, timeout: float = 4.0, readiness_floor: int = 40) -> dict:
    url = base_url.rstrip("/") + "/api/stats"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (trusted router)
            stats = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}  # fail-open: picker behaves exactly as before when router unreachable
    return parse_stats(stats, readiness_floor=readiness_floor)
