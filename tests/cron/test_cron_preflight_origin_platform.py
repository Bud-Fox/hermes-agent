"""Preflight blocks an undeliverable persisted origin BEFORE the job runs (#t_88980860).

A cron job created on a non-delivery surface (``origin.platform='webui'``) with
``deliver: origin`` used to run to completion and only then fail as
``delivery_failed: unknown platform 'webui'`` (scheduler_delivery._prepare_target_delivery,
Platform(name) ValueError). The delivery check in the preflight now applies the SAME
Platform-resolution predicate to the origin's platform when a lane includes ``origin``,
so the mismatch surfaces as a visible preflight block instead of a silent post-run
delivery failure.
"""

import pytest

from cron import scheduler_preflight as sched_preflight


def _job(platform: str) -> dict:
    return {
        "id": "t1",
        "name": "origin-platform-check",
        "deliver": "origin",
        "origin": {"platform": platform, "chat_id": "local-abc", "thread_id": None},
    }


def test_unresolvable_origin_platform_blocks_before_run():
    reason = sched_preflight._preflight_check_delivery(_job("webui"))
    assert reason is not None
    assert "webui" in reason
    assert "origin" in reason


def test_resolvable_origin_platform_passes_delivery_check():
    assert sched_preflight._preflight_check_delivery(_job("local")) is None


def test_origin_missing_platform_still_passes_here():
    job = _job("webui")
    job["origin"] = {}
    # missing origin is HR-124's create-time check; the platform check must not mask it
    assert sched_preflight._preflight_check_delivery(job) is None


def test_no_origin_token_unchecked():
    job = _job("webui")
    job["deliver"] = "local"
    assert sched_preflight._preflight_check_delivery(job) is None


def test_failure_deliver_origin_checked_too():
    job = _job("webui")
    job["deliver"] = "local"
    job["failure_deliver"] = "origin"
    reason = sched_preflight._preflight_check_delivery(job)
    assert reason is not None and "webui" in reason


def test_preflight_job_config_surfaces_reason():
    # the dispatcher refuses BEFORE building agent machinery on a truthy reason
    assert sched_preflight._preflight_job_config(_job("webui"), {}) is not None
