"""Configured models extend built-in picker rows."""

from unittest.mock import patch

import hermes_cli.providers as providers_mod
from hermes_cli.model_switch import list_authenticated_providers, switch_model


def _provider_row(configured_models, *, max_models=None):
    with (
        patch(
            "agent.models_dev.fetch_models_dev",
            return_value={"deepseek": {"env": ["DEEPSEEK_API_KEY"], "name": "DeepSeek"}},
        ),
        patch(
            "agent.models_dev.PROVIDER_TO_MODELS_DEV",
            {"deepseek": "deepseek"},
        ),
        patch(
            "hermes_cli.models.cached_provider_model_ids",
            return_value=["live-a", "shared"],
        ),
        patch("hermes_cli.providers.HERMES_OVERLAYS", {}),
        patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}),
    ):
        rows = list_authenticated_providers(
            current_provider="deepseek",
            user_providers={"deepseek": {"models": configured_models}},
            max_models=max_models,
        )
    return next(row for row in rows if row["slug"] == "deepseek")


def test_configured_models_precede_and_deduplicate_discovered_models():
    row = _provider_row({"configured-x": {}, "shared": {}})

    assert row["models"] == ["configured-x", "shared", "live-a"]
    assert row["total_models"] == 3


def test_configured_models_extend_the_openai_codex_oauth_overlay(monkeypatch):
    """Bridge routes declared for native Codex stay visible beside OAuth discovery."""
    monkeypatch.setattr("agent.models_dev.fetch_models_dev", lambda: {})
    monkeypatch.setattr("agent.models_dev.PROVIDER_TO_MODELS_DEV", {})
    monkeypatch.setattr(
        "hermes_cli.providers.HERMES_OVERLAYS",
        {"openai-codex": providers_mod.HERMES_OVERLAYS["openai-codex"]},
    )
    monkeypatch.setattr(
        "hermes_cli.model_switch_providers._overlay_has_creds",
        lambda *_args: True,
    )
    monkeypatch.setattr(
        "hermes_cli.models.cached_provider_model_ids",
        lambda *_args, **_kwargs: ["gpt-5.6-sol", "gpt-5.6-luna"],
    )

    rows = list_authenticated_providers(
        current_provider="openai-codex",
        user_providers={
            "openai-codex": {
                "models": ["chatgpt-web/high", "chatgpt-web/medium", "gpt-5.6-sol"],
            }
        },
    )

    row = next(item for item in rows if item["slug"] == "openai-codex")
    assert row["models"] == [
        "chatgpt-web/high",
        "chatgpt-web/medium",
        "gpt-5.6-sol",
        "gpt-5.6-luna",
    ]


def test_configured_catalog_extension_keeps_openai_codex_native_validation(monkeypatch):
    """A models-only overlay must not turn the built-in OAuth route into a custom endpoint."""
    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        lambda **_kwargs: {
            "provider": "openai-codex",
            "api_key": "oauth-token",
            "base_url": "https://chatgpt.com/backend-api/codex",
            "api_mode": "codex_responses",
        },
    )
    monkeypatch.setattr(
        "hermes_cli.models.probe_api_models",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("built-in openai-codex must not probe /models")
        ),
    )
    monkeypatch.setattr("hermes_cli.model_switch.get_model_info", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("hermes_cli.model_switch.get_model_capabilities", lambda *_args, **_kwargs: None)

    result = switch_model(
        raw_input="gpt-5.6-luna",
        current_provider="contabo-gemini",
        current_model="gemini-3.8-flash",
        current_base_url="https://proxy.example/v1",
        explicit_provider="openai-codex",
        user_providers={
            "openai-codex": {
                "name": "OpenAI Codex",
                "models": ["chatgpt-web/light", "chatgpt-web/medium", "chatgpt-web/high"],
            }
        },
    )

    assert result.success is True, result.error_message
    assert result.target_provider == "openai-codex"
    assert result.new_model == "gpt-5.6-luna"
