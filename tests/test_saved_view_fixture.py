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


def test_private_https_phone_fixture_uses_separate_secure_cookie_and_form_redirects():
    script = """
from fastapi.testclient import TestClient
from tests.saved_view_fixture import create_fixture, FIXTURE_COOKIE
origin='https://fixture.invalid:8443'
with TestClient(create_fixture(origin), base_url=origin) as client:
    client.cookies.set('mypocket_session', 'synthetic-main-cookie-untouched')
    assert client.get('/saved-view/access').status_code == 401
    response=client.post('/fixture/login',headers={'Accept':'text/html'},follow_redirects=False)
    assert response.status_code == 303 and response.headers['location']=='/saved-view/'
    cookie=response.headers['set-cookie']
    assert cookie.startswith(FIXTURE_COOKIE+'=') and 'Secure' in cookie and 'HttpOnly' in cookie
    assert client.cookies.get('mypocket_session')=='synthetic-main-cookie-untouched'
    assert client.get('/saved-view/options').status_code==200
    response=client.post('/fixture/check',headers={'Accept':'text/html'},follow_redirects=False)
    assert response.status_code==303
    response=client.post('/saved-view/logout',headers={'Origin':origin})
    assert response.status_code==200
    assert response.headers['set-cookie'].startswith(FIXTURE_COOKIE+'=')
    assert client.cookies.get('mypocket_session')=='synthetic-main-cookie-untouched'
"""
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, timeout=15)
