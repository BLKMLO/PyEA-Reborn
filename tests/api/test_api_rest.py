"""Tests fumée de l'API : l'app démarre et les endpoints clés répondent."""

from fastapi.testclient import TestClient

from pyea.app_factory import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def test_dashboard_repond() -> None:
    with _client() as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "PyEA" in response.text


def test_status_repond() -> None:
    with _client() as client:
        response = client.get("/api/status")
    assert response.status_code == 200
    assert response.json()["strategy"] == "couleuvre_v0_1"


def test_price_history_au_format_chartjs() -> None:
    with _client() as client:
        response = client.get("/api/charts/price-history?points=10")
    data = response.json()
    assert response.status_code == 200
    assert len(data["labels"]) == len(data["prices"]) == 10
