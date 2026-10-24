"""Regression tests for HR-124: cron create preflight for --deliver origin and delivery_outcome handling."""
import types
from unittest.mock import patch, MagicMock

import pytest

from hermes_cli.cron import preflight_deliver_origin, cron_create
from cron.executions import finish_execution, create_execution, latest_execution
from cron.scheduler_bookkeeping import finish_completed_run
from cron import jobs


def test_preflight_deliver_origin_rejects_null_origin():
    """preflight_deliver_origin must reject 'origin' when origin is None."""
    err = preflight_deliver_origin(deliver="origin", origin=None)
    assert err is not None
    assert "Cannot use '--deliver origin'" in err
    assert "explicit delivery target" in err


def test_preflight_deliver_origin_rejects_comma_separated_origin():
    """preflight_deliver_origin must reject if 'origin' is one of several targets and origin is None."""
    err = preflight_deliver_origin(deliver="telegram,origin", origin=None)
    assert err is not None
    assert "Cannot use '--deliver origin'" in err


def test_preflight_deliver_origin_rejects_failure_deliver_origin():
    """preflight_deliver_origin must reject if failure_deliver is 'origin' and origin is None."""
    err = preflight_deliver_origin(deliver="local", failure_deliver="origin", origin=None)
    assert err is not None
    assert "Cannot use '--deliver origin'" in err


def test_preflight_deliver_origin_allows_valid_origin():
    """preflight_deliver_origin must accept 'origin' when origin is provided."""
    origin = {"platform": "telegram", "chat_id": "-100123456789"}
    err = preflight_deliver_origin(deliver="origin", origin=origin)
    assert err is None


def test_preflight_deliver_origin_allows_non_origin_targets():
    """preflight_deliver_origin must accept targets that do not include 'origin'."""
    assert preflight_deliver_origin(deliver="local", origin=None) is None
    assert preflight_deliver_origin(deliver="telegram", origin=None) is None
    assert preflight_deliver_origin(deliver=None, origin=None) is None


def test_cron_create_cli_exits_nonzero_on_deliver_origin(monkeypatch, capsys):
    """hermes cron create --deliver origin from CLI must exit non-zero and print error."""
    # Ensure env has no session origin
    monkeypatch.delenv("HERMES_SESSION_PLATFORM", raising=False)
    monkeypatch.delenv("HERMES_SESSION_CHAT_ID", raising=False)
    monkeypatch.delenv("HERMES_SESSION_KEY", raising=False)
    monkeypatch.delenv("HERMES_UI_SESSION_ID", raising=False)

    args = MagicMock()
    args.deliver = "origin"
    args.failure_deliver = None
    args.schedule = "every 1h"
    args.prompt = "test prompt"
    args.paused = False

    rc = cron_create(args)
    assert rc == 1

    captured = capsys.readouterr()
    assert "Cannot use '--deliver origin' from CLI" in captured.out or "Cannot use '--deliver origin' from CLI" in captured.err


def test_delivery_outcome_not_configured_marks_run_failed():
    """delivery_outcome=not_configured must mark execution status='failed' in cron history."""
    job_id = "test_hr124_unconf"
    execution = create_execution(job_id=job_id, source="test")
    exec_id = execution["id"]

    # Call finish_execution with delivery_outcome="not_configured" and success=True
    finished = finish_execution(exec_id, success=True, delivery_outcome="not_configured")
    assert finished is not None
    assert finished["status"] == "failed"
    assert finished["delivery_outcome"] == "not_configured"
    assert "delivery target is not configured" in (finished["error"] or "")


def test_finish_completed_run_with_unresolved_origin_marks_failed():
    """finish_completed_run marks execution status='failed' when delivery_outcome resolves to not_configured."""
    job = jobs.create_job(
        prompt="test prompt",
        schedule="every 1h",
        deliver="origin",
    )
    execution = create_execution(job_id=job["id"], source="test")
    exec_id = execution["id"]

    d = types.SimpleNamespace(
        job=job,
        delivery_error=None,
        should_deliver=True,
        unresolved_origin=True,
        incident_acked=False,
        success=True,
        error=None,
        blocked_config=False,
        failure_incident_id=None,
    )

    with patch("cron.scheduler.mark_job_run", return_value=True), \
         patch("cron.scheduler.self_removal_delivery_allowed", return_value=False), \
         patch("cron.scheduler._mark_incident_alerted"):
        finished = finish_completed_run(
            d,
            fire_owner=None,
            execution_id=exec_id,
            recovered=False,
        )
        assert finished is True

    latest = latest_execution(job["id"])
    assert latest is not None
    assert latest["delivery_outcome"] == "not_configured"
    assert latest["status"] == "failed"
