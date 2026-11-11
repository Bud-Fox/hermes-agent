"""Stale-session-route guard for the TUI/Desktop path (T-092 follow-up).

Incident 2026-11-11: a desktop session resumed with a persisted runtime whose
base_url pointed at api.reflection.ai while the model was glm-5.3-flash
(catalogued only under zai-coding-plan). base_url won the identity recovery,
the stale endpoint was applied as an override, and the request died with a
vendor 404 model_not_found. The messaging-gateway path already runs
``assert_model_served_by_runtime_provider`` (gateway/run_turn_runner.py); these
tests pin the guard itself and its wiring into ``tui_gateway.agent_factory``.
"""

import pytest

from hermes_cli.auth import AuthError

PROVIDERS = {
    "reflection-beam": {
        "api": "https://api.reflection.ai/openai/v1",
        "models": ["Beam-1.0-501B-A23B"],
    },
    "zai-coding-plan": {
        "base_url": "https://api.z.ai/api/coding/paas/v4",
        "models": ["glm-5.3", "glm-5.3-flash"],
    },
}


def test_guard_rejects_cross_provider_pair():
    """glm-5.3-flash is catalogued only under zai-coding-plan; a reflection
    base_url must fail fast with code=model_provider_mismatch, pre-API."""
    from gateway.model_provider_guard import assert_model_served_by_runtime_provider

    with pytest.raises(AuthError) as ei:
        assert_model_served_by_runtime_provider(
            "glm-5.3-flash",
            {"provider": "custom", "base_url": "https://api.reflection.ai/openai/v1"},
            cfg={"providers": PROVIDERS},
        )
    assert ei.value.code == "model_provider_mismatch"
    assert "zai-coding-plan" in str(ei.value)


def test_guard_passes_matching_pair():
    from gateway.model_provider_guard import assert_model_served_by_runtime_provider

    assert_model_served_by_runtime_provider(
        "glm-5.3-flash",
        {"provider": "custom", "base_url": "https://api.z.ai/api/coding/paas/v4"},
        cfg={"providers": PROVIDERS},
    )


def test_guard_passes_unknown_model():
    """Models catalogued nowhere are legitimate (dynamic discovery) — not blocked."""
    from gateway.model_provider_guard import assert_model_served_by_runtime_provider

    assert_model_served_by_runtime_provider(
        "totally-unknown-model",
        {"provider": "custom", "base_url": "https://api.reflection.ai/openai/v1"},
        cfg={"providers": PROVIDERS},
    )


def test_tui_resume_stale_route_raises(monkeypatch):
    """TUI seam: a resumed session's stale (reflection) base_url with a
    zai-only model must raise AuthError from _resolve_agent_model_runtime
    instead of resolving to the stale endpoint."""
    from tui_gateway import agent_factory
    from tui_gateway.server import _RuntimeFallbackResolution

    # Seed the providers catalog in the isolated profile home: the guard resolves cfg via
    # load_config() (agent_factory helpers are rebound into server.py's namespace, so they
    # cannot be monkeypatched through agent_factory reliably).
    import yaml
    from hermes_constants import get_hermes_home
    (get_hermes_home() / "config.yaml").write_text(yaml.safe_dump({"providers": PROVIDERS}))

    stale_runtime = {
        "provider": "custom",
        "base_url": "https://api.reflection.ai/openai/v1",
    }
    monkeypatch.setattr(
        agent_factory,
        "_resolve_runtime_with_fallback",
        lambda kwargs: _RuntimeFallbackResolution(dict(stale_runtime), None, False),
    )

    with pytest.raises(AuthError) as ei:
        agent_factory._resolve_agent_model_runtime(
            {"model": "glm-5.3-flash", "provider": "custom",
             "base_url": "https://api.reflection.ai/openai/v1"},
            None,
        )
    assert ei.value.code == "model_provider_mismatch"
    assert "zai-coding-plan" in str(ei.value)


# ── T-094c: a HEALED 'custom:<slug>' identity must not false-positive the guard ──


def test_guard_passes_healed_custom_slug_identity():
    """T-094b's heal emits 'custom:<slug>' identities (e.g. custom:zai-coding-plan).
    The guard must strip the 'custom:' prefix before matching provider keys —
    the healed (CORRECT) pair must pass the turn, not raise (E2E regression
    found 2026-11-11 ~15:10 AWST)."""
    from gateway.model_provider_guard import assert_model_served_by_runtime_provider

    assert_model_served_by_runtime_provider(
        "glm-5.3-flash",
        {"provider": "custom:zai-coding-plan", "base_url": None},
        cfg={"providers": PROVIDERS},
    )


def test_tui_resume_healed_row_resolves(monkeypatch):
    """Integration: a poisoned row shaped like the incident, healed by
    _stored_session_runtime_overrides into custom:zai-coding-plan, must resolve
    cleanly through _resolve_agent_model_runtime."""
    from tui_gateway import agent_factory
    from tui_gateway import server as srv
    from tui_gateway.server import _RuntimeFallbackResolution

    import yaml
    from hermes_constants import get_hermes_home
    (get_hermes_home() / "config.yaml").write_text(yaml.safe_dump({"providers": PROVIDERS}))

    monkeypatch.setattr(
        agent_factory,
        "_resolve_runtime_with_fallback",
        lambda kwargs: _RuntimeFallbackResolution(
            {"provider": "custom:zai-coding-plan", "base_url": None}, None, False),
    )
    # Heal path first (real function, no mocking): proves the pair survives heal -> guard.
    overrides = srv._stored_session_runtime_overrides({
        "model": "glm-5.3-flash",
        "model_config": '{"gateway_runtime": {"provider": "custom", "base_url": '
                        '"https://api.reflection.ai/openai/v1", "api_mode": "chat_completions", '
                        '"fallback_active": false}, "model": "glm-5.3-flash", '
                        '"provider": "zai-coding-plan", "base_url": '
                        '"https://api.z.ai/api/coding/paas/v4", "api_mode": "chat_completions"}',
        "billing_provider": None,
    })["model_override"]
    assert overrides["provider"] == "custom:zai-coding-plan"
    resolved_model, resolved_runtime = agent_factory._resolve_agent_model_runtime(overrides, None)
    assert resolved_model == "glm-5.3-flash"
    assert resolved_runtime["provider"] == "custom:zai-coding-plan"
