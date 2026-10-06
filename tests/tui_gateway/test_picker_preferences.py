import pytest

from hermes_cli import picker_preferences
from tui_gateway import methods_complete
from tui_gateway.server import _methods


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(picker_preferences, 'preference_path', lambda: tmp_path / 'prefs.json')


def test_contract_optional_fields_reject_explicit_null():
    from tui_gateway.contracts.config_free_tier_control import PickerPreferencesUpdateParams
    from pydantic import ValidationError
    assert PickerPreferencesUpdateParams(expected_revision=0).model_dump(exclude_unset=True) == {'expected_revision': 0}
    for name in ['favorites', 'visibility', 'custom_models']:
        with pytest.raises(ValidationError):
            PickerPreferencesUpdateParams.model_validate({'expected_revision': 0, name: None})
    schema = PickerPreferencesUpdateParams.model_json_schema()
    for name in ['favorites', 'visibility', 'custom_models']:
        assert 'anyOf' not in schema['properties'][name]


def test_rpc_shared_store_and_conflict():
    get = _methods['model.preferences.get']
    update = _methods['model.preferences.update']
    assert get(1, {})['result']['revision'] == 0
    result = update(2, {'expected_revision': 0, 'favorites': ['a::model:tag']})
    assert result['result'] == picker_preferences.get_preferences()
    assert update(3, {'expected_revision': 0, 'favorites': []})['error']['code'] == 4090
    assert update(4, {'expected_revision': True, 'favorites': []})['error']['code'] == 4000
