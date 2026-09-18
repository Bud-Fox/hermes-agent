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

def test_router_health_binds_by_vendor_path_not_bare_slug():
    """REGRESSION: router readiness must annotate the /vendor/<v>/ row, not a same-named DIRECT one.

    A direct provider (bare slug 'gemini', DIRECT googleapis URL, no /vendor/) must NOT borrow the
    router's 'gemini' pool health -> stays 'unknown' (no glyph). The router row for the same vendor
    (slug 'contabo-gemini', api_url .../vendor/gemini/v1) is the one that gets 'ready'/'●'.
    """
    rows = [
        {"slug": "gemini", "is_current": False,
         "api_url": "https://generativelanguage.googleapis.com/v1beta",
         "models": ["gemini-3.8-flash"], "total_models": 50},
        {"slug": "contabo-gemini", "is_current": False,
         "api_url": "http://194.34.232.59:8790/vendor/gemini/v1",
         "models": ["gemini-3.8-flash"], "total_models": 30},
    ]
    health = parse_stats(STATS, readiness_floor=40)
    out = _apply_health_overlay(rows, health, {"readiness_floor": 40, "collapse_not_ready": False, "million_only": False})
    by = {r["slug"]: r for r in out}
    assert by["gemini"]["readiness"] == "unknown" and by["gemini"]["glyph"] == "·"   # direct: no router health
    assert by["contabo-gemini"]["readiness"] == "ready" and by["contabo-gemini"]["glyph"] == "●"  # router-backed

def test_top_row_marks_first_ready_million_as_preferred():
    from hermes_cli.model_switch_providers import _mark_preferred_default
    rows = [{"slug": "gemini", "readiness": "ready", "has_million_ready": True,
             "ready_model_ids": ["gemini-3.8-flash"], "models": ["gemini-3.8-flash", "x-non-1m"]}]
    health = parse_stats(STATS)
    _mark_preferred_default(rows, health)
    assert rows[0]["preferred_model"] == "gemini-3.8-flash"

def test_preferred_marks_only_first_matching_row_and_stops():
    """GUARD: the is_million() reject filter AND first-match/early-return.

    Row A is ready + has_million_ready but its ready model is NON-1M -> must be SKIPPED;
    Row B is the first row carrying a real ready 1M model -> gets preferred_model;
    Row C is a second valid 1M match but must stay untouched (early return before C).
    """
    from hermes_cli.model_switch_providers import _mark_preferred_default
    stats = {
        "pools": {
            "vendora": [{"status": "active", "readiness_score": 80}],
            "vendorb": [{"status": "active", "readiness_score": 80}],
            "vendorc": [{"status": "active", "readiness_score": 80}],
        },
        "models": {
            "vendora": [{"id": "a-128k", "enabled": True, "context": 128000}],   # NON-1M
            "vendorb": [{"id": "b-1m", "enabled": True, "context": 1048576}],    # 1M
            "vendorc": [{"id": "c-1m", "enabled": True, "context": 1048576}],    # 1M
        },
    }
    health = parse_stats(stats)
    rows = [
        {"slug": "vendora", "readiness": "ready", "has_million_ready": True,
         "ready_model_ids": ["a-128k"]},
        {"slug": "vendorb", "readiness": "ready", "has_million_ready": True,
         "ready_model_ids": ["b-1m"]},
        {"slug": "vendorc", "readiness": "ready", "has_million_ready": True,
         "ready_model_ids": ["c-1m"]},
    ]
    _mark_preferred_default(rows, health)
    assert rows[0].get("preferred_model") is None      # is_million() rejected the 128k model
    assert rows[1]["preferred_model"] == "b-1m"         # first valid 1M match
    assert rows[2].get("preferred_model") is None       # early return stopped before row C

def test_preferred_is_non_destructive():
    """GUARD: never reorders/removes/adds rows, mutates only preferred_model, returns SAME list."""
    from hermes_cli.model_switch_providers import _mark_preferred_default
    stats = {
        "pools": {
            "vendora": [{"status": "active", "readiness_score": 80}],
            "vendorb": [{"status": "active", "readiness_score": 80}],
        },
        "models": {
            "vendora": [{"id": "a-1m", "enabled": True, "context": 1048576}],
            "vendorb": [{"id": "b-1m", "enabled": True, "context": 1048576}],
        },
    }
    health = parse_stats(stats)
    rows = [
        {"slug": "vendora", "readiness": "ready", "has_million_ready": True,
         "ready_model_ids": ["a-1m"], "current_model_sentinel": "keep-a"},
        {"slug": "vendorb", "readiness": "ready", "has_million_ready": True,
         "ready_model_ids": ["b-1m"], "current_model_sentinel": "keep-b"},
    ]
    before_len = len(rows)
    before_slugs = [r["slug"] for r in rows]
    out = _mark_preferred_default(rows, health)
    assert out is rows                                    # same list object, no copy
    assert len(rows) == before_len                        # nothing added/removed
    assert [r["slug"] for r in rows] == before_slugs      # no reorder
    assert rows[0]["current_model_sentinel"] == "keep-a"  # sentinel key untouched
    assert rows[1]["current_model_sentinel"] == "keep-b"

def test_preferred_no_match_leaves_all_rows_untouched():
    """GUARD: depleted rows / rows without a ready 1M model never receive preferred_model."""
    from hermes_cli.model_switch_providers import _mark_preferred_default
    stats = {
        "pools": {
            "vendora": [{"status": "depleted", "readiness_score": 0}],
            "vendorb": [{"status": "active", "readiness_score": 80}],
        },
        "models": {
            "vendora": [{"id": "a-1m", "enabled": True, "context": 1048576}],
            "vendorb": [{"id": "b-128k", "enabled": True, "context": 128000}],
        },
    }
    health = parse_stats(stats)
    rows = [
        {"slug": "vendora", "readiness": "depleted", "has_million_ready": False,
         "ready_model_ids": []},
        {"slug": "vendorb", "readiness": "ready", "has_million_ready": False,
         "ready_model_ids": []},
    ]
    _mark_preferred_default(rows, health)
    assert all("preferred_model" not in r for r in rows)

def test_preferred_health_none_no_crash():
    """Unit boundary: health=None must not raise (helper is total) and returns the same list."""
    from hermes_cli.model_switch_providers import _mark_preferred_default
    rows = [{"slug": "gemini", "readiness": "ready", "has_million_ready": True,
             "ready_model_ids": ["gemini-3.8-flash"]}]
    out = _mark_preferred_default(rows, None)
    assert out is rows
    assert rows[0].get("preferred_model") is None
