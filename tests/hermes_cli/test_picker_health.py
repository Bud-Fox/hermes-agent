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
