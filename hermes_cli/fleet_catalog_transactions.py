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


def commit_transaction(transaction_id: str, *, invalidate_caches: Callable[[], None] = lambda: None,
                       read_model_options: Callable[[Path], Any] = lambda _p: None) -> Receipt:
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
        for record in manifest["files"]: read_model_options(Path(record["path"]))
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


def rollback_transaction(transaction_id: str = "last", *, invalidate_caches: Callable[[], None] = lambda: None,
                         read_model_options: Callable[[Path], Any] = lambda _p: None) -> Receipt:
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
