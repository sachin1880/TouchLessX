import os
import shutil
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://smarthome:smarthome@localhost:5432/smarthome_camera_test"
)
os.environ.setdefault("JWT_SECRET", "test-secret-key-not-for-production")
# Isolated from the real dev media/ directory so test runs never leak capture files there.
os.environ.setdefault("MEDIA_ROOT", ".test-media")

from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.core.config import get_settings  # noqa: E402

engine = create_engine(os.environ["DATABASE_URL"])
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="session", autouse=True)
def setup_database():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    shutil.rmtree(get_settings().media_root, ignore_errors=True)


@pytest.fixture()
def db_session():
    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def register_and_login(client):
    def _create(email: str | None = None, password: str = "supersecret123"):
        email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
        client.post("/api/auth/register", json={"name": "Test User", "email": email, "password": password})
        response = client.post("/api/auth/login", json={"email": email, "password": password})
        token = response.json()["access_token"]
        return {
            "email": email,
            "password": password,
            "token": token,
            "headers": {"Authorization": f"Bearer {token}"},
        }

    return _create
