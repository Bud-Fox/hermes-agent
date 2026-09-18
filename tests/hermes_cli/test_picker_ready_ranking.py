# tests/hermes_cli/test_picker_ready_ranking.py
from hermes_cli.model_switch_providers import _apply_health_overlay
from hermes_cli.picker_health import parse_stats

STATS = {
    "pools": {
        "gemini": [{"status": "active", "readiness_score": 80}],
        "openrouter": [{"status": "depleted", "readiness_score": 0} for _ in range(21)],
    },
    "models": {
        "gemini": [{"id": "gemini-3.8-flash", "enabled": True, "context": 1048576}],
        "openrouter": [{"id": "google/gemini-3.8-flash", "enabled": True, "context": 1048576}],
    },
}

def _rows():
    return [
        {"slug": "openrouter", "provider_id": "contabo-openrouter", "is_current": False,
         "models": ["google/gemini-3.8-flash"], "total_models": 400},
        {"slug": "gemini", "provider_id": "contabo-gemini", "is_current": False,
         "models": ["gemini-3.8-flash"], "total_models": 30},
    ]

def test_ready_provider_ranks_above_depleted_even_with_fewer_models():
    rows = _rows()
    health = parse_stats(STATS, readiness_floor=40)
    out = _apply_health_overlay(rows, health, {"readiness_floor": 40, "collapse_not_ready": False, "million_only": False})
    assert out[0]["slug"] == "gemini"        # ready+1M first, despite 30 < 400 models
    assert out[1]["slug"] == "openrouter"

def test_catalog_rows_are_never_dropped():
    rows = _rows()
    health = parse_stats(STATS)
    out = _apply_health_overlay(rows, health, {"readiness_floor": 40, "collapse_not_ready": True, "million_only": True})
    slugs = {r["slug"] for r in out}
    assert slugs == {"gemini", "openrouter"}  # depleted collapsed/annotated, NOT removed

def test_glyph_reflects_status():
    rows = _rows()
    health = parse_stats(STATS)
    out = _apply_health_overlay(rows, health, {"readiness_floor": 40, "collapse_not_ready": False, "million_only": False})
    by = {r["slug"]: r for r in out}
    assert by["gemini"]["readiness"] == "ready" and by["gemini"]["glyph"] == "●"
    assert by["openrouter"]["readiness"] == "depleted" and by["openrouter"]["glyph"] == "○"
