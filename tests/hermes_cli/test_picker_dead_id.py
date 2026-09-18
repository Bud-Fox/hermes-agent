"""Auto dead-id detection: a catalog id no key ever probed OK, at a vendor that DOES probe.

Root fix for class A (id-mismatch / structurally-broken ids like an unresolvable ``~alias``):
the router exposes a catalog id in ``models[]`` that no key can actually serve, while working
ids land in that key's ``supported_models`` after a live success. So a dead id is one with
zero positive probes AT A VENDOR THAT HAS PROBED SOMETHING — distinct from a fully-depleted
vendor (no probes at all -> unknown, never auto-hidden).
"""
from hermes_cli.picker_health import parse_stats
from hermes_cli.model_switch_providers import _apply_health_overlay


def _stats():
    # gemini: 3 active keys. Working ids appear in supported_models; the ~alias never does.
    return {
        "pools": {
            "gemini": [
                {"status": "active", "readiness_score": 70,
                 "supported_models": ["gemini-3.8-flash", "gemini-3.7-flash"]},
                {"status": "active", "readiness_score": 70,
                 "supported_models": ["gemini-3.8-flash"]},
                {"status": "active", "readiness_score": 70,
                 "supported_models": []},  # never probed anything (empty)
            ],
            # anthropic: every key inactive -> vendor never probed -> depleted, NOT dead-id
            "anthropic": [
                {"status": "cooldown", "readiness_score": 0, "supported_models": []},
            ],
        },
        "models": {
            "gemini": [
                {"id": "gemini-3.8-flash", "context": 1048576, "enabled": True},
                {"id": "gemini-3.7-flash", "context": 1048576, "enabled": True},
                {"id": "~google/gemini-flash-latest", "context": 1048576, "enabled": True},
            ],
            "anthropic": [
                {"id": "claude-opus-5", "context": 1000000, "enabled": True},
            ],
        },
    }


def test_probed_ok_keys_counts_only_positive_evidence():
    h = parse_stats(_stats(), readiness_floor=40)
    gm = h["gemini"].models
    assert gm["gemini-3.8-flash"].probed_ok_keys == 2   # in supported_models of 2 keys
    assert gm["gemini-3.7-flash"].probed_ok_keys == 1
    assert gm["~google/gemini-flash-latest"].probed_ok_keys == 0  # never in any supported_models


def test_vendor_ever_probed_flag():
    h = parse_stats(_stats(), readiness_floor=40)
    assert h["gemini"].ever_probed is True     # at least one active key with non-empty supported
    assert h["anthropic"].ever_probed is False  # no active key probed anything


def test_dead_id_dropped_from_picker_at_probing_vendor():
    # gemini vendor probes; the ~alias has zero positive probes -> dead -> dropped from picker_models
    rows = [{"slug": "gemini", "provider_id": "contabo-gemini", "is_current": False,
             "api_url": "http://r/vendor/gemini/v1",
             "models": ["gemini-3.8-flash", "gemini-3.7-flash", "~google/gemini-flash-latest"],
             "total_models": 3}]
    h = parse_stats(_stats(), readiness_floor=40)
    out = _apply_health_overlay(rows, h, {
        "readiness_floor": 40, "collapse_not_ready": False,
        "million_only": False, "hide_unusable": True})
    assert "~google/gemini-flash-latest" not in out[0]["picker_models"]  # auto-blocked, no manual list
    assert "gemini-3.8-flash" in out[0]["picker_models"]
    assert "~google/gemini-flash-latest" in out[0]["models"]  # full catalog intact (Edit-Models)


def test_depleted_vendor_not_treated_as_dead_id():
    # anthropic never probed (all inactive) -> models are 'unknown', fail-open, NOT auto-dropped
    rows = [{"slug": "anthropic", "provider_id": "contabo-anthropic", "is_current": False,
             "api_url": "http://r/vendor/anthropic/v1",
             "models": ["claude-opus-5"], "total_models": 1}]
    h = parse_stats(_stats(), readiness_floor=40)
    out = _apply_health_overlay(rows, h, {
        "readiness_floor": 40, "collapse_not_ready": False,
        "million_only": False, "hide_unusable": True})
    assert out[0]["picker_models"] == ["claude-opus-5"]  # depleted != dead; keep it (fail-open)


def test_dead_id_dropped_even_without_hide_unusable():
    # A proven dead id is ALWAYS removed from the picker view (like block_models), independent of
    # the hide_unusable toggle — hide_unusable governs the readiness/million filter, not dead ids.
    rows = [{"slug": "gemini", "provider_id": "contabo-gemini", "is_current": False,
             "api_url": "http://r/vendor/gemini/v1",
             "models": ["gemini-3.8-flash", "~google/gemini-flash-latest"], "total_models": 2}]
    h = parse_stats(_stats(), readiness_floor=40)
    out = _apply_health_overlay(rows, h, {
        "readiness_floor": 40, "collapse_not_ready": False,
        "million_only": False, "hide_unusable": False})
    assert "~google/gemini-flash-latest" not in out[0]["picker_models"]
    assert "gemini-3.8-flash" in out[0]["picker_models"]
