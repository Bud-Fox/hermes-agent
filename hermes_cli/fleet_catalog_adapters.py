"""Transactional adapters for adding literal model IDs to provider pools."""
from __future__ import annotations

import copy
import json
import stat
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
        if pool == "openai-codex":
            from hermes_cli.codex_models import probe_codex_model
            return bool(probe_codex_model(model, kind=kind))
        if pool == "contabo-openai":
            from hermes_cli.models_validate import probe_openai_compatible_model
            return bool(probe_openai_compatible_model(model, kind=kind, provider=pool))
        raise ValueError(f"unsupported probe pool: {pool}")

    def run(self, command: Sequence[str], *, cwd: Path | None = None, timeout: int = 60) -> dict[str, Any]:
        result = subprocess.run(list(command), cwd=cwd, timeout=timeout, text=True, capture_output=True, check=False)
        return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}

    def read_json(self, path: Path) -> Any:
        import json
        return json.loads(path.read_text(encoding="utf-8"))

    def read_bytes(self, path: Path) -> bytes:
        return path.read_bytes()

    def write_bytes(self, path: Path, value: bytes, mode: int | None = None) -> None:
        from hermes_cli.fleet_catalog_transactions import _atomic_write_bytes
        _atomic_write_bytes(path, value, mode if mode is not None else 0o600)

    def file_mode(self, path: Path) -> int:
        return stat.S_IMODE(path.stat().st_mode)

    def write_json(self, path: Path, value: Any) -> None:
        import json
        from hermes_cli.fleet_catalog_transactions import _atomic_write_bytes
        data = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        _atomic_write_bytes(path, data, path.stat().st_mode & 0o777 if path.exists() else 0o600)

    def catalog_bytes(self) -> bytes:
        from hermes_cli.fleet_catalog import catalog_path
        return catalog_path().read_bytes()

    def write_catalog_bytes(self, value: bytes, mode: int | None = None) -> None:
        from hermes_cli.fleet_catalog import catalog_path
        from hermes_cli.fleet_catalog_transactions import _atomic_write_bytes
        path = catalog_path()
        _atomic_write_bytes(path, value, mode if mode is not None else (path.stat().st_mode & 0o777 if path.exists() else 0o600))


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
        # Codex has no mutable pool-local manifest. Catalog activation is owned
        # exclusively by PoolOrchestrator after every pool commit succeeds.
        return AppliedChange(change, {})

    def rollback(self, applied: AppliedChange, *, io: AdapterIO) -> None:
        return None


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
    def _model_list(payload: Any) -> list[dict[str, Any]]:
        models = payload.get("openai", {}).get("models") if isinstance(payload, dict) else None
        if isinstance(models, list) and all(isinstance(item, dict) for item in models):
            return models
        raise RuntimeError("router app/models.json must contain openai.models model-entry mappings")

    def _run_checked(self, io: AdapterIO, command: Sequence[str], timeout: int = 60) -> None:
        result = io.run(command, cwd=self.router_root, timeout=timeout)
        if int(result.get("returncode", 1)) != 0:
            raise RuntimeError("router command failed")

    def commit(self, change: PreparedChange, *, io: AdapterIO) -> AppliedChange:
        before = io.read_bytes(self.models_path)
        before_mode = io.file_mode(self.models_path)
        after = json.loads(before.decode("utf-8"))
        models = self._model_list(after)
        if not any(item.get("id") == change.model for item in models):
            metadata = change.metadata.get("model_entry") if isinstance(change.metadata, dict) else None
            entry = {"id": change.model, "name": change.model}
            if isinstance(metadata, dict):
                for key in ("supports_tools", "supports_streaming", "supports_reasoning"):
                    if isinstance(metadata.get(key), bool): entry[key] = metadata[key]
            elif models and isinstance(models[0].get("supports_tools"), bool):
                entry["supports_tools"] = bool(models[0]["supports_tools"])
            models.append(entry)
        io.write_bytes(self.models_path, (json.dumps(after, indent=2, ensure_ascii=False) + "\n").encode(), before_mode)
        try:
            self._run_checked(io, ("python", "-m", "pytest", "-q", "tests"), 120)
            self._run_checked(io, (str(self.deploy_script),), 300)
            for kind in ("manifest", "models", "canary"):
                if not io.probe_model(self.pool, change.model, kind):
                    raise RuntimeError(f"post-deploy {kind} verification failed")
        except Exception:
            io.write_bytes(self.models_path, before, before_mode)
            self._run_checked(io, (str(self.deploy_script),), 300)
            self._verify_restore(io, change.model, before, before_mode)
            raise
        return AppliedChange(change, {"models_before_bytes": before, "models_before_mode": before_mode})

    def rollback(self, applied: AppliedChange, *, io: AdapterIO) -> None:
        before = applied.rollback_data.get("models_before_bytes")
        if before is not None:
            mode = int(applied.rollback_data["models_before_mode"])
            io.write_bytes(self.models_path, before, mode)
            self._run_checked(io, (str(self.deploy_script),), 300)
            self._verify_restore(io, applied.prepared.model, before, mode)

    def _verify_restore(self, io: AdapterIO, candidate: str, before: bytes, mode: int) -> None:
        if io.read_bytes(self.models_path) != before or io.file_mode(self.models_path) != mode:
            raise RuntimeError("models.json byte/mode restoration verification failed")
        restored = json.loads(before.decode("utf-8"))
        ids = [item.get("id") for item in self._model_list(restored)]
        if not ids:
            raise RuntimeError("no pre-existing model available for rollback canary")
        if candidate not in ids:
            for kind in ("manifest", "models"):
                if io.probe_model(self.pool, candidate, kind):
                    raise RuntimeError(f"rollback {kind} still serves new candidate")
        if not io.probe_model(self.pool, str(ids[0]), "canary"):
            raise RuntimeError("rollback pre-existing model canary failed")


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
        # Pool adapters stage underlying state only.  The fleet catalog is a
        # single activation point after every pool succeeds.
        before = io.catalog_bytes()
        applied: list[AppliedChange] = []
        try:
            for change in changes:
                applied.append(self.adapters[change.pool].commit(change, io=io))
            self.activate_catalog(changes, io=io, snapshot=before)
        except Exception as exc:
            failures = []
            for item in reversed(applied):
                try: self.adapters[item.prepared.pool].rollback(item, io=io)
                except Exception as rollback_exc: failures.append(f"{item.prepared.pool}: {rollback_exc}")
            try: io.write_catalog_bytes(before)
            except Exception as rollback_exc: failures.append(f"catalog: {rollback_exc}")
            if failures: raise RuntimeError(f"commit failed: {exc}; compensation failures: {failures}") from exc
            raise
        return applied

    def commit_one(self, change: PreparedChange, *, io: AdapterIO) -> AppliedChange:
        return self.adapters[change.pool].commit(change, io=io)

    def activate_catalog(self, changes: Sequence[PreparedChange], *, io: AdapterIO,
                         snapshot: bytes | None = None) -> None:
        import yaml
        before = io.catalog_bytes() if snapshot is None else snapshot
        raw = yaml.safe_load(before.decode())
        for change in changes:
            provider = raw.get("providers", {}).get(change.pool)
            if not isinstance(provider, dict) or not isinstance(provider.get("models"), list):
                raise RuntimeError(f"fleet catalog lacks {change.pool} models list")
            if change.model not in provider["models"]: provider["models"].append(change.model)
        io.write_catalog_bytes(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True).encode())

    def rollback(self, changes: Sequence[AppliedChange], *, io: AdapterIO) -> None:
        failures = []
        for item in reversed(changes):
            try: self.adapters[item.prepared.pool].rollback(item, io=io)
            except Exception as exc: failures.append(f"{item.prepared.pool}: {exc}")
        if failures: raise RuntimeError(f"rollback failures: {failures}")
