# tests/hermes_cli/test_picker_health.py
from hermes_cli.picker_health import parse_stats

STATS = {
    "pools": {
        "gemini": [{"status": "active", "readiness_score": 80},
                   {"status": "cooldown", "readiness_score": 10, "cooldown_remaining": 42}],
        "openrouter": [{"status": "depleted", "readiness_score": 0} for _ in range(21)],
    },
    "models": {
        "gemini": [{"id": "gemini-3.8-flash", "enabled": True, "context": 1048576}],
        "openrouter": [{"id": "google/gemini-3.8-flash", "enabled": True, "context": 1048576}],
    },
}

def test_ready_vendor_has_at_least_one_active_key_above_floor():
    h = parse_stats(STATS, readiness_floor=40)
    assert h["gemini"].is_ready() is True
    assert h["gemini"].ready_keys == 1
    assert h["openrouter"].is_ready() is False
    assert h["openrouter"].ready_keys == 0

def test_million_classification_is_strict_ge_1e6():
    h = parse_stats(STATS)
    assert h["gemini"].models["gemini-3.8-flash"].is_million() is True

def test_depleted_vendor_reports_status_counts():
    h = parse_stats(STATS)
    assert h["openrouter"].statuses.get("depleted") == 21

def test_fetch_health_fails_open_on_malformed_but_valid_json(monkeypatch):
    # router reachable but returns structurally wrong JSON (pools as a list) -> must return {}
    import io, json as _json
    from hermes_cli import picker_health
    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return _json.dumps({"pools": [1, 2, 3], "models": "nope"}).encode()
    monkeypatch.setattr(picker_health.urllib.request, "urlopen", lambda *a, **k: _Resp())
    assert picker_health.fetch_health("http://router.invalid") == {}

def test_fetch_health_fails_open_on_network_error(monkeypatch):
    from hermes_cli import picker_health
    def _boom(*a, **k): raise OSError("connection refused")
    monkeypatch.setattr(picker_health.urllib.request, "urlopen", _boom)
    assert picker_health.fetch_health("http://router.invalid") == {}


def test_fleet_mode_preserves_ready_degraded_and_unknown_glyph_payloads():
    from hermes_cli.model_switch_providers import _apply_health_overlay

    stats = {
        "pools": {
            "ready": [{"status": "active", "readiness_score": 80,
                       "supported_models": ["ready/one"]}],
            "degraded": [
                {"status": "active", "readiness_score": 80,
                 "supported_models": ["degraded/one"]},
                {"status": "cooldown", "readiness_score": 0},
            ],
        },
        "models": {
            "ready": [{"id": "ready/one", "context": 1_000_000}],
            "degraded": [{"id": "degraded/one", "context": 1_000_000}],
        },
    }
    rows = [
        {"slug": "fleet-ready", "provider_id": "contabo-ready", "models": ["ready/one"],
         "total_models": 1, "is_current": False},
        {"slug": "fleet-degraded", "provider_id": "contabo-degraded", "models": ["degraded/one"],
         "total_models": 1, "is_current": False},
        {"slug": "fleet-unknown", "provider_id": "contabo-unknown", "models": ["unknown/one"],
         "total_models": 1, "is_current": False},
    ]
    out = _apply_health_overlay(rows, parse_stats(stats), {})
    payload = {row["slug"]: (row["readiness"], row["glyph"]) for row in out}
    assert payload == {
        "fleet-ready": ("ready", "●"),
        "fleet-degraded": ("partial", "◐"),
        "fleet-unknown": ("unknown", "·"),
    }


def test_router_unreachable_keeps_fleet_rows_with_unknown_glyph():
    from hermes_cli.model_switch_providers import _apply_health_overlay

    rows = [
        {"slug": "fleet-a", "models": ["A"], "total_models": 1, "is_current": False},
        {"slug": "fleet-b", "models": ["B"], "total_models": 1, "is_current": False},
    ]
    out = _apply_health_overlay(rows, {}, {})
    assert [(row["slug"], row["readiness"], row["glyph"]) for row in out] == [
        ("fleet-a", "unknown", "·"), ("fleet-b", "unknown", "·")]
