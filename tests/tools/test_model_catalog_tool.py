import json

import pytest

from tools import model_catalog_tool as tool


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
    plan = json.loads(tool.model_catalog("plan_add", "literal.id", ["openai-codex", "contabo-openai"]))
    txid = plan["transaction_id"]
    applied = json.loads(tool.model_catalog("apply", transaction_id=txid, require_approval=False))
    assert applied["status"] == "committed"
    assert json.loads(tool.model_catalog("status", transaction_id=txid))["status"] == "committed"
    rolled = json.loads(tool.model_catalog("rollback", transaction_id=txid, require_approval=False))
    assert rolled["status"] == "rolled_back"
    assert fake.events[-2:] == ["rollback:contabo-openai", "rollback:openai-codex"]
    again = json.loads(tool.model_catalog("rollback", transaction_id=txid, require_approval=False))
    assert again["status"] == "already_rolled_back"


def test_apply_requires_approval_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(tool, "_plans_root", lambda: tmp_path)
    monkeypatch.setattr(tool, "_build_orchestrator", lambda: FakeOrchestrator())
    monkeypatch.setattr(tool, "_request_approval", lambda action, txid: False)
    plan = json.loads(tool.model_catalog("plan_add", "literal", ["openai-codex"]))
    result = json.loads(tool.model_catalog("apply", transaction_id=plan["transaction_id"]))
    assert result["status"] == "approval_denied"


class FakeOrchestrator:
    def __init__(self):
        self.events = []

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

    def rollback(self, changes, *, io):
        for change in reversed(changes):
            self.events.append(f"rollback:{change.prepared.pool}")
