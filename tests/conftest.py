from __future__ import annotations

import os
import tempfile

_TEST_DIR = tempfile.mkdtemp(prefix="adsentry-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DIR}/test.db"
os.environ["ADSENTRY_SEED"] = "0"
os.environ["ADSENTRY_DEMO"] = "1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from adsentry.config import Settings  # noqa: E402
from adsentry.db import Base, SessionLocal, engine  # noqa: E402
from adsentry.main import create_app  # noqa: E402
from adsentry.models import Account  # noqa: E402
from adsentry.tenant import hash_api_key  # noqa: E402

NORTHWIND_KEY = "test-northwind-key"
SKYLINE_KEY = "test-skyline-key"


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def session() -> Session:
    with SessionLocal() as db:
        yield db


@pytest.fixture
def tenants(session: Session) -> tuple[Account, Account]:
    first = Account(
        name="Northwind Retail", slug="northwind", api_key_hash=hash_api_key(NORTHWIND_KEY)
    )
    second = Account(
        name="Skyline Travel", slug="skyline", api_key_hash=hash_api_key(SKYLINE_KEY)
    )
    session.add_all([first, second])
    session.commit()
    session.refresh(first)
    session.refresh(second)
    return first, second


@pytest.fixture
def app():
    return create_app(Settings(demo_mode=True, seed_demo_data=False))


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def northwind_client(client, tenants):
    client.headers.update({"X-API-Key": NORTHWIND_KEY})
    return client


@pytest.fixture
def skyline_client(app, tenants):
    with TestClient(app) as test_client:
        test_client.headers.update({"X-API-Key": SKYLINE_KEY})
        yield test_client


def make_campaign(client, external_id="CMP-001", **overrides):
    payload = {
        "external_id": external_id,
        "name": f"Campaign {external_id}",
        "channel": "google",
        **overrides,
    }
    response = client.post("/api/v1/campaigns", json=payload)
    assert response.status_code == 201, response.text
    return response.json()
