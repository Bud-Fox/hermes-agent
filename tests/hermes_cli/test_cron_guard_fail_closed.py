"""Regression tests for HR-123: guard fail-closed with typed PolicyViolation outcomes.

Verifies:
1. Guard reject raises typed PolicyViolation and never calls upstream provider (fail-closed, no fail-open via middleware skip).
2. Standalone cron run on reject marks run status='failed' and triggers failure_deliver notice.
3. Fallback chain on reject falls back to next link, and next link is used.
4. Bookkeeping and _final_response_from_result catch blocked responses (defense-in-depth).
"""
from __future__ import annotations

import types
from unittest.mock import MagicMock, patch

import pytest

from agent.error_classifier import FailoverReason, classify_api_error
from agent.turn_retry_state import TurnRetryState
from cron import delivery_queue, executions, jobs, scheduler
from cron.scheduler_bookkeeping import fail_empty_response
from hermes_cli.middleware import (
    LLM_EXECUTION_MIDDLEWARE,
    PolicyViolation,
    is_policy_violation,
    run_llm_execution_middleware,
)
from hermes_cli.plugins import _delivery_manager


def test_is_policy_violation():
    """Verify is_policy_violation recognizes both instances and duck-typed objects."""
    exc = PolicyViolation("blocked by rule", rule="R1")
    assert is_policy_violation(exc) is True

    class CustomPolicyViolation(PolicyViolation):
        pass

    assert is_policy_violation(CustomPolicyViolation("custom")) is True

    class NamedPolicyViolation(Exception):
        __name__ = "PolicyViolation"

    NamedPolicyViolation.__name__ = "PolicyViolation"
    assert is_policy_violation(NamedPolicyViolation("named")) is True

    fake = types.SimpleNamespace(_is_policy_violation=True)
    assert is_policy_violation(fake) is True

    assert is_policy_violation(RuntimeError("other")) is False
    assert is_policy_violation(None) is False


def test_middleware_reject_fail_closed_never_calls_downstream(monkeypatch):
    """Guard raising PolicyViolation must fail-closed: downstream provider is never called and middleware line ~212 must not skip it."""
    manager = _delivery_manager()
    downstream = MagicMock(return_value={"choices": [{"message": {"content": "paid-response"}}]})

    def guard_reject_hook(**kwargs):
        raise PolicyViolation("openrouter-free-guard REJECTED: non-free model", rule="R1")

    monkeypatch.setattr(manager, "_middleware", {LLM_EXECUTION_MIDDLEWARE: [guard_reject_hook]})

    with pytest.raises(PolicyViolation) as exc_info:
        run_llm_execution_middleware(
            {"model": "openai/gpt-4o-mini", "provider": "openrouter"},
            downstream,
        )

    assert "REJECTED: non-free model" in str(exc_info.value)
    assert downstream.call_count == 0, "Downstream provider must NEVER be called after guard reject"


def test_middleware_returned_policy_violation_raises_and_never_calls_downstream(monkeypatch):
    """Guard returning PolicyViolation instance must also be raised and never call downstream."""
    manager = _delivery_manager()
    downstream = MagicMock()

    def guard_return_reject_hook(**kwargs):
        return PolicyViolation("openrouter-free-guard REJECTED", rule="R1")

    monkeypatch.setattr(manager, "_middleware", {LLM_EXECUTION_MIDDLEWARE: [guard_return_reject_hook]})

    with pytest.raises(PolicyViolation):
        run_llm_execution_middleware(
            {"model": "openai/gpt-4o-mini"},
            downstream,
        )

    assert downstream.call_count == 0


def test_classify_api_error_for_policy_violation():
    """PolicyViolation must classify as non-retryable with should_fallback=True."""
    exc = PolicyViolation("openrouter-free-guard REJECTED model 'paid/x'", rule="R1")
    classified = classify_api_error(exc)

    assert classified.reason == FailoverReason.policy_violation
    assert classified.retryable is False
    assert classified.should_fallback is True
    assert classified.error_context.get("rule") == "R1"


def test_cron_run_guard_reject_marks_failed_and_triggers_failure_deliver(tmp_path, monkeypatch):
    """Cron run encountering guard reject must mark execution status='failed' and trigger failure_deliver."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    job = jobs.create_job(
        prompt="run cron task",
        schedule="every 1h",
        deliver="local",
        failure_deliver="telegram:-100123456",
    )

    upstream = MagicMock()

    def fake_run_job(job_to_run, **kw):
        # Simulate agent API retry loop failing on PolicyViolation
        exc = PolicyViolation("openrouter-free-guard REJECTED model 'openai/gpt-4o-mini': R1", rule="R1")
        error_msg = f"{type(exc).__name__}: {exc!s}"
        return False, "", "", error_msg

    monkeypatch.setattr(scheduler, "run_job", fake_run_job)

    enqueued = []

    def fake_enqueue(exec_id, queued_job, deliver_content, for_failure=False):
        enqueued.append({"exec_id": exec_id, "for_failure": for_failure, "content": deliver_content})
        return {"id": "del-1", "status": "queued"}

    monkeypatch.setattr(delivery_queue, "enqueue", fake_enqueue)

    scheduler.run_one_job(job)

    execution = executions.latest_execution(job["id"])
    assert execution is not None
    assert execution["status"] == "failed"
    assert "PolicyViolation" in (execution["error"] or "")
    assert upstream.call_count == 0

    assert len(enqueued) >= 1
    assert any(e["for_failure"] is True for e in enqueued), "Failure deliver notice must be queued on reject"


def test_guard_reject_with_chain_falls_back_to_next_link(monkeypatch):
    """When a guard rejects the first model but a fallback chain exists, the next link must be used."""
    from agent.conversation_loop import _run_phase
    from agent.turn_api_error import handle_api_error

    calls_made = []

    class MockAgent:
        def __init__(self):
            self.model = "openai/gpt-4o-mini"
            self.provider = "openrouter"
            self.base_url = "https://openrouter.ai/api/v1"
            self.platform = "cron"
            self.api_mode = "chat_completions"
            self.session_id = "test-session"
            self.api_key = "test-key"
            self._fallback_chain = [
                {"model": "allowed-model:free", "provider": "openrouter", "base_url": "https://openrouter.ai/api/v1"}
            ]
            self._fallback_index = 0
            self._pending_redirect = None
            self._unavailable_fallback_keys = set()
            self._provider_fallback_active = False
            self.thinking_callback = None
            self._image_rejecting_models = set()
            self._credential_pool = None
            self.log_prefix = ""
            self._interrupt_requested = False

        def _extract_api_error_context(self, err):
            return getattr(err, "details", {}) or {}

        def _touch_activity(self, label=""):
            pass

        def _invoke_api_request_error_hook(self, **kw):
            pass

        def _recover_with_credential_pool(self, *a, **k):
            return False, False

        def _summarize_api_error(self, err):
            return str(err)

        def _client_log_context(self):
            return ""

        def _buffer_vprint(self, *a, **k):
            pass

        def _is_openrouter_url(self, *a, **k):
            return True

        def _has_pending_fallback(self):
            return self._fallback_index < len(self._fallback_chain)

        def _try_activate_fallback(self, reason=None, reset_at=None):
            if not self._has_pending_fallback():
                return False
            fb = self._fallback_chain[self._fallback_index]
            self._fallback_index += 1
            self.model = fb["model"]
            self.provider = fb["provider"]
            self.base_url = fb["base_url"]
            self._provider_fallback_active = True
            return True

        def _buffer_diagnostic_status(self, msg):
            pass

        def _has_pending_redirect(self):
            return False

        def clear_interrupt(self, **kw):
            return False

    agent = MockAgent()

    # Verify initial model is rejected
    initial_err = PolicyViolation("openrouter-free-guard REJECTED model 'openai/gpt-4o-mini'", rule="R1")

    s = types.SimpleNamespace(
        agent=agent,
        api_messages=[{"role": "user", "content": "hi"}],
        api_kwargs={"model": agent.model, "messages": []},
        api_response=None,
        active_system_prompt="system",
        system_message="system",
        thinking_spinner=None,
        current_turn_user_idx=0,
        messages=[],
        conversation_history=[],
        retry_count=0,
        max_retries=3,
        compression_attempts=0,
        max_compression_attempts=2,
        api_call_count=0,
        approx_tokens=10,
        turn_id="t1",
        effective_task_id="task1",
        api_request_id="req1",
        api_start_time=1000.0,
        _llm_middleware_trace=[],
        _retry=TurnRetryState(),
    )

    # First attempt: guard reject triggers error handling
    verdict = _run_phase(handle_api_error, agent, s, api_error=initial_err)
    # Verdict breaks retry loop to restart with new model
    assert verdict.action == "break"
    assert agent.model == "allowed-model:free"
    assert agent._provider_fallback_active is True
    assert s._retry.restart_with_rebuilt_messages is True

    # Next attempt with the fallback link is allowed past guard
    manager = _delivery_manager()

    def guard_hook(**kwargs):
        m = kwargs.get("model") or (kwargs.get("request") or {}).get("model")
        if m == "openai/gpt-4o-mini":
            raise PolicyViolation("Rejected non-free", rule="R1")
        # Allowed model
        calls_made.append(m)
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(manager, "_middleware", {LLM_EXECUTION_MIDDLEWARE: [guard_hook]})
    downstream = MagicMock(return_value={"choices": [{"message": {"content": "ok"}}]})

    resp = run_llm_execution_middleware({"model": agent.model}, downstream)
    assert calls_made == ["allowed-model:free"]
    assert resp["choices"][0]["message"]["content"] == "ok"


def test_scheduler_bookkeeping_fails_on_blocked_response():
    """Bookkeeping must turn d.success=False when response contains guard reject text or blocked_by."""
    d = types.SimpleNamespace(
        success=True,
        error=None,
        blocked_by=None,
    )
    fail_empty_response(d, "openrouter-free-guard REJECTED model 'paid/x' (context=cron): R1")
    assert d.success is False
    assert "openrouter-free-guard REJECTED" in (d.error or "")


def test_final_response_from_result_raises_policy_violation():
    """_final_response_from_result must raise PolicyViolation when result marks policy_violation or blocked_by."""
    from cron.scheduler import _final_response_from_result

    result = {
        "completed": False,
        "failed": True,
        "failure_reason": "policy_violation",
        "error": "openrouter-free-guard REJECTED model",
        "final_response": "error notice",
    }

    with pytest.raises(PolicyViolation):
        _final_response_from_result(result, "job1", "test-job", None)

    # Also raises PolicyViolation if blocked_by is stamped on synthetic result
    synthetic = {
        "completed": True,
        "failed": False,
        "blocked_by": "openrouter-free-guard",
        "final_response": "openrouter-free-guard REJECTED",
    }
    with pytest.raises(PolicyViolation):
        _final_response_from_result(synthetic, "job1", "test-job", None)
