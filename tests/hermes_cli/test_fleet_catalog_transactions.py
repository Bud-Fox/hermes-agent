from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
import yaml

from hermes_cli.fleet_catalog_transactions import (
    commit_transaction,
    prepare_migration,
    rollback_transaction,
)


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    root = tmp_path / ".hermes"
    root.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(root))
    (root / "config.yaml").write_bytes(b"model:\n  default: root\nproviders:\n  a:\n    models: [A]\n")
    profile = root / "profiles" / "dynamic"
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_bytes(
        b"model:\n  default: child\nproviders:\n  b:\n    models: [B]\n")
    (root / "catalog.shared.yaml").write_text(
        yaml.safe_dump({"version": 1, "providers": {"fleet": {"models": ["Exact/ID"]}}}),
        encoding="utf-8",
    )
    return root


def test_commit_removes_legacy_providers_and_rollback_restores_exact_bytes_and_modes(fleet):
    paths = [fleet / "config.yaml", fleet / "profiles" / "dynamic" / "config.yaml"]
    before = {p: (p.read_bytes(), stat.S_IMODE(p.stat().st_mode)) for p in paths}
    paths[1].chmod(0o640)
    before[paths[1]] = (paths[1].read_bytes(), 0o640)

    tx = prepare_migration()
    receipt = commit_transaction(tx.transaction_id)
    assert receipt.status == "committed"
    assert all("providers" not in yaml.safe_load(p.read_text(encoding="utf-8")) for p in paths)

    rolled = rollback_transaction("last")
    assert rolled.status == "rolled_back"
    for path, (data, mode) in before.items():
        assert path.read_bytes() == data
        assert stat.S_IMODE(path.stat().st_mode) == mode

    again = rollback_transaction(tx.transaction_id)
    assert again.status == "already_rolled_back"


def test_mid_commit_failure_compensates_all_files(fleet, monkeypatch):
    from hermes_cli import fleet_catalog_transactions as transactions

    paths = [fleet / "config.yaml", fleet / "profiles" / "dynamic" / "config.yaml"]
    before = {p: p.read_bytes() for p in paths}
    tx = prepare_migration()
    real = transactions._atomic_write_bytes
    writes = 0

    def fail_second(path, data, mode):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("injected")
        return real(path, data, mode)

    monkeypatch.setattr(transactions, "_atomic_write_bytes", fail_second)
    with pytest.raises(OSError, match="injected"):
        commit_transaction(tx.transaction_id)

    assert {p: p.read_bytes() for p in paths} == before
    receipt_path = fleet / "backups" / "fleet-catalog" / tx.transaction_id / "receipt.json"
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["status"] == "compensated"
