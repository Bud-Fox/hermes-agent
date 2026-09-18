"""Task 5 (R2): OpenAI Chat Completions caps ``tool_call.id`` at 64 chars (a longer
id — e.g. one minted by another provider earlier in the conversation — is a
non-retryable 400 ``string_above_max_length``). ``_cap_tool_call_ids`` caps every
oversized id with a deterministic sha256 remap and rewrites the paired
``tool_call_id`` with the SAME value so request/response pairing survives. The cap is
wired into ``sanitize_outbound_kwargs`` only for the OpenAI-compatible chat wire
(``_provider_enforces_64_id``); Anthropic-native and Gemini-native are never touched.
"""

from types import SimpleNamespace

from agent.message_sanitization import _cap_tool_call_ids  # new helper


def test_long_id_capped_and_pair_remapped_consistently():
    long_id = "call_" + "a" * 70  # length 75 > 64
    msgs = [
        {"role": "assistant", "tool_calls": [{"id": long_id, "type": "function",
            "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": long_id, "content": "ok"},
    ]
    _cap_tool_call_ids(msgs, limit=64)
    new_id = msgs[0]["tool_calls"][0]["id"]
    assert len(new_id) <= 64
    assert msgs[1]["tool_call_id"] == new_id     # pairing preserved
    assert new_id != long_id


def test_short_ids_untouched():
    msgs = [{"role": "assistant", "tool_calls": [{"id": "call_abc", "type": "function",
             "function": {"name": "f", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call_abc", "content": "ok"}]
    _cap_tool_call_ids(msgs, limit=64)
    assert msgs[0]["tool_calls"][0]["id"] == "call_abc"


def test_cap_is_deterministic():
    from agent.message_sanitization import _cap_id
    long_id = "x" * 90
    assert _cap_id(long_id, 64) == _cap_id(long_id, 64)
    assert len(_cap_id(long_id, 64)) <= 64


def test_predicate_true_only_for_openai_compatible_chat_wire():
    """The cap must fire for the OpenAI-compatible chat wire and NEVER for
    Anthropic-native or Gemini-native (they have their own id rules / message shapes).
    A wrong predicate that capped Anthropic ids would be a real regression."""
    from agent.message_sanitization import _provider_enforces_64_id

    openai_chat = SimpleNamespace(api_mode="chat_completions",
                                  base_url="https://contabo-openai.example/v1")
    assert _provider_enforces_64_id(openai_chat) is True

    # Anthropic-native: different wire (api_mode), own id rules — must NOT cap.
    anthropic_native = SimpleNamespace(api_mode="anthropic_messages",
                                       base_url="https://api.anthropic.com")
    assert _provider_enforces_64_id(anthropic_native) is False

    # Gemini-native rides chat_completions api_mode but on a native base_url — must NOT cap.
    gemini_native = SimpleNamespace(
        api_mode="chat_completions",
        base_url="https://generativelanguage.googleapis.com/v1beta")
    assert _provider_enforces_64_id(gemini_native) is False

    # codex_responses has its own id clamp downstream — not this cap's job.
    codex = SimpleNamespace(api_mode="codex_responses",
                            base_url="https://api.openai.com/v1")
    assert _provider_enforces_64_id(codex) is False

    # Fail-safe: a broken agent (missing/exploding attrs) caps nothing.
    assert _provider_enforces_64_id(object()) is False
