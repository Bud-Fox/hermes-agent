"""The operator's ``openrouter-funded`` lane alias must resolve to a real route.

2026-10-22 cascade: the chain carried the lane as ``{provider: openrouter-funded, model:
z-ai/glm-5.3}`` (legacy) and later ``{provider: openrouter, model: openrouter-funded}``
(Phase A revival). "openrouter-funded" is not a provider and not an OpenRouter catalog id,
so the binder returned None ("provider not configured") and the funded lane was absent
from the live transition. get_fallback_chain must normalize both spellings.
"""
import logging

from hermes_cli.fallback_config import (
    OPENROUTER_FUNDED_DEFAULT_MODEL,
    get_fallback_chain,
)


class TestFundedLaneNormalization:
    def test_legacy_pseudo_provider_form_resolves_to_real_route(self):
        chain = get_fallback_chain({
            "fallback_providers": [{"provider": "openrouter-funded", "model": "z-ai/glm-5.3"}],
        })
        assert chain == [{"provider": "openrouter", "model": "z-ai/glm-5.3"}]

    def test_current_model_alias_form_gets_designated_model(self):
        chain = get_fallback_chain({
            "fallback_providers": [{"provider": "openrouter", "model": "openrouter-funded"}],
        })
        assert chain == [{"provider": "openrouter", "model": OPENROUTER_FUNDED_DEFAULT_MODEL}]

    def test_lane_only_entry_survives_parse(self):
        chain = get_fallback_chain({
            "fallback_providers": [{"provider": "openrouter-funded"}],
        })
        assert chain == [{"provider": "openrouter", "model": OPENROUTER_FUNDED_DEFAULT_MODEL}]

    def test_provider_routing_override_wins(self):
        chain = get_fallback_chain({
            "fallback_providers": [{"provider": "openrouter", "model": "openrouter-funded"}],
            "provider_routing": {"openrouter_funded_model": "z-ai/glm-5.2"},
        })
        assert chain == [{"provider": "openrouter", "model": "z-ai/glm-5.2"}]

    def test_both_spellings_dedup_to_one_slot(self):
        chain = get_fallback_chain({
            "fallback_providers": [
                {"provider": "openrouter-funded", "model": "z-ai/glm-5.3"},
                {"provider": "openrouter", "model": "openrouter-funded"},
            ],
        })
        assert len(chain) == 1
        assert chain[0]["provider"] == "openrouter"

    def test_real_routes_are_untouched(self):
        entries = [
            {"provider": "openai-codex", "model": "gpt-5.6-sol"},
            {"provider": "openrouter", "model": "thinkingmachines/inkling:free"},
            {"provider": "zai", "model": "glm-5.3-flash"},
        ]
        chain = get_fallback_chain({"fallback_providers": entries})
        assert [(e["provider"], e["model"]) for e in chain] == [
            (e["provider"], e["model"]) for e in entries
        ]

    def test_normalization_is_logged(self, caplog):
        with caplog.at_level(logging.INFO, logger="hermes_cli.fallback_config"):
            get_fallback_chain({
                "fallback_providers": [{"provider": "openrouter-funded", "model": "z-ai/glm-5.3"}],
            })
        assert any("normalized to openrouter/z-ai/glm-5.3" in r.message for r in caplog.records), caplog.records
