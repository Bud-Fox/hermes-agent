"""Installation-shared, non-secret picker presentation state (REST/RPC authority)."""
from __future__ import annotations

import copy
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from hermes_constants import get_default_hermes_root

MAX_BODY_BYTES = 262144
MAX_ROWS = 4096
MAX_ID = 1024


class PreferenceConflict(ValueError):
    """The caller's snapshot is stale; fetch before proposing another write."""


@contextmanager
def _preference_lock(path: Path):
    # Keep this shared REST/RPC core stdlib-only; WebUI need not import agent runtime dependencies.
    with path.open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            if lock.tell() == 0:
                lock.write(b'0')
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'nt':
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def preference_path() -> Path:
    return get_default_hermes_root() / 'picker-preferences.shared.json'


def _empty() -> dict:
    return {'version': 1, 'revision': 0, 'initialized': False, 'favorites': [],
            'visibility': {'visible': None, 'known': None}, 'custom_models': []}


def _identifier(value) -> bool:
    return isinstance(value, str) and 0 < len(value) <= MAX_ID and not any(c.isspace() or ord(c) < 32 for c in value)


def _keys(value, *, sentinel=False):
    if not isinstance(value, list) or len(value) > MAX_ROWS:
        raise ValueError('preference lists must be bounded arrays')
    for key in value:
        if not isinstance(key, str) or '::' not in key:
            raise ValueError('expected literal provider::model identity')
        provider, model = key.split('::', 1)
        if not _identifier(provider) or not (_identifier(model) or (sentinel and model == '')):
            raise ValueError('invalid preference identity')
    return list(dict.fromkeys(value))


def _fields(raw: dict) -> dict:
    out = {}
    if 'favorites' in raw:
        out['favorites'] = _keys(raw['favorites'])
    if 'visibility' in raw:
        vis = raw['visibility']
        if not isinstance(vis, dict) or set(vis) != {'visible', 'known'}:
            raise ValueError('visibility requires only visible and known')
        out['visibility'] = {k: None if v is None else _keys(v, sentinel=k == 'visible') for k, v in vis.items()}
    if 'custom_models' in raw:
        customs = raw['custom_models']
        if not isinstance(customs, list) or len(customs) > MAX_ROWS:
            raise ValueError('custom_models must be a bounded array')
        out['custom_models'] = []
        for row in customs:
            if not isinstance(row, dict) or set(row) != {'provider', 'model'} or not all(_identifier(row[k]) for k in row):
                raise ValueError('custom rows require only provider and model identifiers')
            if row not in out['custom_models']:
                out['custom_models'].append(dict(row))
    return out


def _bounded(raw):
    if not isinstance(raw, dict) or len(json.dumps(raw, ensure_ascii=False).encode('utf-8')) > MAX_BODY_BYTES:
        raise ValueError('preference payload exceeds size limit or is not an object')


def _read(path: Path) -> dict:
    try:
        with path.open('rb') as source:
            data = source.read(MAX_BODY_BYTES + 1)
    except FileNotFoundError:
        return _empty()
    if len(data) > MAX_BODY_BYTES:
        raise ValueError('stored preferences exceed size limit')
    raw = json.loads(data)
    if (not isinstance(raw, dict) or set(raw) != set(_empty()) or type(raw['version']) is not int
            or raw['version'] != 1 or type(raw['revision']) is not int or raw['revision'] < 0
            or type(raw['initialized']) is not bool):
        raise ValueError('invalid stored preference schema; recovery required')
    return {**raw, **_fields(raw)}


def get_preferences() -> dict:
    # Atomic replace means readers see complete old/new snapshots without creating any files.
    return _read(preference_path())


def update_preferences(payload: dict) -> dict:
    _bounded(payload)
    if set(payload) - {'expected_revision', 'import_once', 'favorites', 'visibility', 'custom_models'}:
        raise ValueError('unknown preference fields')
    if type(payload.get('expected_revision')) is not int or payload['expected_revision'] < 0:
        raise ValueError('expected_revision must be a nonnegative integer')
    if 'import_once' in payload and type(payload['import_once']) is not bool:
        raise ValueError('import_once must be boolean')
    changes = _fields(payload)
    if not changes:
        raise ValueError('at least one preference field is required')
    path = preference_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _preference_lock(path.with_suffix('.lock')):
        state = _read(path)
        if payload.get('import_once') and state['initialized']:
            return state
        if payload['expected_revision'] != state['revision']:
            raise PreferenceConflict('picker preferences changed; reload before saving')
        next_state = {**state, **changes, 'initialized': True, 'revision': state['revision'] + 1}
        _bounded(next_state)
        fd, name = tempfile.mkstemp(prefix='.picker-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as target:
                json.dump(next_state, target, ensure_ascii=False)
                target.flush()
                os.fsync(target.fileno())
            os.replace(name, path)
            if os.name != 'nt':
                directory = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        return copy.deepcopy(next_state)
