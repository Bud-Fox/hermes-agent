"""Regression coverage for replacing native Codex threads after /model switches."""

from unittest.mock import MagicMock, patch

from agent.agent_runtime_helpers import switch_model


class _FakeCodexSession:
    def __init__(self):
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


def _agent_with_native_codex_session():
    """A live native-runtime agent without constructing an SDK client."""
    agent = MagicMock(name="native-codex-agent")
    agent.model = "chatgpt-web/light"
    agent.provider = "chatgpt-web"
    agent.base_url = "https://chatgpt.com/backend-api/codex/responses"
    agent.api_key = "test-token"
    agent.api_mode = "codex_app_server"
    agent.client = MagicMock(name="client")
    agent._client_kwargs = {"api_key": "test-token", "base_url": agent.base_url}
    agent._anthropic_client = None
    agent._anthropic_api_key = ""
    agent._anthropic_base_url = None
    agent._is_anthropic_oauth = False
    agent._config_context_length = None
    agent._transport_cache = {}
    agent._cached_system_prompt = "cached"
    agent.context_compressor = None
    agent._use_prompt_caching = False
    agent._use_native_cache_layout = False
    agent._primary_runtime = {}
    agent._fallback_activated = False
    agent._fallback_index = 0
    agent._fallback_chain = []
    agent._fallback_model = None
    agent._credential_pool = MagicMock(name="credential-pool")
    agent._anthropic_prompt_cache_policy.return_value = (False, False)
    agent._ensure_lmstudio_runtime_loaded.return_value = None
    agent._create_openai_client.return_value = MagicMock(name="replacement-client")
    agent._codex_session = _FakeCodexSession()
    return agent


def _switch(agent, model):
    with patch("hermes_cli.timeouts.get_provider_request_timeout", return_value=None):
        switch_model(
            agent,
            new_model=model,
            new_provider="chatgpt-web",
            api_key="test-token",
            base_url="https://chatgpt.com/backend-api/codex/responses",
            api_mode="codex_app_server",
        )


def test_model_change_retires_native_codex_session_before_next_turn():
    """A new native model cannot share the thread that captured the old one."""
    agent = _agent_with_native_codex_session()
    old_session = agent._codex_session

    _switch(agent, "chatgpt-web/high")

    assert old_session.close_calls == 1
    assert agent._codex_session is None


def test_noop_model_switch_keeps_native_codex_session():
    """Re-selecting the same native identity must not kill its reusable thread."""
    agent = _agent_with_native_codex_session()
    codex_session = agent._codex_session

    _switch(agent, "chatgpt-web/light")

    assert codex_session.close_calls == 0
    assert agent._codex_session is codex_session
