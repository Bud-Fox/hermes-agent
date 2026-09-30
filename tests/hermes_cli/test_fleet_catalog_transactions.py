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


def test_prepare_persists_mode_0600_catalog_snapshot_and_rollback_restores_it_once(fleet, monkeypatch):
    from hermes_cli import fleet_catalog_transactions as transactions
    original = (fleet / "catalog.shared.yaml").read_bytes()
    tx = prepare_migration(original.replace(b"Exact/ID", b"New/ID"))
    manifest = json.loads((tx.bundle_path / "manifest.json").read_text())
    snapshot = tx.bundle_path / manifest["catalog"]["payload"]
    assert stat.S_IMODE(snapshot.stat().st_mode) == 0o600
    commit_transaction(tx.transaction_id)
    restores = []
    real_restore = transactions._restore_one
    monkeypatch.setattr(transactions, "_restore_one", lambda bundle, record: (restores.append(record["path"]), real_restore(bundle, record))[1])
    rollback_transaction(tx.transaction_id)
    assert (fleet / "catalog.shared.yaml").read_bytes() == original
    assert restores.count(str(fleet / "catalog.shared.yaml")) == 1


def test_commit_verifies_expected_hashes_modes_and_equal_profile_catalog_hashes(fleet):
    tx = prepare_migration()
    commit_transaction(tx.transaction_id)
    verification = json.loads((tx.bundle_path / "verification.json").read_text())
    assert verification["all_expected_hashes_match"] is True
    assert verification["all_expected_modes_match"] is True
    assert verification["all_profile_static_hashes_equal"] is True


def test_commit_verifies_picker_payload_inventory_for_every_profile(fleet, monkeypatch):
    from hermes_constants import get_hermes_home_override
    from hermes_cli import fleet_catalog_transactions as transactions

    seen = []

    def build(config, *, profile_home=None):
        seen.append((config, profile_home, get_hermes_home_override()))
        providers = config.get("providers", {})
        return {"providers": [
            {"provider_id": provider, "models": value.get("models", [])}
            for provider, value in providers.items()
        ]}

    monkeypatch.setattr(transactions, "_picker_payload", build)
    tx = prepare_migration()
    commit_transaction(tx.transaction_id)

    verification = json.loads((tx.bundle_path / "verification.json").read_text())
    assert verification["all_profile_picker_inventories_equal"] is True
    assert verification["all_profiles_owner_route_picker_equal"] is True
    assert len(seen) == 3  # two canonical profiles plus the All-profiles owner route
    profile_calls = [item for item in seen if item[1] is not None]
    assert [home for _, home, _ in profile_calls] == [
        fleet,
        fleet / "profiles" / "dynamic",
    ]
    assert [active for _, _, active in profile_calls] == [str(fleet), str(fleet / "profiles" / "dynamic")]


def test_read_effective_profile_uses_and_restores_profile_runtime_scope(fleet, monkeypatch):
    from hermes_constants import get_hermes_home_override, reset_hermes_home_override, set_hermes_home_override
    from hermes_cli import config_effective
    from hermes_cli.fleet_catalog_transactions import _read_effective_profile

    path = fleet / "profiles" / "dynamic" / "config.yaml"
    seen = []
    monkeypatch.setattr(
        config_effective,
        "load_user_config_effective",
        lambda requested, fail_closed: seen.append((requested, fail_closed, get_hermes_home_override())) or {},
    )
    token = set_hermes_home_override(fleet / "outer")
    try:
        _read_effective_profile(path)
        assert seen == [(path, True, str(path.parent))]
        assert get_hermes_home_override() == str(fleet / "outer")
    finally:
        reset_hermes_home_override(token)


def test_catalog_only_edit_invalidates_both_config_loaders(fleet):
    from hermes_cli import config, config_effective
    config._LOAD_CONFIG_CACHE.clear()
    config_effective._EFFECTIVE_CACHE.clear()
    path = fleet / "config.yaml"
    assert config.load_config()["providers"]["fleet"]["models"] == ["Exact/ID"]
    assert config_effective.load_user_config_effective(path)["providers"]["fleet"]["models"] == ["Exact/ID"]
    catalog = fleet / "catalog.shared.yaml"
    catalog.write_bytes(catalog.read_bytes().replace(b"Exact/ID", b"Fresh/ID"))
    assert config.load_config()["providers"]["fleet"]["models"] == ["Fresh/ID"]
    assert config_effective.load_user_config_effective(path)["providers"]["fleet"]["models"] == ["Fresh/ID"]
