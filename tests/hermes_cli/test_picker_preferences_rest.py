import asyncio
import json
from contextlib import nullcontext

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli import picker_preferences as prefs
from hermes_cli.web_routers import models


def test_dashboard_rest_bounded_cas_shared_core(tmp_path, monkeypatch):
    monkeypatch.setattr(prefs, 'preference_path', lambda: tmp_path / 'shared.json')
    monkeypatch.setattr(models, '_config_profile_scope', lambda profile: nullcontext())
    app = FastAPI()
    app.include_router(models.router)
    client = TestClient(app)
    assert client.get('/api/model/preferences').json()['revision'] == 0
    result = client.post('/api/model/preferences', json={'expected_revision': 0, 'favorites': ['p::model:tag']})
    assert result.status_code == 200
    assert result.json() == prefs.get_preferences()
    assert client.post('/api/model/preferences', json={'expected_revision': 0, 'favorites': []}).status_code == 409
    assert client.post('/api/model/preferences', json={'expected_revision': True, 'favorites': []}).status_code == 400
    assert client.post('/api/model/preferences', content=b'x' * (prefs.MAX_BODY_BYTES + 1)).status_code == 413


def test_preferences_not_public_and_existing_auth_gate_requires_auth(monkeypatch):
    from hermes_cli import web_server
    from starlette.requests import Request
    assert '/api/model/preferences' not in web_server._PUBLIC_API_PATHS
    app = FastAPI()
    app.state.auth_required = False
    request = Request({'type': 'http', 'method': 'GET', 'path': '/api/model/preferences', 'headers': [], 'query_string': b'', 'app': app})
    called = []
    async def downstream(request):
        called.append(True)
    response = asyncio.run(web_server.auth_middleware(request, downstream))
    assert response.status_code == 401
    assert called == []
