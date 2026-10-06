import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from hermes_cli import picker_preferences as prefs


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(prefs, 'preference_path', lambda: tmp_path / 'picker.json')


def test_uninitialized_import_once_clear_and_orphans():
    assert prefs.get_preferences()['initialized'] is False
    state = prefs.update_preferences({'expected_revision': 0, 'import_once': True,
        'favorites': ['gone::model:tag'],
        'visibility': {'visible': ['gone::'], 'known': ['gone::model:tag']},
        'custom_models': [{'provider': 'gone', 'model': 'x:y'}]})
    assert state['revision'] == 1
    assert prefs.update_preferences({'expected_revision': 0, 'import_once': True, 'favorites': []}) == state
    cleared = prefs.update_preferences({'expected_revision': 1, 'favorites': []})
    assert cleared['favorites'] == []
    assert cleared['visibility'] == state['visibility']
    assert cleared['custom_models'] == state['custom_models']
    assert json.loads(prefs.preference_path().read_text()) == cleared


def test_cas_conflict_and_simultaneous_updates():
    def write(key):
        try:
            return prefs.update_preferences({'expected_revision': 0, 'favorites': [key]})
        except prefs.PreferenceConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ['a::one', 'a::two']))
    assert sum(r is not None for r in results) == 1
    assert prefs.get_preferences()['revision'] == 1


@pytest.mark.parametrize('payload', [
    {'expected_revision': True, 'favorites': []},
    {'expected_revision': 0, 'unknown': []},
    {'expected_revision': 0, 'favorites': ['not-a-key']},
    {'expected_revision': 0, 'favorites': ['a::']},
    {'expected_revision': 0, 'visibility': {'visible': [], 'oops': []}},
    {'expected_revision': 0, 'custom_models': [{'provider': 'a', 'model': 'x', 'api_key': 'no'}]},
    {'expected_revision': 0},
    {'expected_revision': 0, 'favorites': ['a::' + 'x' * 1025]},
    # Outer null is not a reset; generated RPC contract types these fields non-null too.
    {'expected_revision': 0, 'favorites': None},
    {'expected_revision': 0, 'visibility': None},
    {'expected_revision': 0, 'custom_models': None},
])
def test_malformed_rejected_without_storage(payload):
    with pytest.raises(ValueError):
        prefs.update_preferences(payload)
    assert not prefs.preference_path().exists()


def test_atomic_replace_failure_preserves_previous(monkeypatch):
    state = prefs.update_preferences({'expected_revision': 0, 'favorites': ['a::old']})
    def fail(*args):
        raise OSError('replace failure')
    monkeypatch.setattr(prefs.os, 'replace', fail)
    with pytest.raises(OSError):
        prefs.update_preferences({'expected_revision': 1, 'favorites': ['a::new']})
    assert prefs.get_preferences() == state


def test_corrupt_store_not_silently_reset():
    prefs.preference_path().write_text('{}')
    with pytest.raises(ValueError):
        prefs.get_preferences()


def test_file_permissions():
    prefs.update_preferences({'expected_revision': 0, 'custom_models': [{'provider': 'orphan', 'model': 'MiXeD/tag:free'}]})
    import stat
    assert stat.S_IMODE(prefs.preference_path().stat().st_mode) == 0o600


def test_import_validates_bounds_before_noop():
    prefs.update_preferences({'expected_revision': 0, 'favorites': ['p::one']})
    with pytest.raises(ValueError):
        prefs.update_preferences({'expected_revision': 0, 'import_once': True, 'favorites': ['p::x'] * 4097})
    with pytest.raises(ValueError):
        prefs.update_preferences({'expected_revision': 1, 'favorites': ['p::' + str(i) + 'x' * 1000 for i in range(300)]})
