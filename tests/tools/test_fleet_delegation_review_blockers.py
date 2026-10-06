"""Independent-review regressions: policy boundaries and failing-child ownership."""
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import run_agent  # bootstrap before the per-test real-home I/O guard
from pathlib import Path

from hermes_cli import fleet_catalog as fleet
from tools import delegate_tool as delegate
from tools import delegate_tool_config as config


@pytest.mark.parametrize("declared", [False, True])
def test_shared_policy_filters_inherited_and_declared_fallback_exact_pairs(monkeypatch, declared):
    catalog = fleet.FleetCatalog(1, {
        "allowed": {"models": ["Exact"]}, "anthropic": {"models": ["claude"]}},
        Path("catalog.shared.yaml"), {"excluded_providers": ["anthropic"]})
    monkeypatch.setattr(fleet, "load_fleet_catalog", lambda: catalog)
    chain = [{"provider": "anthropic", "model": "claude"},
             {"provider": "allowed", "model": "wrong"},
             {"provider": "allowed", "model": "exact"},
             {"provider": "allowed ", "model": "Exact"},
             {"provider": "allowed", "model": "Exact", "api_key": "offline"}]
    cfg = fleet.apply_fleet_catalog({"delegation": {"fallback_providers": chain} if declared else {}})["delegation"]
    assert cfg.get("_shared_policy") is True
    parent = SimpleNamespace(_fallback_chain=chain)
    assert config._resolve_child_fallback_chain(parent, cfg, pinned=False) == [chain[-1]]


def test_explicit_task_route_disables_even_declared_fallback(monkeypatch):
    route = {"model": "m", "provider": "allowed", "base_url": "https://test.invalid",
             "api_key": "offline", "api_mode": "chat_completions"}
    monkeypatch.setattr(delegate, "_resolve_task_route", lambda *a: (route, None))
    capture = Mock(return_value=SimpleNamespace())
    monkeypatch.setattr(delegate, "_build_child_preserving_parent_tools", capture)
    cfg = {"fallback_providers": [{"provider": "substitute", "model": "reviewer"}]}
    _, err = delegate._build_children([{ "goal": "review", "provider": "allowed", "model": "m"}], [],
        route, top_role="leaf", max_iterations=1, parent_agent=None, routing_cfg=cfg,
        live_deleg_id=None, live_writers=[])
    assert err is None
    assert capture.call_args.kwargs["routing_cfg"]["fallback_providers"] == []
    assert cfg["fallback_providers"] != []


def invalid_authority(monkeypatch):
    import hermes_cli.config as cli_config
    monkeypatch.delenv("HERMES_IGNORE_USER_CONFIG", raising=False)
    monkeypatch.setattr(cli_config, "load_config_readonly", Mock(side_effect=ValueError("invalid fleet policy")))
    monkeypatch.setitem(sys.modules, "cli", SimpleNamespace(CLI_CONFIG={"delegation": {"provider": "anthropic"}}))


def test_authoritative_config_value_error_never_uses_legacy_pin(monkeypatch):
    invalid_authority(monkeypatch)
    with pytest.raises(ValueError, match="invalid fleet policy"):
        config._load_config()


def test_authoritative_config_value_error_is_spawn_error(monkeypatch):
    invalid_authority(monkeypatch)
    resolve = Mock(side_effect=AssertionError("must not resolve legacy pin"))
    monkeypatch.setattr(delegate, "_resolve_delegation_credentials", resolve)
    result = json.loads(delegate.delegate_task(tasks=[{"goal": "review"}], parent_agent=SimpleNamespace(_delegate_depth=0)))
    assert "invalid fleet policy" in result["error"]
    resolve.assert_not_called()


@pytest.mark.parametrize("provider,model", [("allowed ", "m "), ("allowed", "m\t"), ("al lowed", "m")])
def test_whitespace_ids_rejected_even_if_allowlisted(monkeypatch, provider, model):
    resolve = Mock()
    monkeypatch.setattr(config, "_resolve_delegation_credentials", resolve)
    creds, err = config._resolve_task_route({"provider": provider, "model": model},
        {"routes": {provider: {"models": [model]}}}, None)
    assert creds is None and err
    resolve.assert_not_called()


@pytest.mark.parametrize("task", [{"model": [], "provider": False}, {"model": []}, {"provider": False},
                                 {"model": "", "provider": ""}, {"model": None, "provider": None}])
def test_supplied_falsey_ids_are_not_inheritance(monkeypatch, task):
    resolve = Mock()
    monkeypatch.setattr(config, "_resolve_delegation_credentials", resolve)
    creds, err = config._resolve_task_route(task, {}, None)
    assert creds is None and err
    resolve.assert_not_called()


@pytest.fixture
def child_setup(monkeypatch):
    import run_agent
    from tests.tools.test_delegate import _make_mock_parent
    parent = _make_mock_parent(depth=0)
    parent._active_children = []
    db = SimpleNamespace(close=Mock())
    child = SimpleNamespace(close=Mock(), session_id="child")
    monkeypatch.setattr(delegate, "_get_max_spawn_depth", lambda: 1)
    monkeypatch.setattr(delegate, "_get_orchestrator_enabled", lambda: True)
    monkeypatch.setattr(delegate, "_load_config", lambda: {})
    monkeypatch.setattr(delegate, "_resolve_child_toolsets", lambda *a: ([], []))
    monkeypatch.setattr(delegate, "_open_child_session_db", lambda p: db)
    monkeypatch.setattr(run_agent, "AIAgent", Mock(return_value=child))
    return parent, child, db


def spawn(parent):
    return delegate._build_child_agent(task_index=0, goal="review", context=None, toolsets=None,
        model=None, max_iterations=1, task_count=1, parent_agent=parent)


@pytest.mark.parametrize("phase", ["cache", "compression", "attached"])
def test_post_constructor_failure_closes_current_child_with_db_ownership(monkeypatch, child_setup, phase):
    parent, child, db = child_setup
    def fail(*a, **kw):
        raise OverflowError("post-constructor setup failed")
    if phase == "cache":
        monkeypatch.setattr(delegate, "_apply_child_cache_ttl", fail)
    elif phase == "compression":
        monkeypatch.setattr(delegate, "_apply_child_compression_cap", fail)
    else:
        def attach_then_fail(p, c):
            p._active_children.append(c)
            fail()
        monkeypatch.setattr(delegate, "_attach_child", attach_then_fail)
    owned = []
    child.close.side_effect = lambda: owned.append(getattr(child, "_owns_session_db", False))
    with pytest.raises(OverflowError, match="post-constructor setup"):
        spawn(parent)
    child.close.assert_called_once()
    assert owned == [True]
    assert child not in parent._active_children


@pytest.mark.parametrize("raw", [float("inf"), float("-inf"), float("nan")])
def test_nonfinite_compression_cap_is_ignored(raw):
    assert delegate._child_compression_cap_tokens(raw) is None


@pytest.mark.parametrize("direct", [False, True])
def test_explicit_named_custom_runtime_preserves_requested_owner(monkeypatch, child_setup, direct):
    import hermes_cli.runtime_provider as runtime
    parent, child, db = child_setup
    monkeypatch.setattr(runtime, "resolve_runtime_provider", lambda **kw: {
        "provider": "custom", "api_key": "offline", "base_url": "https://test.invalid"})
    import hermes_cli.config as cli_config
    monkeypatch.setattr(cli_config, "load_config_readonly", lambda: {"providers": {"zai-coding-plan": {}}})
    route_cfg = {"provider": "zai-coding-plan", "model": "glm-5.3"}
    if direct:
        route_cfg.update(base_url="https://test.invalid", api_key="offline")
    creds = config._resolve_delegation_credentials(route_cfg, parent)
    capture = Mock(return_value=child)
    import run_agent
    monkeypatch.setattr(run_agent, "AIAgent", capture)
    monkeypatch.setattr(delegate, "_resolve_task_route", lambda *a: (creds, None))
    _, err = delegate._build_children([{"goal": "review", "provider": "zai-coding-plan", "model": "glm-5.3"}], [],
        creds, top_role="leaf", max_iterations=1, parent_agent=parent,
        routing_cfg={"fallback_providers": [{"provider": "substitute", "model": "reviewer"}]},
        live_deleg_id=None, live_writers=[])
    assert err is None
    assert capture.call_args.kwargs["fallback_model"] is None
    assert capture.call_args.kwargs["provider"] == "custom"
    assert capture.call_args.kwargs["requested_provider"] == "zai-coding-plan"
