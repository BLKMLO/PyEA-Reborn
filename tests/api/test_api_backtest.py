"""Tests des endpoints /api/backtest/* (datasets + exécution)."""

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from pyea.app_factory import create_app
from pyea.config.config_settings import get_settings
from pyea.data.data_history_downloader import year_file_path


@pytest.fixture
def history_dir(tmp_path: Path):
    """Historique M1 synthétique (2 jours EURUSD) dans un dossier isolé."""
    index = pd.date_range("2024-03-01", periods=2 * 1440, freq="1min", tz="UTC")
    closes = [1.08 + 0.0001 * (i % 50) for i in range(len(index))]
    frame = pd.DataFrame(
        {
            "bid_open": closes, "bid_high": [c + 0.0001 for c in closes],
            "bid_low": [c - 0.0001 for c in closes], "bid_close": closes,
            "ask_open": closes, "ask_high": closes, "ask_low": closes,
            "ask_close": closes, "volume": [1.0] * len(index),
        },
        index=index,
    )
    target = year_file_path(tmp_path, "EURUSD", 2024)
    target.parent.mkdir(parents=True)
    frame.to_parquet(target)

    settings = get_settings()
    original = settings.history_data_dir
    settings.history_data_dir = str(tmp_path)
    yield
    settings.history_data_dir = original


def _client() -> TestClient:
    return TestClient(create_app())


def test_datasets_liste_l_historique_local(history_dir: None) -> None:
    with _client() as client:
        data = client.get("/api/backtest/datasets").json()
    assert data["datasets"] == [{"symbol": "EURUSD", "years": [2024]}]
    assert "couleuvre_v0_1" in data["strategies"]
    assert "H1" in data["timeframes"]


def test_run_backtest_h1_strategie_vide(history_dir: None) -> None:
    with _client() as client:
        response = client.post(
            "/api/backtest/run", json={"symbol": "EURUSD", "timeframe": "H1"}
        )
    assert response.status_code == 200
    result = response.json()
    # 2 jours de M1 → 48 bougies H1 ; Couleuvre v0.1 est encore muette.
    assert result["stats"]["bars"] == 48
    assert result["stats"]["trades"] == 0
    assert result["trades"] == []
    assert len(result["equity_curve"]) >= 2


def test_run_erreurs_explicites(history_dir: None) -> None:
    with _client() as client:
        no_data = client.post("/api/backtest/run", json={"symbol": "GBPUSD"})
        bad_timeframe = client.post(
            "/api/backtest/run", json={"symbol": "EURUSD", "timeframe": "H2"}
        )
        bad_strategy = client.post(
            "/api/backtest/run", json={"symbol": "EURUSD", "strategy": "nimporte"}
        )
    assert no_data.status_code == 404
    assert bad_timeframe.status_code == 400
    assert bad_strategy.status_code == 404
