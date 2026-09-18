"""Regression: the OAuth codex resolution path must honour
``model.openai_runtime: codex_app_server`` and rewrite the api_mode to
``codex_app_server`` — same as the credential-pool path.

Root cause (observed 2026-09-18): a ChatGPT-account ``openai-codex`` provider has
no explicit api_key/base_url and no credential pool, so it resolves through
``_resolve_oauth_runtime`` (ladder rung 7). That path hardcoded ``codex_responses``
and never called ``_maybe_apply_codex_app_server_runtime``, so ``chatgpt-web/*``
requests went to https://chatgpt.com/backend-api/codex (HTTP 400 "not supported
when using Codex with a ChatGPT account") instead of the local Native2 app-server
runtime. Only the pool path (``_resolve_runtime_from_pool_entry``) applied the
rewrite.
"""

from __future__ import annotations

from unittest.mock import patch

import hermes_cli.runtime_provider as rp


_CREDS = {
    "api_key": "tok",
    "base_url": "",
    "source": "hermes-auth-store",
    "last_refresh": 0,
}


def _resolve(model_cfg):
    with patch.object(rp, "resolve_codex_runtime_credentials", return_value=dict(_CREDS)):
        return rp._resolve_oauth_runtime(
            "openai-codex", "openai-codex", model_cfg, "chatgpt-web/light"
        )


def test_oauth_codex_honours_app_server_runtime():
    """openai_runtime=codex_app_server on the model config rewrites the api_mode."""
    rt = _resolve(
        {"default": "chatgpt-web/light", "provider": "openai-codex",
         "openai_runtime": "codex_app_server"}
    )
    assert rt["api_mode"] == "codex_app_server"


def test_oauth_codex_defaults_to_codex_responses():
    """Without the opt-in, the OAuth path keeps the historical codex_responses mode."""
    rt = _resolve({"default": "gpt-5.6-high", "provider": "openai-codex"})
    assert rt["api_mode"] == "codex_responses"


def test_oauth_codex_auto_runtime_is_not_rewritten():
    """openai_runtime='auto' (or empty) must not force the app-server runtime."""
    rt = _resolve(
        {"default": "gpt-5.6-high", "provider": "openai-codex", "openai_runtime": "auto"}
    )
    assert rt["api_mode"] == "codex_responses"
