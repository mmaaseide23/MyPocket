"""Synthetic fixtures only: no local database, credentials, network or Keychain."""

import importlib
import os
import socket

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

os.environ["DATABASE_URL"] = "sqlite://"

from mypocket.core.config import settings  # noqa: E402
from mypocket.security import crypto, keys  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network and Keychain access are forbidden in tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(keys, "_get_or_create", forbidden)
    monkeypatch.setattr(crypto, "master_encryption_key", lambda: b"x" * 32)
    monkeypatch.setattr(settings, "etrade_consumer_key", "test-key")
    monkeypatch.setattr(settings, "etrade_consumer_secret", "test-secret")
    monkeypatch.setattr(settings, "etrade_environment", "production")
    monkeypatch.setattr(settings, "teller_environment", "development")
    monkeypatch.setattr(settings, "teller_application_id", "test-app")
    monkeypatch.setattr(settings, "teller_cert_path", "not-used-test-cert")
    monkeypatch.setattr(settings, "teller_key_path", "not-used-test-key")


@pytest.fixture
def db(monkeypatch):
    from mypocket.core import templating

    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    monkeypatch.setattr(templating, "engine", engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def plaid(monkeypatch):
    module = importlib.import_module("mypocket.integrations.plaid")
    monkeypatch.setattr(settings, "plaid_client_id", "synthetic-client")
    monkeypatch.setattr(settings, "plaid_secret", "synthetic-secret")
    monkeypatch.setattr(settings, "plaid_environment", "sandbox")
    monkeypatch.setattr(settings, "plaid_trial_confirmed", False)
    return module
