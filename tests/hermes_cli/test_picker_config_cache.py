"""Real config/context reads against isolated profile and fleet files."""
import pytest

from hermes_cli import config, fleet_catalog
from hermes_cli.inventory import load_picker_context


@pytest.mark.parametrize('readonly', [False, True])
def test_cold_warm_and_catalog_change_project_fleet(tmp_path, monkeypatch, readonly):
    root = tmp_path / 'fleet'
    root.mkdir()
    monkeypatch.setattr(fleet_catalog, 'fleet_root', lambda: root)
    monkeypatch.setattr(config, 'ensure_hermes_home', lambda: None)
    homes = [root / 'a', root / 'b']
    for home in homes:
        home.mkdir()
        (home / 'config.yaml').write_text('model:\n  default: profile-choice\nproviders:\n  stale:\n    models: [profile-only]\n')
    reader = config.load_config_readonly if readonly else config.load_config
    for home in [homes[0], homes[1], homes[0]]:
        monkeypatch.setattr(config, 'get_config_path', lambda: home / 'config.yaml')
        for model in ['fleet-one', 'fleet-two']:
            (root / 'catalog.shared.yaml').write_text('version: 1\nproviders:\n  fleet:\n    models: [' + model + ']\n')
            assert reader()['providers'] == {'fleet': {'models': [model]}}
            # Exercise the actual warm hit, not a silently disabled cache.
            _, signature = config._load_config_cache_sig(home / 'config.yaml')
            assert config._load_config_cache_hit(str(home / 'config.yaml'), signature) is not None
            for _ in range(2):
                assert reader()['providers'] == {'fleet': {'models': [model]}}
                assert load_picker_context().user_providers == {'fleet': {'models': [model]}}
                assert reader()['model']['default'] == 'profile-choice'
        assert 'profile-only' in (home / 'config.yaml').read_text()


def test_warm_hit_serves_cached_projection_without_reparsing_catalog(tmp_path, monkeypatch):
    root = tmp_path / 'fleet'
    root.mkdir()
    monkeypatch.setattr(fleet_catalog, 'fleet_root', lambda: root)
    monkeypatch.setattr(config, 'ensure_hermes_home', lambda: None)
    home = root / 'p'
    home.mkdir()
    (home / 'config.yaml').write_text('providers:\n  stale:\n    models: [profile-only]\n')
    (root / 'catalog.shared.yaml').write_text('version: 1\nproviders:\n  fleet:\n    models: [m]\n')
    monkeypatch.setattr(config, 'get_config_path', lambda: home / 'config.yaml')
    first = config.load_config_readonly()
    loads = []
    real = fleet_catalog.load_fleet_catalog
    monkeypatch.setattr(fleet_catalog, 'load_fleet_catalog', lambda: loads.append(1) or real())
    assert config.load_config_readonly() is first
    assert first['providers'] == {'fleet': {'models': ['m']}}
    assert config.load_config()['providers'] == {'fleet': {'models': ['m']}}
    assert loads == []
