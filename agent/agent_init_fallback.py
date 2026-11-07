"""Init-time fallback chain: entry normalization, chain setup, and the fallback's api_mode."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger("run_agent")


def _fallback_entries(fallback_model) -> list[dict[str, Any]]:
    """Normalize legacy single-dict ``fallback_model`` / list ``fallback_providers``.

    Entries run through the SAME lane normalizer as ``get_fallback_chain`` (the canonical
    config path): the operator's ``openrouter-funded`` LANE alias must never survive into
    ``agent._fallback_chain`` as a literal model id. The runtime fallback walk consumes the
    init chain as-is (``try_activate_fallback`` → the "attached fallback credential pool"
    path), while the free-guard's R1 operator set is built from ``get_fallback_chain`` —
    a raw lane here therefore walks an id the guard's exact-id set can never contain, and
    the funded lane takes a guaranteed veto (t_9cf902a8: live walk 'openrouter-funded'
    REJECTED while the guard's set held only the normalized 'z-ai/glm-5.3'). Normalization
    is idempotent for already-resolved chains, preserves order, and drops malformed
    entries after the rewrite (a lane-only entry now resolves instead of vanishing).
    """
    if isinstance(fallback_model, dict):
        fallback_model = [fallback_model]
    if not isinstance(fallback_model, list):
        return []
    try:
        from hermes_cli.fallback_config import (
            OPENROUTER_FUNDED_DEFAULT_MODEL,
            _funded_lane_model,
            _iter_fallback_entries,
        )
        # Honor the same override the canonical path honors; a config read failure keeps
        # the designated default instead of blocking agent init.
        try:
            from hermes_cli.config import load_config_readonly
            lane_model = _funded_lane_model(load_config_readonly() or {})
        except Exception:
            lane_model = OPENROUTER_FUNDED_DEFAULT_MODEL
        return _iter_fallback_entries(fallback_model, lane_model)
    except Exception:  # pragma: no cover - normalizer unavailable: keep the legacy filter
        logger.debug("fallback lane normalization unavailable; using legacy entry filter", exc_info=True)
        return [
            f for f in fallback_model if isinstance(f, dict) and f.get("provider") and f.get("model")
        ]


def _init_fallback_chain(agent, fallback_model):
    # Stable pool-entry identity: OAuth refreshes can replace the token before a failed
    # request is recovered, so the key value alone can't attribute the failure.
    from agent.agent_runtime_helpers import sync_credential_pool_entry_id
    sync_credential_pool_entry_id(agent)

    # Ordered backups tried when the primary is exhausted (legacy single-dict or list).
    agent._fallback_chain = _fallback_entries(fallback_model)
    agent._fallback_index = 0
    agent._fallback_activated = getattr(agent, "_fallback_activated", False)
    # Legacy attribute kept for backward compat (tests, external callers)
    agent._fallback_model = agent._fallback_chain[0] if agent._fallback_chain else None
    chain = agent._fallback_chain
    if chain and not agent.quiet_mode:
        labels = [f"{f['model']} ({f['provider']})" for f in chain]
        if len(chain) == 1:
            print(f"🔄 Fallback model: {labels[0]}")
        else:
            print(f"🔄 Fallback chain ({len(chain)} providers): " + " → ".join(labels))


def recompute_init_fallback_api_mode(agent, fb_client) -> None:
    """Give an init-time fallback its own api_mode instead of the unreachable primary's.

    A Nous primary configured with ``api_mode: codex_responses`` otherwise leaves a fallback whose
    profile serves that model on chat completions (``api_mode_for_model``) on the Responses API,
    which silently drops reasoning content (#46527).
    The per-turn path (``try_activate_fallback``) already recomputes via
    ``_fallback_api_mode_resolved``; this keeps init in agreement, and runs before the
    ``_primary_runtime`` snapshot so ``restore_primary_runtime`` replays the recomputed mode.
    A detection failure keeps the primary's mode and never blocks the fallback itself.
    """
    from agent.chat_completion_helpers import _fallback_api_mode_resolved
    try:
        agent.api_mode = _fallback_api_mode_resolved(
            agent, agent.provider, agent.model, str(getattr(fb_client, "base_url", "") or ""))
    except Exception:
        logger.debug("Init-time fallback api_mode detection failed for %s", agent.provider, exc_info=True)
        return
    if hasattr(agent, "_transport_cache"):
        agent._transport_cache.clear()
