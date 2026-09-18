"""picker_models: a reversible display-only filter that never mutates the full catalog.

Contract:
- With hide_unusable=True the overlay stamps each row with ``picker_models`` = the models the
  picker should DISPLAY (usable now; million-only when million_only is also set). The full
  ``models`` list is left untouched so Edit-Models still shows everything.
- With hide_unusable=False, ``picker_models`` mirrors ``models`` (no filtering).
- Fail-open: empty health -> nothing is usable, so hide_unusable must NOT blank the picker; it
  falls back to the full ``models`` (never hand the user an empty picker on a router hiccup).
"""
from hermes_cli.model_switch_providers import _apply_health_overlay
from hermes_cli.picker_health import parse_stats

STATS = {
    "pools": {
        "nvidia": [{
            "status": "active", "readiness_score": 80,
            "supported_models": ["nvidia/nemotron-3-super-120b-a12b", "moonshotai/kimi-k3"],
            "model_cooldowns": {"moonshotai/kimi-k3": 9_999_999_999.0},  # kimi cooling far future
        }],
    },
    "models": {
        "nvidia": [
            {"id": "nvidia/nemotron-3-super-120b-a12b", "context": 1_048_576, "enabled": True},
            {"id": "moonshotai/kimi-k3", "context": 131_072, "enabled": True},
            {"id": "deepseek-ai/deepseek-r1", "context": 163_840, "enabled": True},
        ],
    },
}

FULL = ["nvidia/nemotron-3-super-120b-a12b", "moonshotai/kimi-k3", "deepseek-ai/deepseek-r1"]


def _row():
    return {"slug": "contabo-nvidia", "is_current": False,
            "api_url": "http://194.34.232.59:8790/vendor/nvidia/v1",
            "models": list(FULL), "total_models": 3}


def test_hide_unusable_million_only_filters_picker_models_only():
    health = parse_stats(STATS, readiness_floor=40)
    rows = [_row()]
    out = _apply_health_overlay(rows, health, {
        "readiness_floor": 40, "collapse_not_ready": False,
        "million_only": True, "hide_unusable": True})
    r = out[0]
    # picker shows ONLY the usable 1M model (nemotron); kimi (cooling+128K) and deepseek (128K, no
    # support entry -> not usable) are dropped from the PICKER view.
    assert r["picker_models"] == ["nvidia/nemotron-3-super-120b-a12b"]
    # full catalog is UNTOUCHED (Edit-Models still sees all three)
    assert r["models"] == FULL


def test_hide_unusable_without_million_keeps_usable_non_million():
    # kimi is on cooldown -> not usable; only nemotron is usable (supported + not cooling).
    health = parse_stats(STATS, readiness_floor=40)
    rows = [_row()]
    out = _apply_health_overlay(rows, health, {
        "readiness_floor": 40, "collapse_not_ready": False,
        "million_only": False, "hide_unusable": True})
    assert out[0]["picker_models"] == ["nvidia/nemotron-3-super-120b-a12b"]
    assert out[0]["models"] == FULL


def test_hide_unusable_false_mirrors_full_models_minus_dead_ids():
    health = parse_stats(STATS, readiness_floor=40)
    rows = [_row()]
    out = _apply_health_overlay(rows, health, {
        "readiness_floor": 40, "collapse_not_ready": False,
        "million_only": True, "hide_unusable": False})
    # No readiness/million filtering requested -> picker_models mirrors the full list EXCEPT proven
    # dead ids: deepseek-r1 is catalogued but appears in NO key's supported_models while nvidia does
    # probe (nemotron/kimi), so it is auto-classed dead and dropped even with hide_unusable off.
    assert out[0]["picker_models"] == [
        "nvidia/nemotron-3-super-120b-a12b", "moonshotai/kimi-k3"]
    assert out[0]["models"] == FULL  # full catalog untouched (Edit-Models)


def test_fail_open_empty_health_does_not_blank_picker():
    # router unreachable -> health={} -> nothing "usable"; hide_unusable must fall back to full list
    rows = [_row()]
    out = _apply_health_overlay(rows, {}, {
        "readiness_floor": 40, "collapse_not_ready": False,
        "million_only": True, "hide_unusable": True})
    assert out[0]["picker_models"] == FULL  # NEVER an empty picker on a router hiccup


def test_block_models_always_removed_even_fail_open():
    # A structurally-broken id (proven by a live 400, invisible to /api/stats) must be dropped from
    # picker_models even on the fail-open path — a block is NOT restored the way the health filter is.
    rows = [_row()]
    out = _apply_health_overlay(rows, {}, {  # empty health => fail-open branch
        "readiness_floor": 40, "collapse_not_ready": False, "million_only": True,
        "hide_unusable": True, "block_models": ["moonshotai/kimi-k3"]})
    assert "moonshotai/kimi-k3" not in out[0]["picker_models"]  # blocked, despite fail-open
    assert "moonshotai/kimi-k3" in out[0]["models"]              # full catalog still intact


def test_block_models_removed_when_hide_unusable_off():
    # block applies to the plain mirror path too (hide_unusable off).
    rows = [_row()]
    out = _apply_health_overlay(rows, {}, {
        "readiness_floor": 40, "collapse_not_ready": False,
        "hide_unusable": False, "block_models": ["deepseek-ai/deepseek-r1"]})
    assert out[0]["picker_models"] == [
        "nvidia/nemotron-3-super-120b-a12b", "moonshotai/kimi-k3"]
    assert out[0]["models"] == FULL  # full catalog untouched
