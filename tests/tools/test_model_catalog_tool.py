import json

import pytest

from tools import model_catalog_tool as tool
from tests.hermes_cli.test_fleet_catalog_adapters import FakeIO


def test_schema_has_machine_actions_and_approval_metadata():
    schema = tool.MODEL_CATALOG_SCHEMA
    action = schema["function"]["parameters"]["properties"]["action"]
    assert action["enum"] == ["plan_add", "apply", "rollback", "status"]
    assert schema["function"]["x-hermes-approval"]["actions"] == ["apply", "rollback"]


def test_plan_add_is_read_only_literal_and_secret_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(tool, "_plans_root", lambda: tmp_path)
    monkeypatch.setattr(tool, "_build_orchestrator", lambda: FakeOrchestrator())
    result = json.loads(tool.model_catalog("plan_add", "Vendor/Model.X", ["openai-codex"]))
    assert result["model"] == "Vendor/Model.X"
    assert result["status"] == "prepared"
    assert result["approval_required"] is True
    text = json.dumps(result)
    assert "sk-secret" not in text
    assert (tmp_path / result["transaction_id"] / "plan.json").is_file()


def test_apply_status_and_rollback_persist_receipt(tmp_path, monkeypatch):
    fake = FakeOrchestrator()
    monkeypatch.setattr(tool, "_plans_root", lambda: tmp_path)
    monkeypatch.setattr(tool, "_build_orchestrator", lambda: fake)
    monkeypatch.setattr(tool, "_invalidate_and_verify_catalog", lambda: None)
    plan = json.loads(tool.model_catalog("plan_add", "literal.id", ["openai-codex", "contabo-openai"]))
    txid = plan["transaction_id"]
    io = FakeIO(probe=lambda pool, model, kind: kind not in {"manifest", "models"})
    models_path = "/Users/user/contabo-router/app/models.json"
    io.files[models_path] = b'{"openai":{"models":[{"id":"old"}]}}'
    io.file_modes[models_path] = 0o640
    applied = json.loads(tool.model_catalog("apply", transaction_id=txid, require_approval=False, io=io))
    assert applied["status"] == "committed"
    assert json.loads(tool.model_catalog("status", transaction_id=txid))["status"] == "committed"
    rolled = json.loads(tool.model_catalog("rollback", transaction_id=txid, require_approval=False, io=io))
    assert rolled["status"] == "rolled_back"
    assert fake.events[-2:] == ["rollback:contabo-openai", "rollback:openai-codex"]
    again = json.loads(tool.model_catalog("rollback", transaction_id=txid, require_approval=False))
    assert again["status"] == "already_rolled_back"


def test_apply_persists_contabo_rollback_payload_before_commit(tmp_path, monkeypatch):
    fake = FakeOrchestrator()
    fake.assert_precommit_payload = True
    monkeypatch.setattr(tool, "_plans_root", lambda: tmp_path)
    monkeypatch.setattr(tool, "_build_orchestrator", lambda: fake)
    monkeypatch.setattr(tool, "_invalidate_and_verify_catalog", lambda: None)
    plan = json.loads(tool.model_catalog("plan_add", "literal.id", ["contabo-openai"]))
    fake.bundle = tmp_path / plan["transaction_id"]
    io = FakeIO()
    models_path = "/Users/user/contabo-router/app/models.json"
    io.files[models_path] = b'{"openai":{"models":[{"id":"old"}]}}'
    io.file_modes[models_path] = 0o640

    applied = json.loads(tool.model_catalog("apply", transaction_id=plan["transaction_id"], require_approval=False, io=io))

    assert applied["status"] == "committed"


def test_apply_journals_each_step_outside_plan_and_crash_is_recoverable(tmp_path, monkeypatch):
    fake = FakeOrchestrator()
    monkeypatch.setattr(tool, "_plans_root", lambda: tmp_path)
    monkeypatch.setattr(tool, "_build_orchestrator", lambda: fake)
    monkeypatch.setattr(tool, "_invalidate_and_verify_catalog", lambda: None)
    plan = json.loads(tool.model_catalog("plan_add", "literal.id", ["openai-codex", "contabo-openai"]))
    txid = plan["transaction_id"]
    fake.crash_after = 1
    io = FakeIO(probe=lambda pool, model, kind: kind not in {"manifest", "models"})
    models_path = "/Users/user/contabo-router/app/models.json"
    io.files[models_path] = b'{"openai":{"models":[{"id":"old"}]}}'
    io.file_modes[models_path] = 0o640
    failed = json.loads(tool.model_catalog("apply", transaction_id=txid, require_approval=False, io=io))
    assert failed["status"] == "error"
    persisted = json.loads((tmp_path / txid / "plan.json").read_text())
    assert persisted["status"] == "applying"
    assert "rollback_data" not in json.dumps(persisted)
    journal = json.loads((tmp_path / txid / "journal.json").read_text())
    assert journal["steps"][0]["state"] == "applied"
    assert (tmp_path / txid / journal["steps"][0]["rollback_snapshot"]).is_file()
    fake.crash_after = None
    rolled = json.loads(tool.model_catalog("rollback", transaction_id=txid, require_approval=False, io=io))
    assert rolled["status"] == "rolled_back"


def test_apply_journal_recovers_step_interrupted_while_applying(tmp_path, monkeypatch):
    fake = FakeOrchestrator()
    monkeypatch.setattr(tool, "_plans_root", lambda: tmp_path)
    monkeypatch.setattr(tool, "_build_orchestrator", lambda: fake)
    plan = json.loads(tool.model_catalog("plan_add", "literal.id", ["contabo-openai"]))
    txid = plan["transaction_id"]
    fake.crash_after_commit = True
    io = FakeIO(probe=lambda pool, model, kind: kind not in {"manifest", "models"})
    models_path = "/Users/user/contabo-router/app/models.json"
    io.files[models_path] = b'{"openai":{"models":[{"id":"old"}]}}'
    io.file_modes[models_path] = 0o640

    with pytest.raises(KeyboardInterrupt, match="simulated interrupt"):
        tool.model_catalog("apply", transaction_id=txid, require_approval=False, io=io)
    journal = json.loads((tmp_path / txid / "journal.json").read_text())
    assert journal["steps"][0]["state"] == "applying"

    fake.crash_after_commit = False
    rolled = json.loads(tool.model_catalog("rollback", transaction_id=txid, require_approval=False, io=io))
    assert rolled["status"] == "rolled_back"
    assert fake.events[-1] == "rollback:contabo-openai"


def test_apply_requires_approval_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(tool, "_plans_root", lambda: tmp_path)
    monkeypatch.setattr(tool, "_build_orchestrator", lambda: FakeOrchestrator())
    monkeypatch.setattr(tool, "_request_approval", lambda action, txid: False)
    plan = json.loads(tool.model_catalog("plan_add", "literal", ["openai-codex"]))
    result = json.loads(tool.model_catalog("apply", transaction_id=plan["transaction_id"]))
    assert result["status"] == "approval_denied"


class FakeOrchestrator:
    def __init__(self):
        from hermes_cli.fleet_catalog_adapters import ContaboOpenAIAdapter
        self.events = []
        self.crash_after: int | None = None
        self.crash_after_commit = False
        self.assert_precommit_payload = False
        self.bundle = None
        self.adapters = {
            "contabo-openai": ContaboOpenAIAdapter(),
        }


    def prepare(self, model, pools, *, io):
        from hermes_cli.fleet_catalog_adapters import PreparedChange
        return [PreparedChange(pool, model, {"token": "sk-secret", "availability": "ok"}) for pool in pools]

    def commit(self, changes, *, io):
        from hermes_cli.fleet_catalog_adapters import AppliedChange
        out = []
        for change in changes:
            self.events.append(f"commit:{change.pool}")
            out.append(AppliedChange(change, {"secret": "sk-secret"}))
        return out

    def commit_one(self, change, *, io):
        if self.assert_precommit_payload:
            assert self.bundle is not None
            journal = json.loads((self.bundle / "journal.json").read_text())
            step = journal["steps"][-1]
            assert step["state"] == "applying"
            assert (self.bundle / step["rollback_snapshot"]).is_file()
            assert (self.bundle / step["rollback_payload"]).is_file()
        if self.crash_after == len([e for e in self.events if e.startswith("commit:")]):
            raise RuntimeError("simulated crash")
        self.events.append(f"commit:{change.pool}")
        from hermes_cli.fleet_catalog_adapters import AppliedChange
        data: dict[str, object] = {}
        if change.pool == "contabo-openai":
            data = {"models_before_mode": 0o640,
                    "models_before_bytes": b'{"openai":{"models":[{"id":"old"}]}}'}
        item = AppliedChange(change, data)
        if self.crash_after_commit:
            self.events.append(f"committed-before-interrupt:{change.pool}")
            raise KeyboardInterrupt("simulated interrupt after commit")
        return item

    def activate_catalog(self, changes, *, io, snapshot=None):
        self.events.append("catalog")

    def rollback(self, changes, *, io):
        for change in reversed(changes):
            self.events.append(f"rollback:{change.prepared.pool}")
