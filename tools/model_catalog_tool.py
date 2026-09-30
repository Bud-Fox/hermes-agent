"""Built-in transactional model catalog management tool."""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from hermes_cli.fleet_catalog_adapters import (
    AdapterIO, AppliedChange, CodexPoolAdapter, ContaboOpenAIAdapter,
    PoolOrchestrator, PreparedChange,
)

_ACTIONS = ["plan_add", "apply", "rollback", "status"]
_SECRET_KEYS = {"api_key", "authorization", "credential", "password", "secret", "token"}

MODEL_CATALOG_SCHEMA = {
    "type": "function",
    "function": {
        "name": "model_catalog",
        "description": "Plan, apply, roll back, or inspect an atomic literal model addition across provider pools.",
        "x-hermes-approval": {"actions": ["apply", "rollback"]},
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": _ACTIONS},
                "model": {"type": "string", "description": "Literal model ID; never normalized."},
                "pools": {"type": "array", "items": {"type": "string", "enum": ["openai-codex", "contabo-openai"]}},
                "transaction_id": {"type": "string", "default": "last"},
            },
            "required": ["action"],
        },
    },
}


def _plans_root() -> Path:
    from hermes_cli.fleet_catalog import fleet_root
    return fleet_root() / "backups" / "model-catalog"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    from hermes_cli.fleet_catalog_transactions import _atomic_write_bytes
    _atomic_write_bytes(path, (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode(), 0o600)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if key.lower() in _SECRET_KEYS else _redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _build_orchestrator() -> PoolOrchestrator:
    adapters = [CodexPoolAdapter(), ContaboOpenAIAdapter()]
    return PoolOrchestrator({adapter.pool: adapter for adapter in adapters})


def _last_id() -> str | None:
    root = _plans_root()
    if not root.is_dir():
        return None
    candidates = [path.name for path in root.iterdir() if (path / "plan.json").is_file()]
    return max(candidates) if candidates else None


def _resolve_id(transaction_id: str) -> str:
    if transaction_id == "last":
        transaction_id = _last_id() or ""
    if not transaction_id or Path(transaction_id).name != transaction_id:
        raise ValueError("valid transaction_id required")
    return transaction_id


def _load(transaction_id: str) -> tuple[Path, dict[str, Any]]:
    txid = _resolve_id(transaction_id)
    bundle = _plans_root() / txid
    return bundle, json.loads((bundle / "plan.json").read_text(encoding="utf-8"))


def _request_approval(action: str, transaction_id: str) -> bool:
    from tools.approval import request_tool_approval
    verdict = request_tool_approval(
        "model_catalog", f"{action} model catalog transaction {transaction_id}",
        rule_key=f"model_catalog:{action}")
    return verdict.get("approved") is True


def _prepared(plan: dict[str, Any]) -> list[PreparedChange]:
    return [PreparedChange(item["pool"], plan["model"], item.get("metadata", {})) for item in plan["changes"]]


def _applied(plan: dict[str, Any]) -> list[AppliedChange]:
    prepared = {item.pool: item for item in _prepared(plan)}
    return [AppliedChange(prepared[item["pool"]], item.get("rollback_data", {})) for item in plan.get("applied", [])]


def _journal_applied(bundle: Path, plan: dict[str, Any]) -> list[AppliedChange]:
    path = bundle / "journal.json"
    if not path.is_file(): return []
    journal = json.loads(path.read_text(encoding="utf-8"))
    prepared = {item.pool: item for item in _prepared(plan)}
    out = []
    for step in journal.get("steps", []):
        if step.get("state") != "applied": continue
        rollback = json.loads((bundle / step["rollback_snapshot"]).read_text(encoding="utf-8"))
        out.append(AppliedChange(prepared[step["pool"]], rollback))
    return out


def model_catalog(action: str, model: str | None = None, pools: list[str] | None = None,
                  transaction_id: str = "last", *, io: AdapterIO | None = None,
                  require_approval: bool = True) -> str:
    io = io or AdapterIO()
    orchestrator = _build_orchestrator()
    try:
        if action == "plan_add":
            if model is None:
                raise ValueError("plan_add requires model")
            changes = orchestrator.prepare(model, pools or [], io=io)
            txid = f"{time.time_ns()}-{uuid.uuid4().hex[:12]}"
            bundle = _plans_root() / txid
            bundle.mkdir(parents=True, exist_ok=False)
            plan = {
                "version": 1, "transaction_id": txid, "status": "prepared", "model": model,
                "pools": list(pools or []), "approval_required": True,
                "changes": [{"pool": item.pool, "metadata": _redact(item.metadata)} for item in changes],
            }
            _atomic_json(bundle / "plan.json", plan)
            return json.dumps(_redact(plan), ensure_ascii=False)

        bundle, plan = _load(transaction_id)
        if action == "status":
            return json.dumps(_redact(plan), ensure_ascii=False)
        if action not in {"apply", "rollback"}:
            raise ValueError(f"unsupported action: {action}")
        if require_approval and not _request_approval(action, plan["transaction_id"]):
            return json.dumps({"success": False, "status": "approval_denied", "transaction_id": plan["transaction_id"]})
        if action == "apply":
            if plan["status"] == "committed":
                return json.dumps(_redact(plan), ensure_ascii=False)
            if plan["status"] != "prepared":
                raise ValueError(f"transaction is {plan['status']}")
            changes = _prepared(plan)
            snapshot = io.catalog_bytes()
            from hermes_cli.fleet_catalog_transactions import _atomic_write_bytes
            _atomic_write_bytes(bundle / "catalog.before", snapshot, 0o600)
            journal = {"version": 1, "catalog_snapshot": "catalog.before", "steps": []}
            _atomic_json(bundle / "journal.json", journal)
            plan["status"] = "applying"
            _atomic_json(bundle / "plan.json", plan)
            applied = []
            for index, change in enumerate(changes):
                step = {"pool": change.pool, "state": "applying"}
                journal["steps"].append(step); _atomic_json(bundle / "journal.json", journal)
                item = orchestrator.commit_one(change, io=io)
                rollback_name = f"rollback-{index:04d}.json"
                _atomic_json(bundle / rollback_name, item.rollback_data)
                step.update({"state": "applied", "rollback_snapshot": rollback_name})
                _atomic_json(bundle / "journal.json", journal)
                applied.append(item)
            orchestrator.activate_catalog(changes, io=io, snapshot=snapshot)
            journal["catalog_state"] = "applied"; _atomic_json(bundle / "journal.json", journal)
            plan["applied_pools"] = [item.prepared.pool for item in applied]
            plan["status"] = "committed"
        else:
            if plan["status"] == "rolled_back":
                return json.dumps({"success": True, "status": "already_rolled_back", "transaction_id": plan["transaction_id"]})
            if plan["status"] not in {"committed", "applying"}:
                raise ValueError(f"transaction is {plan['status']}")
            orchestrator.rollback(_journal_applied(bundle, plan) or _applied(plan), io=io)
            journal_path = bundle / "journal.json"
            if journal_path.is_file():
                journal = json.loads(journal_path.read_text(encoding="utf-8"))
                snapshot = bundle / journal.get("catalog_snapshot", "catalog.before")
                if snapshot.is_file(): io.write_catalog_bytes(snapshot.read_bytes())
            plan["status"] = "rolled_back"
        _atomic_json(bundle / "plan.json", plan)
        return json.dumps(_redact(plan), ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"success": False, "status": "error", "error": str(exc),
                           "transaction_id": transaction_id}, ensure_ascii=False)


def _handler(args: dict[str, Any], **_kwargs: Any) -> str:
    return model_catalog(
        action=args.get("action", ""), model=args.get("model"), pools=args.get("pools"),
        transaction_id=args.get("transaction_id", "last"))


from tools.registry import registry  # noqa: E402  (normal discovery scans this literal registration)

registry.register(name="model_catalog", toolset="model_catalog", schema=MODEL_CATALOG_SCHEMA,
                  handler=_handler, emoji="📚")
