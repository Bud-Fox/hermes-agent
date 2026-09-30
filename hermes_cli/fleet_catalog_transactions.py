"""Reversible migration transactions for fleet catalog authority."""
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
from typing import Any

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


def _atomic_write_bytes(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        os.write(fd, data)
        os.fchmod(fd, mode)
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(temp_name, path)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _record(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "existed": False, "mode": None, "bytes_hex": None}
    data = path.read_bytes()
    return {
        "path": str(path), "existed": True,
        "mode": stat.S_IMODE(path.stat().st_mode), "bytes_hex": data.hex(),
    }


def _after_config(record: dict[str, Any]) -> bytes:
    raw = yaml.safe_load(bytes.fromhex(record["bytes_hex"]).decode("utf-8")) if record["existed"] else {}
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"{record['path']} top level must be a mapping")
    raw.pop("providers", None)
    return yaml.safe_dump(raw, sort_keys=False, allow_unicode=True).encode("utf-8")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_bytes(
        path, (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8"), 0o600)


def prepare_migration() -> Transaction:
    catalog = catalog_path()
    if not catalog.is_file():
        raise FileNotFoundError(f"fleet catalog not found: {catalog}")
    # Parse and validate before creating a transaction bundle.
    from hermes_cli.fleet_catalog import load_fleet_catalog
    load_fleet_catalog()

    txid = f"{time.time_ns()}-{uuid.uuid4().hex[:12]}"
    bundle = _transactions_root() / txid
    bundle.mkdir(parents=True, exist_ok=False)
    records = [_record(path) for path in canonical_profile_config_paths()]
    after = {record["path"]: _sha(_after_config(record)) for record in records}
    manifest = {
        "version": 1, "transaction_id": txid, "state": "prepared",
        "catalog_path": str(catalog), "files": records,
    }
    _write_json(bundle / "manifest.json", manifest)
    _write_json(bundle / "hashes.before.json", {
        record["path"]: _sha(bytes.fromhex(record["bytes_hex"])) if record["existed"] else None
        for record in records
    })
    _write_json(bundle / "hashes.after.json", after)
    _write_json(bundle / "receipt.json", {"transaction_id": txid, "status": "prepared"})
    return Transaction(txid, bundle)


def _load_bundle(transaction_id: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    bundle = _transactions_root() / transaction_id
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    receipt = json.loads((bundle / "receipt.json").read_text(encoding="utf-8"))
    return bundle, manifest, receipt


def _restore(records: list[dict[str, Any]]) -> None:
    for record in reversed(records):
        path = Path(record["path"])
        if record["existed"]:
            _atomic_write_bytes(path, bytes.fromhex(record["bytes_hex"]), int(record["mode"]))
        else:
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def commit_transaction(transaction_id: str) -> Receipt:
    bundle, manifest, receipt = _load_bundle(transaction_id)
    if receipt["status"] == "committed":
        return Receipt(transaction_id, "committed", bundle)
    if receipt["status"] != "prepared":
        raise ValueError(f"transaction {transaction_id} is {receipt['status']}")
    records = manifest["files"]
    try:
        for record in records:
            _atomic_write_bytes(Path(record["path"]), _after_config(record), int(record["mode"] or 0o600))
        # Catalog is already staged at its canonical location and becomes authoritative only after
        # all legacy profile mappings have been removed successfully.
        from hermes_cli.fleet_catalog import apply_fleet_catalog, static_catalog_hash
        hashes = []
        for record in records:
            path = Path(record["path"])
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
            hashes.append(static_catalog_hash(apply_fleet_catalog(raw or {})))
        if len(set(hashes)) > 1:
            raise RuntimeError("effective fleet catalog hashes differ after commit")
    except Exception:
        _restore(records)
        _write_json(bundle / "receipt.json", {"transaction_id": transaction_id, "status": "compensated"})
        raise
    _write_json(bundle / "receipt.json", {"transaction_id": transaction_id, "status": "committed"})
    return Receipt(transaction_id, "committed", bundle)


def _last_committed_id() -> str | None:
    root = _transactions_root()
    if not root.is_dir():
        return None
    candidates = []
    for bundle in root.iterdir():
        try:
            receipt = json.loads((bundle / "receipt.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if receipt.get("status") == "committed":
            candidates.append(bundle.name)
    return max(candidates) if candidates else None


def rollback_transaction(transaction_id: str = "last") -> Receipt:
    if transaction_id == "last":
        transaction_id = _last_committed_id() or ""
        if not transaction_id:
            return Receipt("last", "already_rolled_back", _transactions_root())
    bundle, manifest, receipt = _load_bundle(transaction_id)
    if receipt["status"] == "rolled_back":
        return Receipt(transaction_id, "already_rolled_back", bundle)
    if receipt["status"] != "committed":
        if receipt["status"] == "compensated":
            return Receipt(transaction_id, "already_rolled_back", bundle)
        raise ValueError(f"transaction {transaction_id} is {receipt['status']}")
    _restore(manifest["files"])
    for record in manifest["files"]:
        path = Path(record["path"])
        if record["existed"]:
            if _sha(path.read_bytes()) != _sha(bytes.fromhex(record["bytes_hex"])):
                raise RuntimeError(f"rollback verification failed for {path}")
        elif path.exists():
            raise RuntimeError(f"rollback failed to remove {path}")
    _write_json(bundle / "receipt.json", {"transaction_id": transaction_id, "status": "rolled_back"})
    return Receipt(transaction_id, "rolled_back", bundle)
