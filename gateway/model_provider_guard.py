"""Fail-fast guard: the turn's model id must be served by the turn's provider.

Incident shape (2026-11-09 06:01 AWST, picker-bot session 20261106_163311_2e690f): the GUI
model switcher sent model id ``claude-opus-5-5`` (catalogued only under the expired
``anthropic-promo`` entry) while the session's runtime provider stayed ``custom`` → Z.ai
GLM endpoint. Z.ai answered HTTP 400 code 1211 "Unknown Model, please check the model code"
and the turn died with a vendor error that names no fix.

This guard runs AFTER model/runtime resolution and BEFORE any agent is built: if the model
is catalogued under OTHER configured providers but not under the runtime provider's own
catalog, the turn fails immediately with an actionable message and zero upstream spend.

Models unknown to every provider catalog are NOT blocked (dynamic discovery / openrouter
pools are legitimate) — the guard only rejects the provable cross-provider mismatch.
"""

from __future__ import annotations

from typing import Any


def assert_model_served_by_runtime_provider(model: Any, runtime_kwargs: dict) -> None:
    from hermes_cli.auth import AuthError
    from hermes_cli.config import load_config

    model_l = str(model or "").strip().lower()
    if not model_l:
        return

    cfg = load_config()
    providers = cfg.get("providers") or {}
    if not isinstance(providers, dict) or not providers:
        return

    rid = str(runtime_kwargs.get("requested_provider")
              or runtime_kwargs.get("provider") or "").strip().lower()
    base = str(runtime_kwargs.get("base_url") or "").strip().rstrip("/")
    if rid in ("", "custom", "openrouter") and base:
        for pid, pc in providers.items():
            pc = pc or {}
            purls = {str(pc.get(k) or "").strip().rstrip("/")
                     for k in ("base_url", "url", "api") if pc.get(k)}
            if base in purls:
                rid = str(pid).strip().lower()
                break
    if not rid:
        return  # no resolvable runtime provider identity — nothing to check against

    serving: list[str] = []
    others: list[str] = []
    for pid, pc in providers.items():
        pc = pc or {}
        models = [str(m).strip().lower() for m in (pc.get("models") or [])]
        if model_l not in models:
            continue
        if str(pid).strip().lower() == rid:
            serving.append(str(pid))
        else:
            others.append(str(pid))

    if serving or not others:
        # Served by this provider, or catalogued nowhere (dynamic/discovery) — do not guess.
        return

    names = ", ".join(sorted(set(others)))
    raise AuthError(
        f"Model '{model}' is served by provider(s) {names}, not by this turn's provider "
        f"'{rid}'. Switch the provider together with the model (e.g. /model {model} "
        f"--provider {min(set(others))}), or pick a model from the current provider's "
        f"catalog ('hermes model' lists both).",
        code="model_provider_mismatch",
    )
