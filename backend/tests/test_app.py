"""CP-0.1 gate: the app boots, /health answers, and the MVP stack is present."""

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_root_is_reachable() -> None:
    assert client.get("/").status_code == 200


def test_networkx_is_installed() -> None:
    """The MVP traversal engine (CP-1.4) — absent from requirements before CP-0.1."""
    import networkx

    assert networkx.Graph() is not None


def test_config_is_sqlite_first() -> None:
    """ARCHITECTURE.md §"Two corrections": no server-backed store before CP-6.1.

    This test is the guardrail on the drift CP-0.1 corrected. If someone
    reintroduces a Postgres/Redis/Neo4j setting ahead of its checkpoint, this
    fails and makes them justify it.
    """
    premature = {"DATABASE_URL", "REDIS_URL", "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"}
    assert premature.isdisjoint(type(settings).model_fields)
    assert settings.sqlite_url.startswith("sqlite:///")
