"""
Shared pytest fixtures.

Tests use an in-memory SQLite database instead of Postgres so they run
fast and don't require Docker or a real .env. This works because
app.db_types.GUID/JSONType are dialect-portable (see that file).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app


@pytest.fixture()
def sqlite_sessionmaker():
    """A sessionmaker bound to a fresh in-memory SQLite DB (shared across
    connections via StaticPool, so multiple SessionLocal() calls in the
    same test -- as app/tools/mcp_server.py makes -- see the same data)."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    try:
        yield TestingSessionLocal
    finally:
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db_session(sqlite_sessionmaker):
    session = sqlite_sessionmaker()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    # Deliberately NOT using TestClient as a context manager: that would run
    # the app's lifespan (app/main.py), which calls Base.metadata.create_all
    # against the real Postgres engine. Tests already create tables on the
    # in-memory SQLite engine above via the db_session fixture.
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()
