"""Offline route ownership, preflight, discovery and notification contracts."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools import delegate_tool as delegate
from tools import delegate_tool_config as config
from tools import delegate_tool_dispatch as dispatch


@pytest.fixture
def resolver(monkeypatch):
    resolve = Mock(side_effect=lambda cfg, parent: {**cfg, "base_url": "https://test.invalid", "api_key": "test-only", "api_mode": "chat_completions"})
    monkeypatch.setattr(config, "_resolve_delegation_credentials", resolve)
    monkeypatch.setattr(config, "_load_routes_allowlist", lambda: {})
    return resolve


def test_route_uses_supplied_config_not_ambient_profile(resolver):
    creds, err = config._resolve_task_route(
        {"provider": "zai-coding-plan", "model": "glm-5.3"},
        {"routes": {"zai-coding-plan": {"models": ["glm-5.3"]}}}, None)
    assert err is None
    assert creds["model"] == "glm-5.3"
    assert resolver.call_args.args[0]["provider"] == "zai-coding-plan"


@pytest.mark.parametrize("models", [None, [], "glm-5.3", [None], ["glm-5.3", 42], [""]])
def test_missing_empty_malformed_models_fail_closed(monkeypatch, resolver, models):
    routes = {"zai-coding-plan": {"models": models}}
    monkeypatch.setattr(config, "_load_routes_allowlist", lambda: routes)
    creds, err = config._resolve_task_route(
        {"provider": "zai-coding-plan", "model": "glm-5.3"}, {"routes": routes}, None)
    assert creds is None and err
    resolver.assert_not_called()


def test_literal_ids_are_not_normalized(monkeypatch, resolver):
    routes = {"Literal": {"models": ["Exact"]}}
    monkeypatch.setattr(config, "_load_routes_allowlist", lambda: routes)
    creds, err = config._resolve_task_route(
        {"provider": "Literal ", "model": "Exact"}, {"routes": routes}, None)
    assert creds is None and err
    resolver.assert_not_called()


def test_unpaired_route_is_rejected(resolver):
    assert config._resolve_task_route({"provider": "p"}, {"routes": {"p": {"models": ["m"]}}}, None)[1]
    resolver.assert_not_called()


def test_unpinned_route_inherits_parent(resolver):
    assert config._resolve_task_route({"goal": "work"}, {"routes": {}}, None) == ({}, None)
    resolver.assert_not_called()


def test_dynamic_schema_lists_exact_pairs_without_mutating_static(monkeypatch):
    routes = {"zai-coding-plan": {"models": ["glm-5.3"]}, "Literal": {"models": ["Vendor/Exact"]},
              "bad": {"models": []}}
    monkeypatch.setattr(delegate, "_load_config", lambda: {"routes": routes})
    before = json.dumps(delegate.DELEGATE_TASK_SCHEMA, sort_keys=True)
    schema = delegate._build_dynamic_schema_overrides()
    text = schema["parameters"]["properties"]["tasks"]["description"]
    assert '"provider": "zai-coding-plan", "model": "glm-5.3"' in text
    assert '"provider": "Literal", "model": "Vendor/Exact"' in text
    assert '"provider": "bad"' not in text
    assert "preferred" in text and "not mandatory" in text
    assert "readiness" in text
    assert json.dumps(delegate.DELEGATE_TASK_SCHEMA, sort_keys=True) == before


def build(monkeypatch, route, factory):
    monkeypatch.setattr(delegate, "_resolve_task_route", route)
    monkeypatch.setattr(delegate, "_build_child_preserving_parent_tools", factory)
    return delegate._build_children(
        [{"goal": "one"}, {"goal": "two"}], [],
        {"model": "default", "provider": None, "base_url": None, "api_key": None, "api_mode": None},
        top_role="worker", max_iterations=1, parent_agent=SimpleNamespace(), routing_cfg={},
        live_deleg_id=None, live_writers=[])


def test_all_routes_preflight_before_any_child_construction(monkeypatch):
    factory = Mock()
    children, err = build(monkeypatch, Mock(side_effect=[({}, None), (None, "denied")]), factory)
    assert children == [] and err == "denied"
    factory.assert_not_called()


@pytest.mark.parametrize("failure", [ValueError("construction denied"), RuntimeError("construction denied")])
def test_constructed_children_closed_on_later_construction_failure(monkeypatch, failure):
    child = SimpleNamespace(close=Mock())
    factory = Mock(side_effect=[child, failure])
    children, err = build(monkeypatch, Mock(return_value=({}, None)), factory)
    assert children == [] and err == "construction denied"
    child.close.assert_called_once()


def unit(children):
    return dispatch._Batch(task_list=[{"goal": "one"}, {"goal": "two"}], children=children,
        parent_agent=None, creds={"model": "wrong-default"}, context=None, top_role="worker", max_children=2,
        live_deleg_id=None, live_writers=[], live_paths=[], origin_wake_sid="", origin_ui_session_id="",
        origin_owner_transport=None, origin_owner_session_record=None, origin_session_history_delivery=False,
        overall_start=0)


def test_async_metadata_uses_actual_children_and_canonical_identity(monkeypatch):
    import tools.async_delegation as async_module
    capture = Mock(return_value={"status": "dispatched"})
    monkeypatch.setattr(async_module, "dispatch_async_delegation_batch", capture)
    batch = unit([(0, {"goal": "one"}, SimpleNamespace(model="glm-5.3", provider="custom", requested_provider="zai-coding-plan")),
                  (1, {"goal": "two"}, SimpleNamespace(model="other-model", provider="native", requested_provider="native"))])
    dispatch._dispatch_unit(batch, "test-unit", None, {})
    label = capture.call_args.kwargs["model"]
    assert "wrong-default" not in label
    assert all(value in label for value in ("glm-5.3", "other-model", "zai-coding-plan", "custom"))
    payload = dispatch._dispatched_payload(batch, [(batch, "test-unit")])
    assert payload["routes"] == [
        {"task_index": 0, "provider": "custom", "requested_provider": "zai-coding-plan", "model": "glm-5.3"},
        {"task_index": 1, "provider": "native", "requested_provider": "native", "model": "other-model"}]


@pytest.mark.parametrize("key", ["no-key-required", ""])
def test_custom_declared_key_env_placeholder_fails_before_child(monkeypatch, key):
    import hermes_cli.runtime_provider as runtime
    import hermes_cli.config as cli_config
    monkeypatch.setattr(cli_config, "load_config_readonly", lambda: {
        "providers": {"zai-coding-plan": {"key_env": "TEST_REQUIRED_KEY"}}})
    monkeypatch.setattr(runtime, "resolve_runtime_provider", lambda **kw: {
        "provider": "custom", "api_key": key, "base_url": "https://test.invalid"})
    with pytest.raises(ValueError, match="zai-coding-plan.*TEST_REQUIRED_KEY"):
        config._resolve_delegation_credentials({"provider": "zai-coding-plan", "model": "glm-5.3"}, None)


def test_keyless_custom_without_declared_key_env_stays_compatible(monkeypatch):
    import hermes_cli.runtime_provider as runtime
    import hermes_cli.config as cli_config
    monkeypatch.setattr(cli_config, "load_config_readonly", lambda: {"providers": {"local": {}}})
    monkeypatch.setattr(runtime, "resolve_runtime_provider", lambda **kw: {
        "provider": "custom", "api_key": "no-key-required", "base_url": "http://localhost:1234"})
    assert config._resolve_delegation_credentials({"provider": "local", "model": "m"}, None)["provider"] == "local"


@pytest.mark.parametrize("key", ["offline-test-key", lambda: "offline-test-key"])
def test_custom_declared_key_env_accepts_resolver_credentials(monkeypatch, key):
    import hermes_cli.runtime_provider as runtime
    import hermes_cli.config as cli_config
    monkeypatch.setattr(cli_config, "load_config_readonly", lambda: {
        "providers": {"zai-coding-plan": {"key_env": "TEST_REQUIRED_KEY"}}})
    monkeypatch.setattr(runtime, "resolve_runtime_provider", lambda **kw: {
        "provider": "custom", "api_key": key, "base_url": "https://test.invalid"})
    creds = config._resolve_delegation_credentials({"provider": "zai-coding-plan", "model": "glm-5.3"}, None)
    assert creds["provider"] == "zai-coding-plan"
    assert creds["api_key"] is key


def test_declared_key_env_check_does_not_apply_to_native_auth(monkeypatch):
    import hermes_cli.runtime_provider as runtime
    import hermes_cli.config as cli_config
    lookup = Mock(side_effect=AssertionError("custom key_env lookup on native auth"))
    monkeypatch.setattr(cli_config, "load_config_readonly", lookup)
    monkeypatch.setattr(runtime, "resolve_runtime_provider", lambda **kw: {
        "provider": "openai-codex", "api_key": "native-test-token", "base_url": "https://test.invalid"})
    assert config._resolve_delegation_credentials({"provider": "openai-codex", "model": "m"}, None)["provider"] == "openai-codex"
    lookup.assert_not_called()


def test_runtime_failure_is_preflight_error_not_route_substitution(monkeypatch):
    monkeypatch.setattr(config, "_resolve_delegation_credentials", Mock(side_effect=ValueError("credentials unavailable")))
    creds, err = config._resolve_task_route(
        {"provider": "zai-coding-plan", "model": "glm-5.3"},
        {"routes": {"zai-coding-plan": {"models": ["glm-5.3"]}}}, None)
    assert creds is None
    assert "zai-coding-plan/glm-5.3" in err and "credentials unavailable" in err


def test_per_task_overrides_reach_constructor_without_default_pins(monkeypatch):
    route = {"model": "glm-5.3", "provider": "zai-coding-plan", "base_url": "https://test.invalid",
             "api_key": "offline-test-key", "api_mode": "chat_completions"}
    factory = Mock(side_effect=[SimpleNamespace(), SimpleNamespace()])
    children, err = build(monkeypatch, Mock(side_effect=[(route, None), ({}, None)]), factory)
    assert err is None and len(children) == 2
    first, second = factory.call_args_list
    assert first.kwargs["model"] == "glm-5.3"
    assert first.kwargs["override_provider"] == "zai-coding-plan"
    assert first.kwargs["override_base_url"] == "https://test.invalid"
    assert second.kwargs["model"] == "default"


def test_single_child_async_metadata_uses_inherited_actual_route(monkeypatch):
    import tools.async_delegation as async_module
    capture = Mock(return_value={"status": "dispatched"})
    monkeypatch.setattr(async_module, "dispatch_async_delegation_batch", capture)
    batch = unit([(0, {"goal": "one"}, SimpleNamespace(model="parent-model", provider="custom", requested_provider="parent-canonical"))])
    dispatch._dispatch_unit(batch, "unit", None, {})
    assert capture.call_args.kwargs["model"] == "parent-canonical/parent-model (runtime=custom)"
