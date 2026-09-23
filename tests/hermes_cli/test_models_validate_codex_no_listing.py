"""Codex api_modes without a /models listing must not hard-reject (`/model` brick).

Repro (proven 2026-09-23): provider `custom` resolved to base_url
https://chatgpt.com/backend-api/codex with api_mode `codex_responses` — the browser
backend answers GET /models with 401 (the endpoint does not exist for machine
clients), probe_api_models returns models=None, and `_validate_custom` hard-rejected
with "`{model}` was not saved; the endpoint should expose `/models`". The curator
path for provider='openai-codex' accepts (recognized=True); only the custom branch
with a resolved OAuth base_url fell through. Same for `codex_app_server`.

Rule: keyed on api_mode (codex family), NOT on the chatgpt.com host — no special
casing by base_url.
"""

from unittest.mock import patch

import pytest

from hermes_cli.models_validate import validate_requested_model


def _probe(models):
    return {
        "models": models,
        "probed_url": "https://chatgpt.com/backend-api/codex/models",
        "resolved_base_url": "https://chatgpt.com/backend-api/codex",
        "suggested_base_url": None,
        "used_fallback": False,
    }


def _validate(model, api_mode, models, **kw):
    with patch("hermes_cli.models.probe_api_models", return_value=_probe(models)):
        return validate_requested_model(
            model,
            "custom",
            api_key="k",
            base_url="https://chatgpt.com/backend-api/codex",
            api_mode=api_mode,
            **kw,
        )


@pytest.mark.parametrize("api_mode", ["codex_responses", "codex_app_server"])
def test_codex_mode_without_listing_soft_accepts(api_mode):
    """models=None (GET /models 401s on the Codex backend) → accept + persist with an
    'accepted without verification' note, never a 'was not saved' hard-reject."""
    result = _validate("gpt-4.1-test", api_mode, models=None)
    assert (result["accepted"], result["persist"], result["recognized"]) == (True, True, False)
    assert "accepted without verification" in result["message"]
    assert "was not saved" not in result["message"]


@pytest.mark.parametrize("api_mode", ["codex_responses", "codex_app_server"])
def test_codex_mode_with_listing_still_uses_listing(api_mode):
    """Regression: a REACHABLE listing stays authoritative — a model absent from it
    soft-accepts with the 'not found in listing' note and a suggestion, i.e. the
    probe result is still consumed when available."""
    result = _validate("gpt-4.1-test", api_mode, models=["gpt-5.5", "gpt-5.5-codex"])
    assert (result["accepted"], result["persist"], result["recognized"]) == (True, True, False)
    assert "was not found in this custom endpoint's model listing" in result["message"]
    assert "`gpt-5.5`" in result["message"]  # suggestion from the reachable listing
    assert "accepted without verification" not in result["message"]


def test_unknown_api_mode_without_listing_still_rejects():
    """Invariant: the soft-accept is scoped to the codex family — an unrecognized
    api_mode with no listing keeps the hard-reject (no widening beyond codex)."""
    result = _validate("mystery-model", "some_future_mode", models=None)
    assert result["accepted"] is False
    assert "was not saved" in result["message"]


def test_codex_note_mentions_runtime_verification():
    """The unverified-accept note for codex modes points at runtime verification
    (native codex client / app-server), not a generic chat-completions caveat."""
    result = _validate("gpt-4.1-test", "codex_responses", models=None)
    assert "accepted without verification" in result["message"]
    assert "codex" in result["message"].lower()
