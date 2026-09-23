"""Regression tests for the silent stream truncation gap (SDD task B3).

A stream that dies mid-flight must NEVER end the turn as a normal-looking
response. Two shapes are pinned:

1. Zero-chunk abort (router STREAM_ABORTED transport / eventless SSE / empty
   iterator) → retried as a transient failure and, once retries are spent,
   surfaces as a *retryable* ``ProviderStreamError`` (never a silent empty
   response).
2. ≥1 chunk delivered, then the stream aborts — including the router's
   post-mortem markers ``finish_reason="upstream_truncated"`` /
   ``"upstream_stalled"`` sent over HTTP 200 with ``[DONE]`` — the turn gets a
   VISIBLE error; silently stamping the partial text as a completed answer is
   the bug this file pins shut.

The pre-fix behavior (verified by probe on this branch):
- marker finish_reasons passed every drop-guard in ``_finish_chat_stream``
  (each requires ``finish_reason is None``) and produced a NORMAL response,
  ending the turn with truncated text and zero user-facing warnings.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from hermes_constants import PARTIAL_STREAM_STUB_ID


def _make_stream_chunk(content=None, tool_calls=None, finish_reason=None):
    delta = SimpleNamespace(
        content=content, tool_calls=tool_calls,
        reasoning_content=None, reasoning=None,
    )
    choice = SimpleNamespace(index=0, delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], model="test/model", usage=None)


def _make_agent():
    from run_agent import AIAgent
    agent = AIAgent(
        api_key="test-key",
        base_url="https://example.com/v1",
        model="test/model",
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
    )
    agent.api_mode = "chat_completions"
    agent._interrupt_requested = False
    return agent


# ── (а) zero chunks + abort → retryable ProviderStreamError ───────────────

class TestZeroChunkAbortIsRetryableProviderStreamError:
    """0 delivered chunks + stream abort must surface as a retryable
    ProviderStreamError (after the in-helper retry budget is spent), never as
    a silent empty/normal response."""

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
    @patch("run_agent.AIAgent._create_request_openai_client")
    @patch("run_agent.AIAgent._close_request_openai_client")
    def test_zero_chunks_abort_raises_provider_stream_error(self, _mock_close, mock_create, monkeypatch):
        """Stream aborts before ANY chunk: with HERMES_STREAM_RETRIES=0 the call
        must raise ProviderStreamError (retryable class), not EmptyStreamError
        and not a fabricated response."""
        from agent.chat_completion_helpers import ProviderStreamError

        def _aborting_stream():
            raise RuntimeError("stream aborted before any chunk (router STREAM_ABORTED)")
            yield  # pragma: no cover

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = lambda *a, **kw: _aborting_stream()
        mock_create.return_value = mock_client

        agent = _make_agent()
        monkeypatch.setenv("HERMES_STREAM_RETRIES", "0")

        with pytest.raises(ProviderStreamError) as exc_info:
            agent._interruptible_streaming_api_call({})

        assert getattr(exc_info.value, "body", {}).get("error", {}).get("code") == "provider_stream_aborted"

        from agent.error_classifier import classify_api_error
        classified = classify_api_error(exc_info.value)
        assert classified.retryable is True, (
            "A zero-chunk stream abort must classify as retryable so the main "
            "loop can fall back instead of surfacing a dead turn."
        )

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
    @patch("run_agent.AIAgent._create_request_openai_client")
    @patch("run_agent.AIAgent._close_request_openai_client")
    def test_zero_chunk_marker_only_stream_is_not_silent(self, _mock_close, mock_create, monkeypatch):
        """Router STREAM_ABORTED post-mortem shape with ZERO content chunks:
        marker-only finish_reason="upstream_truncated" + [DONE]. Must not
        fabricate a normal response."""
        def _marker_only_stream():
            yield _make_stream_chunk(finish_reason="upstream_truncated")

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = lambda *a, **kw: _marker_only_stream()
        mock_create.return_value = mock_client

        agent = _make_agent()
        monkeypatch.setenv("HERMES_STREAM_RETRIES", "0")

        with pytest.raises(Exception) as exc_info:
            agent._interruptible_streaming_api_call({})

        assert type(exc_info.value).__name__ == "ProviderStreamError", (
            "A marker-only aborted stream (no content) must surface as "
            "ProviderStreamError, not a normal response."
        )
        assert getattr(exc_info.value, "body", {}).get("error", {}).get("code") == "provider_stream_aborted"


# ── (б) ≥1 chunk delivered + abort → visible error, never silent ─────────

class TestPartialDeliveryAbortIsVisible:
    """≥1 chunk delivered and the stream aborts: the turn must end in a
    VISIBLE failure — either the partial-stub continuation path (explicit
    length-stamped stub, never plain "stop") or a surfaced error — but NEVER
    a silent truncation stamped as a completed response."""

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
    @patch("run_agent.AIAgent._create_request_openai_client")
    @patch("run_agent.AIAgent._close_request_openai_client")
    def test_router_truncation_marker_after_text_never_silent(self, _mock_close, mock_create, monkeypatch):
        """THE core bug: router post-mortem `finish_reason="upstream_truncated"`
        after ≥1 text chunk arrives as a normal-looking terminal chunk on HTTP
        200 + [DONE]. Pre-fix: response assembled as normal → turn ends with
        truncated text, zero warnings. Post-fix: must NOT return a normal
        response — visible error or continuation stub instead."""
        def _truncated_stream():
            yield _make_stream_chunk(content="Partial answer so far")
            yield _make_stream_chunk(finish_reason="upstream_truncated")

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = lambda *a, **kw: _truncated_stream()
        mock_create.return_value = mock_client

        agent = _make_agent()
        monkeypatch.setenv("HERMES_STREAM_RETRIES", "0")

        with pytest.raises(Exception) as exc_info:
            agent._interruptible_streaming_api_call({})

        assert type(exc_info.value).__name__ == "ProviderStreamError", (
            "Router upstream_truncated after delivered text must surface as a "
            "provider stream error, never a silently-truncated response."
        )
        assert "upstream_truncated" in str(exc_info.value) or "truncat" in str(exc_info.value).lower()

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
    @patch("run_agent.AIAgent._create_request_openai_client")
    @patch("run_agent.AIAgent._close_request_openai_client")
    def test_router_stall_marker_after_text_never_silent(self, _mock_close, mock_create, monkeypatch):
        """Same contract for the idle-stall marker `upstream_stalled`."""
        def _stalled_stream():
            yield _make_stream_chunk(content="Partial answer so far")
            yield _make_stream_chunk(finish_reason="upstream_stalled")

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = lambda *a, **kw: _stalled_stream()
        mock_create.return_value = mock_client

        agent = _make_agent()
        monkeypatch.setenv("HERMES_STREAM_RETRIES", "0")

        with pytest.raises(Exception) as exc_info:
            agent._interruptible_streaming_api_call({})

        assert type(exc_info.value).__name__ == "ProviderStreamError"

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
    @patch("run_agent.AIAgent._create_request_openai_client")
    @patch("run_agent.AIAgent._close_request_openai_client")
    def test_exception_abort_after_text_is_not_silent_with_retries_left(self, _mock_close, mock_create, monkeypatch):
        """Abrupt iterator death after ≥1 delivered chunk with retry budget
        left: the helper retries the stream (duplicated-preamble contract) —
        and when retries are exhausted the error surfaces. Pinned: the
        call must not return a normal response stamped 'stop'."""
        def _abort_after_text():
            yield _make_stream_chunk(content="Half an answer")
            raise RuntimeError("simulated connection drop mid-stream")

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = lambda *a, **kw: _abort_after_text()
        mock_create.return_value = mock_client

        agent = _make_agent()
        monkeypatch.setenv("HERMES_STREAM_RETRIES", "0")

        with pytest.raises(Exception) as exc_info:
            agent._interruptible_streaming_api_call({})

        assert type(exc_info.value).__name__ == "ProviderStreamError", (
            "An untyped mid-stream iterator death must surface as a typed "
            "ProviderStreamError so the existing retryable machinery applies."
        )

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
    @patch("run_agent.AIAgent._create_request_openai_client")
    @patch("run_agent.AIAgent._close_request_openai_client")
    def test_visible_text_delivered_marker_abort_surfaces_as_stub_or_error(self, _mock_close, mock_create, monkeypatch):
        """Visible-text variant of the core case: text was actually delivered
        to a consumer (agent._fire_stream_delta records it). With retries
        exhausted the partial-delivery path builds the continuation stub —
        which is the VISIBLE error contract (length-stamped, flagged) — or
        re-raises. Either way NOT a plain normal response stamped 'stop'."""
        def _truncated_stream():
            yield _make_stream_chunk(content="Visible partial answer")
            yield _make_stream_chunk(finish_reason="upstream_truncated")

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = lambda *a, **kw: _truncated_stream()
        mock_create.return_value = mock_client

        agent = _make_agent()
        agent._fire_stream_delta = lambda text: None
        monkeypatch.setenv("HERMES_STREAM_RETRIES", "0")

        with pytest.raises(Exception) as exc_info:
            agent._interruptible_streaming_api_call({})

        assert type(exc_info.value).__name__ == "ProviderStreamError", (
            "Even with visible text delivered, the router truncation marker "
            "must surface as an error the turn can act on — never silence."
        )
