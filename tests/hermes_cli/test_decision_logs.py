from hermes_cli.error_classify import classify_failure  # new pure fn


def test_classify_maps_known_shapes():
    assert classify_failure(429, "none eligible for model ... depleted")[0] == "quota"
    assert classify_failure(429, "in cooldown/dead; vendor recovers in ~30s")[0] == "transient"
    assert classify_failure(401, "invalid access token or token expired")[0] == "cred_dead"
    assert classify_failure(400, "string too long ... maximum length 64")[0] == "format_error"


def test_classify_recommends_next_action():
    _, nxt = classify_failure(429, "depleted")
    assert "next_provider" in nxt or "cooldown" in nxt


def test_picker_build_decision_log_fires_and_never_raises(caplog):
    """_finalize_picker_rows emits a `picker_build` INFO on `hermes.decision` for a minimal
    row set, and the log wiring never raises (fail-safe)."""
    import logging
    from hermes_cli.model_switch_providers import _finalize_picker_rows

    rows = [{"slug": "nous", "is_current": True, "total_models": 3, "models": ["m1"]}]
    with caplog.at_level(logging.INFO, logger="hermes.decision"):
        out = _finalize_picker_rows(rows, {}, "")
    assert out is not None
    msgs = [r.getMessage() for r in caplog.records if r.name == "hermes.decision"]
    assert any(m.startswith("picker_build ") for m in msgs)

