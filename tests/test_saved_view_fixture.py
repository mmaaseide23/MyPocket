"""Check the browser fixture's privacy boundary in a separate process."""

import subprocess
import sys


def test_fixture_refuses_environment_file_before_importing_application_settings():
    script = """
import sys
from tests import saved_view_fixture as fixture
assert 'mypocket.core.config' not in sys.modules
fixture.Path.exists = lambda path: True
try:
    fixture.create_fixture('http://127.0.0.1:12345')
except RuntimeError:
    pass
else:
    raise AssertionError('Fixture did not reject its isolation precondition')
assert 'mypocket.core.config' not in sys.modules
"""
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, timeout=15)


def test_isolated_fixture_initializes_without_any_provider_or_keychain_dependency():
    script = """
from fastapi.testclient import TestClient
from tests.saved_view_fixture import create_fixture
origin='http://127.0.0.1:12345'
with TestClient(create_fixture(origin), base_url=origin) as client:
    assert client.get('/saved-view/access').status_code == 401
    assert client.post('/fixture/login').status_code == 200
    assert client.get('/saved-view/options').status_code == 200
    assert client.get('/saved-view/snapshot').status_code == 403
    response=client.get('/saved-view/')
    assert response.status_code == 200 and 'Fictional test only' in response.text
"""
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, timeout=15)
