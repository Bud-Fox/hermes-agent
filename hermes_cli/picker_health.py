"""Router /api/stats → picker readiness. $0 public endpoint, no secrets used.

Single source of truth for "will this provider/model answer right now": the router
already computes per-key readiness_score and per-model context. The picker READS this;
it never hides catalog rows (user's standing principle: barriers live in ranking/routing).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import json
import time
import urllib.request

MILLION = 1_000_000


@dataclass
class ModelHealth:
    model_id: str
    context: int
    enabled: bool = True
    usable_keys: int = 0  # ready keys that SUPPORT this model AND are not in model-cooldown now
    probed_ok_keys: int = 0  # active keys that list this id in supported_models (a LIVE success)
    def is_million(self) -> bool:
        return self.context >= MILLION
    def is_usable(self) -> bool:
        return self.usable_keys >= 1


@dataclass
class VendorHealth:
    vendor: str
    ready_keys: int
    total_keys: int
    statuses: dict = field(default_factory=dict)
    min_cooldown_remaining: Optional[float] = None
    models: dict = field(default_factory=dict)
    ever_probed: bool = False  # any ACTIVE key with a non-empty supported_models (vendor answers at all)
    def is_ready(self) -> bool:
        return self.ready_keys >= 1


def ready_key(k: dict, floor: int) -> bool:
    try:
        return k.get("status") == "active" and float(k.get("readiness_score", 0)) >= floor
    except (TypeError, ValueError):
        return False


def _model_usable_on_key(k: dict, mid: str, now: float) -> bool:
    """True when ready key ``k`` can serve model ``mid`` RIGHT NOW.

    Fail-open by omission: a router that reports neither ``supported_models`` nor
    ``model_cooldowns`` leaves both checks inert, so a ready key counts as usable (today's
    behavior). Excluded only on POSITIVE evidence: the model sits in ``unsupported_models``, an
    explicit non-empty ``supported_models`` omits it, or a ``model_cooldowns[mid]`` timestamp is
    still in the future.
    """
    unsup = k.get("unsupported_models") or []
    if mid in unsup:
        return False
    sup = k.get("supported_models")
    if isinstance(sup, list) and sup and mid not in sup:
        return False
    mc = k.get("model_cooldowns") or {}
    until = mc.get(mid)
    if isinstance(until, (int, float)) and until > now:
        return False
    return True


def parse_stats(stats: dict, *, readiness_floor: int = 40) -> dict:
    now = time.time()
    pools = stats.get("pools") or {}
    models_by_vendor = stats.get("models") or {}
    out: dict = {}
    for vendor, keys in pools.items():
        keys = keys or []
        statuses: dict = {}
        cooldowns = []
        ready = 0
        ready_keys_list = []
        for k in keys:
            st = k.get("status")
            statuses[st] = statuses.get(st, 0) + 1
            if ready_key(k, readiness_floor):
                ready += 1
                ready_keys_list.append(k)
            cr = k.get("cooldown_remaining")
            if isinstance(cr, (int, float)) and cr > 0:
                cooldowns.append(float(cr))
        mh = {}
        # A model was proven LIVE-OK on a key iff its id is in that key's supported_models. The
        # vendor "answers at all" iff any ACTIVE key probed something (non-empty supported_models);
        # this distinguishes a structurally-dead catalog id (0 probes at a probing vendor) from a
        # fully-depleted vendor (nobody probed anything -> годность unknown, never auto-hidden).
        active_keys = [k for k in keys if k.get("status") == "active"]
        ever_probed = any((k.get("supported_models") or []) for k in active_keys)
        for m in models_by_vendor.get(vendor, []) or []:
            mid = m.get("id")
            if not mid:
                continue
            usable = sum(1 for k in ready_keys_list if _model_usable_on_key(k, mid, now))
            probed_ok = sum(1 for k in active_keys if mid in (k.get("supported_models") or []))
            mh[mid] = ModelHealth(
                mid, int(m.get("context") or 0), bool(m.get("enabled", True)),
                usable_keys=usable, probed_ok_keys=probed_ok)
        out[vendor] = VendorHealth(
            vendor=vendor, ready_keys=ready, total_keys=len(keys),
            statuses=statuses, min_cooldown_remaining=min(cooldowns) if cooldowns else None,
            models=mh, ever_probed=ever_probed)
    return out


def fetch_health(base_url: str, *, timeout: float = 4.0, readiness_floor: int = 40) -> dict:
    try:
        url = base_url.rstrip("/") + "/api/stats"
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (trusted router)
            stats = json.loads(resp.read().decode("utf-8"))
        return parse_stats(stats, readiness_floor=readiness_floor)
    except Exception:
        return {}  # fail-open: picker behaves exactly as before when router unreachable OR returns garbage
