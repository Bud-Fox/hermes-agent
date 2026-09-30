"""Reversible, catalog-last migration transactions for fleet authority."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from hermes_cli.fleet_catalog import canonical_profile_config_paths, catalog_path, fleet_root


@dataclass(frozen=True)
class Transaction:
    transaction_id: str
    bundle_path: Path


@dataclass(frozen=True)
class Receipt:
    transaction_id: str
    status: str
    bundle_path: Path


def _transactions_root() -> Path:
    return fleet_root() / "backups" / "fleet-catalog"


def _sha(data: bytes | None) -> str | None:
    return hashlib.sha256(data).hexdigest() if data is not None else None


def _atomic_write_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        os.write(fd, data)
        os.fchmod(fd, mode)
        os.fsync(fd)
        os.close(fd); fd = -1
        os.replace(temp_name, path)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_bytes(path, (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode(), 0o600)


def _snapshot(path: Path, files_dir: Path, index: int) -> dict[str, Any]:
    existed = path.exists()
    data = path.read_bytes() if existed else b""
    payload = files_dir / f"{index:04d}.bin"
    if existed:
        _atomic_write_bytes(payload, data, 0o600)
    return {"path": str(path), "existed": existed,
            "mode": stat.S_IMODE(path.stat().st_mode) if existed else None,
            "payload": str(payload.relative_to(files_dir.parent)) if existed else None,
            "sha256": _sha(data) if existed else None}


def _payload(bundle: Path, record: dict[str, Any]) -> bytes:
    return (bundle / record["payload"]).read_bytes() if record["existed"] else b""


def _after_config(bundle: Path, record: dict[str, Any]) -> bytes:
    raw = yaml.safe_load(_payload(bundle, record).decode()) if record["existed"] else {}
    if raw is None: raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"{record['path']} top level must be a mapping")
    if "providers" not in raw:
        return _payload(bundle, record)
    raw.pop("providers")
    return yaml.safe_dump(raw, sort_keys=False, allow_unicode=True).encode()


def prepare_migration(staged_catalog: bytes | Path | None = None) -> Transaction:
    source = catalog_path() if staged_catalog is None else staged_catalog
    data = Path(source).read_bytes() if isinstance(source, Path) else source
    if data is None:
        raise FileNotFoundError("staged fleet catalog is required")
    raw = yaml.safe_load(data.decode())
    from hermes_cli.fleet_catalog import validate_fleet_catalog_payload
    validate_fleet_catalog_payload(raw, catalog_path())
    txid = f"{time.time_ns()}-{uuid.uuid4().hex[:12]}"
    bundle = _transactions_root() / txid
    files = bundle / "files"; files.mkdir(parents=True, mode=0o700)
    records = [_snapshot(p, files, i) for i, p in enumerate(canonical_profile_config_paths())]
    catalog_record = _snapshot(catalog_path(), files, len(records))
    staged = bundle / "staged.catalog.yaml"; _atomic_write_bytes(staged, data, 0o600)
    manifest = {"version": 1, "transaction_id": txid, "state": "prepared",
                "files": records, "catalog": catalog_record,
                "staged_catalog": staged.name, "staged_sha256": _sha(data)}
    _write_json(bundle / "manifest.json", manifest)
    _write_json(bundle / "hashes.before.json", {r["path"]: r["sha256"] for r in [*records, catalog_record]})
    _write_json(bundle / "hashes.after.json", {r["path"]: _sha(_after_config(bundle, r)) for r in records} | {str(catalog_path()): _sha(data)})
    _write_json(bundle / "receipt.json", {"transaction_id": txid, "status": "prepared", "bundle_path": str(bundle)})
    return Transaction(txid, bundle)


def _load_bundle(txid: str):
    if not txid or Path(txid).name != txid:
        raise ValueError(f"invalid transaction id: {txid!r}")
    bundle = _transactions_root() / txid
    return bundle, json.loads((bundle / "manifest.json").read_text()), json.loads((bundle / "receipt.json").read_text())


def _restore_one(bundle: Path, record: dict[str, Any]) -> None:
    path = Path(record["path"])
    if record["existed"]:
        _atomic_write_bytes(path, _payload(bundle, record), int(record["mode"]))
    else:
        path.unlink(missing_ok=True)


def _verify(path: Path, record: dict[str, Any]) -> None:
    if not record["existed"]:
        if path.exists(): raise RuntimeError(f"rollback failed to remove {path}")
        return
    if _sha(path.read_bytes()) != record["sha256"] or stat.S_IMODE(path.stat().st_mode) != record["mode"]:
        raise RuntimeError(f"rollback verification failed for {path}")


def _invalidate_model_caches() -> None:
    from hermes_cli.models import clear_provider_models_cache
    from hermes_cli.profiles import _iter_named_profile_dirs, _get_default_hermes_home
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    for home in [_get_default_hermes_home(), *_iter_named_profile_dirs()]:
        tok = set_hermes_home_override(home)
        try:
            clear_provider_models_cache()
        finally:
            reset_hermes_home_override(tok)


def _read_effective_profile(path: Path) -> dict[str, Any]:
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    token = set_hermes_home_override(path.parent)
    try:
        from hermes_cli.config_effective import load_user_config_effective
        return load_user_config_effective(path, fail_closed=True)
    finally:
        reset_hermes_home_override(token)


def _picker_payload(config: dict[str, Any], *, profile_home: Path | None = None) -> dict[str, Any]:
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    token = set_hermes_home_override(profile_home) if profile_home is not None else None
    try:
        from hermes_cli.config import get_compatible_custom_providers, stringify_provider_map
        from hermes_cli.inventory import ConfigContext, build_model_options_payload
        model_cfg = config.get("model", {})
        if isinstance(model_cfg, dict):
            current_model = str(model_cfg.get("default", model_cfg.get("name", "")) or "")
            current_provider = str(model_cfg.get("provider", "") or "")
            current_base_url = str(model_cfg.get("base_url", "") or "")
        else:
            current_model, current_provider, current_base_url = str(model_cfg or ""), "", ""
        excluded = config.get("model_catalog", {}).get("excluded_providers") or []
        context = ConfigContext(
            current_provider=current_provider, current_model=current_model,
            current_base_url=current_base_url,
            user_providers=stringify_provider_map(config.get("providers")),
            custom_providers=get_compatible_custom_providers(config),
            excluded_providers=excluded if isinstance(excluded, list) else [],
        )
        return build_model_options_payload(context, explicit_only=True)
    finally:
        if token is not None:
            reset_hermes_home_override(token)


def _picker_inventory(payload: dict[str, Any]) -> dict[str, list[str]]:
    return {
        str(row.get("provider_id") or row.get("slug") or ""): [str(model) for model in row.get("models") or []]
        for row in payload.get("providers", []) if isinstance(row, dict)
    }


def _owner_route_parity(configs: list[dict[str, Any]]) -> bool:
    def routes(cfg: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
        providers = cfg.get("providers") or {}
        return {str(owner): (value.get("base_url"), value.get("api_mode"))
                for owner, value in providers.items() if isinstance(value, dict)}
    return bool(configs) and len({json.dumps(routes(cfg), sort_keys=True) for cfg in configs}) == 1


def commit_transaction(transaction_id: str, *, invalidate_caches: Callable[[], None] = _invalidate_model_caches,
                       read_model_options: Callable[[Path], Any] = _read_effective_profile) -> Receipt:
    bundle, manifest, receipt = _load_bundle(transaction_id)
    if receipt["status"] == "committed": return Receipt(transaction_id, "committed", bundle)
    if receipt["status"] != "prepared": raise ValueError(f"transaction {transaction_id} is {receipt['status']}")
    changed: list[dict[str, Any]] = []
    try:
        for record in manifest["files"]:
            data = _after_config(bundle, record)
            _atomic_write_bytes(Path(record["path"]), data, int(record["mode"] or 0o600)); changed.append(record)
        staged = (bundle / manifest["staged_catalog"]).read_bytes()
        if _sha(staged) != manifest["staged_sha256"]: raise RuntimeError("staged catalog hash mismatch")
        cat = manifest["catalog"]
        _atomic_write_bytes(catalog_path(), staged, int(cat["mode"] or 0o600))
        invalidate_caches()
        profile_paths = [Path(record["path"]) for record in manifest["files"]]
        effective = [read_model_options(path) for path in profile_paths]
        expected = json.loads((bundle / "hashes.after.json").read_text())
        actual = {r["path"]: _sha(Path(r["path"]).read_bytes()) for r in manifest["files"]}
        actual[str(catalog_path())] = _sha(catalog_path().read_bytes())
        modes_match = all(stat.S_IMODE(Path(r["path"]).stat().st_mode) == int(r["mode"] or 0o600) for r in manifest["files"])
        from hermes_cli.fleet_catalog import apply_fleet_catalog, static_catalog_hash
        hashes = [static_catalog_hash(cfg) for cfg in effective]
        expected_catalog_hash = static_catalog_hash(apply_fleet_catalog({}))
        picker_payloads = []
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override
        for cfg, path in zip(effective, profile_paths):
            token = set_hermes_home_override(path.parent)
            try:
                picker_payloads.append(_picker_payload(cfg, profile_home=path.parent))
            finally:
                reset_hermes_home_override(token)
        picker_inventories = [_picker_inventory(payload) for payload in picker_payloads]
        expected_picker = _picker_inventory(_picker_payload(apply_fleet_catalog({})))
        picker_parity = (bool(picker_inventories)
                         and all(value == expected_picker for value in picker_inventories))
        owner_route_parity = _owner_route_parity(effective)
        verification = {"all_expected_hashes_match": actual == expected,
                        "all_expected_modes_match": modes_match,
                        "all_profile_static_hashes_equal": bool(hashes) and all(value == expected_catalog_hash for value in hashes),
                        "all_profile_picker_inventories_equal": picker_parity,
                        "all_profile_owner_routes_equal": owner_route_parity,
                        "all_profiles_owner_route_picker_equal": owner_route_parity and picker_parity,
                        "actual_hashes": actual, "profile_static_hashes": hashes,
                        "profile_picker_inventories": picker_inventories}
        _write_json(bundle / "verification.json", verification)
        required = ("all_expected_hashes_match", "all_expected_modes_match",
                    "all_profile_static_hashes_equal", "all_profile_picker_inventories_equal",
                    "all_profile_owner_routes_equal", "all_profiles_owner_route_picker_equal")
        if not all(verification[key] for key in required):
            raise RuntimeError("post-commit fleet catalog verification failed")
    except Exception as exc:
        failures = []
        for record in reversed([*changed, manifest["catalog"]]):
            try: _restore_one(bundle, record)
            except Exception as rollback_exc: failures.append({"path": record["path"], "error": str(rollback_exc)})
        _write_json(bundle / "receipt.json", {"transaction_id": transaction_id, "status": "compensation_failed" if failures else "compensated", "failures": failures})
        if failures: raise RuntimeError(f"commit failed: {exc}; compensation failures: {failures}") from exc
        raise
    _write_json(bundle / "receipt.json", {"transaction_id": transaction_id, "status": "committed", "bundle_path": str(bundle)})
    return Receipt(transaction_id, "committed", bundle)


def _last_committed_id() -> str | None:
    root = _transactions_root()
    if not root.is_dir(): return None
    ids=[]
    for b in root.iterdir():
        try: r=json.loads((b/"receipt.json").read_text())
        except Exception: continue
        if r.get("status")=="committed": ids.append(b.name)
    return max(ids) if ids else None


def rollback_transaction(transaction_id: str = "last", *, invalidate_caches: Callable[[], None] = _invalidate_model_caches,
                         read_model_options: Callable[[Path], Any] = _read_effective_profile) -> Receipt:
    if transaction_id == "last":
        transaction_id = _last_committed_id() or ""
        if not transaction_id: return Receipt("last", "already_rolled_back", _transactions_root())
    bundle, manifest, receipt = _load_bundle(transaction_id)
    if receipt["status"] in {"rolled_back", "compensated"}: return Receipt(transaction_id, "already_rolled_back", bundle)
    if receipt["status"] != "committed": raise ValueError(f"transaction {transaction_id} is {receipt['status']}")
    failures=[]
    for record in reversed([*manifest["files"], manifest["catalog"]]):
        try: _restore_one(bundle, record); _verify(Path(record["path"]), record)
        except Exception as exc: failures.append({"path": record["path"], "error": str(exc)})
    try: invalidate_caches()
    except Exception as exc: failures.append({"hook": "invalidate_caches", "error": str(exc)})
    for record in manifest["files"]:
        try: read_model_options(Path(record["path"]))
        except Exception as exc: failures.append({"path": record["path"], "hook": "model_options", "error": str(exc)})
    status = "rollback_failed" if failures else "rolled_back"
    _write_json(bundle / "receipt.json", {"transaction_id": transaction_id, "status": status, "failures": failures})
    if failures: raise RuntimeError(f"rollback failures: {failures}")
    return Receipt(transaction_id, status, bundle)
