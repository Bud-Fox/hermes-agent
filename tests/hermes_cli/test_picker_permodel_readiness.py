"""Per-model usable-key readiness in parse_stats.

Contract (not snapshot): a model on cooldown across every ready key that supports it has
usable_keys == 0; a free model has usable_keys > 0; a router that reports NO per-model cooldown
or support fields degrades to usable_keys == ready_keys (today's behavior — fail-open).

Regression for the kimi-k3 case: nvidia pool keys alive but 'moonshotai/kimi-k3' on 300s model
cooldown on all of them -> the picker must see the MODEL as not usable, even though ready_keys>0.
"""
import time

from hermes_cli.picker_health import parse_stats


def _stats(model_cooldowns_per_key, *, support=None, ctx=1_048_576):
    """Build a /api/stats-shaped dict: N nvidia keys, one model 'm1'.

    model_cooldowns_per_key: list (one entry per key) of {mid: until_ts} dicts.
    support: optional list (one per key) of supported_models lists.
    """
    now = time.time()
    keys = []
    for i, mc in enumerate(model_cooldowns_per_key):
        k = {
            "status": "active",
            "readiness_score": 80,
            "model_cooldowns": mc,
        }
        if support is not None:
            k["supported_models"] = support[i]
        keys.append(k)
    return {
        "pools": {"nvidia": keys},
        "models": {"nvidia": [{"id": "m1", "context": ctx, "enabled": True}]},
    }


def test_model_cooling_on_all_ready_keys_is_not_usable():
    now = time.time()
    # 3 ready keys, m1 cooling (future ts) on ALL of them
    stats = _stats([{"m1": now + 300}, {"m1": now + 120}, {"m1": now + 60}])
    h = parse_stats(stats)
    nv = h["nvidia"]
    assert nv.ready_keys == 3                     # keys themselves are alive
    assert nv.models["m1"].usable_keys == 0       # but the MODEL is unusable right now
    assert nv.models["m1"].is_usable() is False


def test_model_free_on_some_keys_is_usable():
    now = time.time()
    # cooling on 2 of 3; 1 key free for m1
    stats = _stats([{"m1": now + 300}, {"m1": now + 120}, {}])
    h = parse_stats(stats)
    nv = h["nvidia"]
    assert nv.ready_keys == 3
    assert nv.models["m1"].usable_keys == 1
    assert nv.models["m1"].is_usable() is True


def test_expired_cooldown_counts_as_usable():
    now = time.time()
    # cooldown timestamp in the PAST -> not cooling anymore
    stats = _stats([{"m1": now - 10}, {"m1": now - 500}])
    h = parse_stats(stats)
    assert h["nvidia"].models["m1"].usable_keys == 2


def test_unsupported_model_excluded_from_usable():
    now = time.time()
    # both keys advertise an explicit support list that does NOT contain m1
    stats = _stats([{}, {}], support=[["other/model"], ["other/model"]])
    h = parse_stats(stats)
    assert h["nvidia"].models["m1"].usable_keys == 0


def test_supported_model_in_explicit_list_is_usable():
    stats = _stats([{}, {}], support=[["m1", "x"], ["m1"]])
    h = parse_stats(stats)
    assert h["nvidia"].models["m1"].usable_keys == 2


def test_fail_open_no_cooldown_or_support_fields_uses_ready_count():
    # A router that reports neither model_cooldowns nor supported_models: every ready key is
    # assumed usable for the model -> usable_keys == ready_keys (today's behavior, no regression).
    stats = {
        "pools": {"nvidia": [
            {"status": "active", "readiness_score": 80},
            {"status": "active", "readiness_score": 80},
        ]},
        "models": {"nvidia": [{"id": "m1", "context": 1_048_576, "enabled": True}]},
    }
    h = parse_stats(stats)
    nv = h["nvidia"]
    assert nv.ready_keys == 2
    assert nv.models["m1"].usable_keys == 2
    assert nv.models["m1"].is_usable() is True


def test_non_ready_keys_do_not_count_toward_usable():
    now = time.time()
    # one active+ready free key, one dead key (should not count) even though not cooling
    stats = {
        "pools": {"nvidia": [
            {"status": "active", "readiness_score": 80, "model_cooldowns": {}},
            {"status": "dead", "readiness_score": 0, "model_cooldowns": {}},
        ]},
        "models": {"nvidia": [{"id": "m1", "context": 1_048_576, "enabled": True}]},
    }
    h = parse_stats(stats)
    assert h["nvidia"].models["m1"].usable_keys == 1
