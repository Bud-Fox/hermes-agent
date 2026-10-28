"""F2 regression (#t_88980860): an unverified bot-chat delivery failure must log its identity.

``_deliver_to_bot_chat``'s catch-all used to return only the bare string
``"bot-chat delivery to profile '...' unverified: <exc>"``: live logs showed
``unverified: not_found`` with no exception type, delivery id or run id, so the
exact refusing branch (gateway/session_local_migration.adopt_legacy_session) could
never be proven from persisted logs. The handler now logs the correlated identity
(job id, profile, exception type, delivery id, run id, for_failure, traceback) at
ERROR before returning the same string.

Mutation sensitivity: deleting the ``logger.error`` call makes these tests fail on
the empty caplog record.
"""

import logging

import pytest

from cron import scheduler_delivery as sched_delivery


@pytest.fixture()
def delivery_env(monkeypatch):
    """Reach the catch-all with a settled identity: no receipt, owner discovered, send refuses."""
    import tools.bot_live_delivery as bld

    owner = {"profile_home": "/x", "session_id": "tip1", "lease_id": "lease1",
             "live_session_id": "tip1"}
    monkeypatch.setattr(bld, "read_delivery_result", lambda home, key: None)
    monkeypatch.setattr(bld, "find_canonical_live_owner", lambda home: owner)

    def refuse(*args, **kwargs):
        raise RuntimeError("not_found")

    monkeypatch.setattr(bld, "deliver_to_live_owner", refuse)


def test_botchat_unverified_failure_logs_correlated_identity(delivery_env, caplog):
    job = {"id": "job_f2", "name": "f2-regression", "execution_id": "exec_f2_123"}
    with caplog.at_level(logging.ERROR, logger="cron.scheduler"):
        result = sched_delivery._deliver_to_bot_chat(job, "report body", "")
    # behavior contract unchanged: caller still gets the explicit unverified status string
    assert result == "bot-chat delivery to profile '(own)' unverified: not_found"
    records = [r for r in caplog.records if r.levelno == logging.ERROR
               and "unverified" in r.getMessage()]
    assert records, "F2 mutation: correlated ERROR log missing"
    msg = records[0].getMessage()
    assert "RuntimeError" in msg and "not_found" in msg
    assert "job_f2" in msg and "(own)" in msg
    assert "delivery_id=" in msg and "run_id=exec_f2_123" in msg
    assert records[0].exc_info is not None, "traceback must be attached for branch proof"


def test_botchat_unverified_failure_run_id_falls_back_to_transient(delivery_env, caplog):
    job = {"id": "job_f2b", "name": "f2b"}
    with caplog.at_level(logging.ERROR, logger="cron.scheduler"):
        result = sched_delivery._deliver_to_bot_chat(job, "body", "any-crm-master-agent")
    assert result == ("bot-chat delivery to profile 'any-crm-master-agent' "
                      "unverified: not_found")
    records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert records and "job_f2b" in records[0].getMessage()
