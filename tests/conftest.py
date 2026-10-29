import os

import pytest


@pytest.fixture(params=["memory", "postgres"])
def client(request):
    """The same API tests run against the in-memory repository and a real PostgreSQL."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    if request.param == "memory":
        from app.repository import InMemoryRepository

        repo = InMemoryRepository()
    else:
        dsn = os.environ.get("TEST_DATABASE_URL")
        if not dsn:
            pytest.skip("TEST_DATABASE_URL not set; skipping PostgreSQL run")
        from app.repository import PostgresRepository

        repo = PostgresRepository(dsn)
        repo.init_schema()
        repo.reset()
    yield TestClient(create_app(repo=repo))
    if request.param == "postgres":
        repo.reset()
        repo.close()
