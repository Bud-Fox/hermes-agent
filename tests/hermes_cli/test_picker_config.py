from hermes_cli.config_defaults import DEFAULT_CONFIG

def test_picker_defaults_present_and_typed():
    picker = DEFAULT_CONFIG["model"]["picker"]
    assert picker["health_aware"] is True
    assert isinstance(picker["readiness_floor"], int) and picker["readiness_floor"] >= 0
    assert picker["collapse_not_ready"] is True
    assert picker["million_only"] is False  # reversible off by default; opt-in

def test_picker_is_under_model_polymorphic_dict():
    # model default stays a mapping (deep-merge safe), picker is a sub-mapping
    assert isinstance(DEFAULT_CONFIG["model"], dict)
    assert isinstance(DEFAULT_CONFIG["model"]["picker"], dict)
