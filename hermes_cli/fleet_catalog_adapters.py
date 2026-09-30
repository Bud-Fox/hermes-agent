"""Transactional adapters for adding literal model IDs to provider pools."""
from __future__ import annotations

import copy
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class PreparedChange:
    pool: str
    model: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class AppliedChange:
    prepared: PreparedChange
    rollback_data: dict[str, Any]


class AdapterIO:
    """Injectable adapter boundary. Tests replace every side effect."""

    def provider_model_ids(self, pool: str) -> list[str]:
        from hermes_cli.models import provider_model_ids
        return provider_model_ids(pool, force_refresh=True)

    def validate_requested_model(self, model: str, pool: str) -> dict[str, Any]:
        from hermes_cli.models_validate import validate_requested_model
        return validate_requested_model(model, pool)

    def probe_model(self, pool: str, model: str, kind: str) -> bool:
        raise RuntimeError(f"live {kind} probe requires an injected runner")

    def run(self, command: Sequence[str], *, cwd: Path | None = None, timeout: int = 60) -> dict[str, Any]:
        result = subprocess.run(list(command), cwd=cwd, timeout=timeout, text=True, capture_output=True, check=False)
        return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}

    def read_json(self, path: Path) -> Any:
        import json
        return json.loads(path.read_text(encoding="utf-8"))

    def write_json(self, path: Path, value: Any) -> None:
        import json
        from hermes_cli.fleet_catalog_transactions import _atomic_write_bytes
        data = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        _atomic_write_bytes(path, data, path.stat().st_mode & 0o777 if path.exists() else 0o600)

    def catalog_bytes(self) -> bytes:
        from hermes_cli.fleet_catalog import catalog_path
        return catalog_path().read_bytes()

    def write_catalog_bytes(self, value: bytes) -> None:
        from hermes_cli.fleet_catalog import catalog_path
        from hermes_cli.fleet_catalog_transactions import _atomic_write_bytes
        path = catalog_path()
        _atomic_write_bytes(path, value, path.stat().st_mode & 0o777)


class PoolAdapter:
    pool: str

    def prepare_add(self, model: str, *, io: AdapterIO) -> PreparedChange:
        raise NotImplementedError

    def commit(self, change: PreparedChange, *, io: AdapterIO) -> AppliedChange:
        raise NotImplementedError

    def rollback(self, applied: AppliedChange, *, io: AdapterIO) -> None:
        raise NotImplementedError


class CodexPoolAdapter(PoolAdapter):
    pool = "openai-codex"

    def prepare_add(self, model: str, *, io: AdapterIO) -> PreparedChange:
        if not isinstance(model, str) or not model:
            raise ValueError("model must be a non-empty literal string")
        listed = model in io.provider_model_ids(self.pool)
        if listed:
            verdict = io.validate_requested_model(model, self.pool)
            if not verdict.get("accepted"):
                raise RuntimeError("listed model failed validation")
            availability = "listed"
        else:
            for kind in ("streaming", "text", "tool"):
                if not io.probe_model(self.pool, model, kind):
                    raise RuntimeError(f"{kind} probe failed for literal model")
            availability = "hidden_but_live"
        return PreparedChange(self.pool, model, {"availability": availability, "support_table_patch": False})

    def commit(self, change: PreparedChange, *, io: AdapterIO) -> AppliedChange:
        import yaml

        before = io.catalog_bytes()
        raw = yaml.safe_load(before.decode("utf-8"))
        provider = raw.get("providers", {}).get(self.pool)
        if not isinstance(provider, dict) or not isinstance(provider.get("models"), list):
            raise RuntimeError("fleet catalog lacks openai-codex models list")
        if change.model not in provider["models"]:
            provider["models"].append(change.model)
            io.write_catalog_bytes(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True).encode())
        return AppliedChange(change, {"catalog_before_hex": before.hex()})

    def rollback(self, applied: AppliedChange, *, io: AdapterIO) -> None:
        before = applied.rollback_data.get("catalog_before_hex")
        if before:
            io.write_catalog_bytes(bytes.fromhex(before))


class ContaboOpenAIAdapter(PoolAdapter):
    pool = "contabo-openai"
    REQUIRED_ROOT = Path("/Users/user/contabo-router")
    REQUIRED_SCRIPT = REQUIRED_ROOT / "deploy_router.sh"

    def __init__(self, *, router_root: Path = REQUIRED_ROOT, deploy_script: Path = REQUIRED_SCRIPT):
        self.router_root = Path(router_root)
        self.deploy_script = Path(deploy_script)
        if self.deploy_script != self.router_root / "deploy_router.sh":
            raise ValueError("Contabo deployment must use the router root deploy_router.sh")
        if self.router_root == self.REQUIRED_ROOT and self.deploy_script != self.REQUIRED_SCRIPT:
            raise ValueError(f"Contabo deployment must use {self.REQUIRED_SCRIPT}")
        self.models_path = self.router_root / "app/models.json"

    def prepare_add(self, model: str, *, io: AdapterIO) -> PreparedChange:
        if not isinstance(model, str) or not model:
            raise ValueError("model must be a non-empty literal string")
        probes = ["text", "tool", "streaming", "reasoning"]
        for kind in probes:
            if not io.probe_model(self.pool, model, kind):
                raise RuntimeError(f"{kind} probe failed for literal model")
        return PreparedChange(self.pool, model, {"probes": probes})

    @staticmethod
    def _model_list(payload: Any) -> list[str]:
        if isinstance(payload, dict) and isinstance(payload.get("models"), list):
            return payload["models"]
        raise RuntimeError("router app/models.json must contain a models list")

    def _run_checked(self, io: AdapterIO, command: Sequence[str], timeout: int = 60) -> None:
        result = io.run(command, cwd=self.router_root, timeout=timeout)
        if int(result.get("returncode", 1)) != 0:
            raise RuntimeError("router command failed")

    def commit(self, change: PreparedChange, *, io: AdapterIO) -> AppliedChange:
        before = copy.deepcopy(io.read_json(self.models_path))
        after = copy.deepcopy(before)
        models = self._model_list(after)
        if change.model not in models:
            models.append(change.model)
        io.write_json(self.models_path, after)
        try:
            self._run_checked(io, ("python", "-m", "pytest", "-q", "tests"), 120)
            self._run_checked(io, (str(self.deploy_script),), 300)
            for kind in ("manifest", "models", "canary"):
                if not io.probe_model(self.pool, change.model, kind):
                    raise RuntimeError(f"post-deploy {kind} verification failed")
        except Exception:
            io.write_json(self.models_path, before)
            raise
        return AppliedChange(change, {"models_before": before})

    def rollback(self, applied: AppliedChange, *, io: AdapterIO) -> None:
        before = applied.rollback_data.get("models_before")
        if before is not None:
            io.write_json(self.models_path, before)


class CatalogActivatingAdapter(PoolAdapter):
    """Activate a prepared pool's fleet row last, after its underlying adapter succeeds."""

    def __init__(self, inner: PoolAdapter):
        self.inner = inner
        self.pool = inner.pool

    def prepare_add(self, model: str, *, io: AdapterIO) -> PreparedChange:
        return self.inner.prepare_add(model, io=io)

    def commit(self, change: PreparedChange, *, io: AdapterIO) -> AppliedChange:
        underlying = self.inner.commit(change, io=io)
        before = io.catalog_bytes()
        import yaml
        raw = yaml.safe_load(before.decode("utf-8"))
        provider = raw.get("providers", {}).get(self.pool)
        if not isinstance(provider, dict) or not isinstance(provider.get("models"), list):
            self.inner.rollback(underlying, io=io)
            raise RuntimeError(f"fleet catalog lacks {self.pool} models list")
        if change.model not in provider["models"]:
            provider["models"].append(change.model)
            io.write_catalog_bytes(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True).encode())
        return AppliedChange(change, {"underlying": underlying.rollback_data, "catalog_before_hex": before.hex()})

    def rollback(self, applied: AppliedChange, *, io: AdapterIO) -> None:
        before = applied.rollback_data.get("catalog_before_hex")
        if before:
            io.write_catalog_bytes(bytes.fromhex(before))
        self.inner.rollback(AppliedChange(applied.prepared, applied.rollback_data.get("underlying", {})), io=io)


class PoolOrchestrator:
    def __init__(self, adapters: Mapping[str, PoolAdapter]):
        self.adapters = dict(adapters)

    def prepare(self, model: str, pools: Sequence[str], *, io: AdapterIO) -> list[PreparedChange]:
        if not pools:
            raise ValueError("at least one pool is required")
        if len(pools) != len(set(pools)):
            raise ValueError("duplicate pools are not allowed")
        unknown = [pool for pool in pools if pool not in self.adapters]
        if unknown:
            raise ValueError(f"unsupported pools: {unknown}")
        return [self.adapters[pool].prepare_add(model, io=io) for pool in pools]

    def commit(self, changes: Sequence[PreparedChange], *, io: AdapterIO) -> list[AppliedChange]:
        applied: list[AppliedChange] = []
        try:
            for change in changes:
                applied.append(self.adapters[change.pool].commit(change, io=io))
        except Exception:
            for item in reversed(applied):
                self.adapters[item.prepared.pool].rollback(item, io=io)
            raise
        return applied

    def rollback(self, changes: Sequence[AppliedChange], *, io: AdapterIO) -> None:
        for item in reversed(changes):
            self.adapters[item.prepared.pool].rollback(item, io=io)
