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


def test_datasets_ignore_fichiers_parasites(history_dir: None, tmp_path: Path) -> None:
    """Une copie de sauvegarde manuelle ne doit plus casser toute la page
    backtest (500 sur /datasets avant la passe de sécurisation)."""
    data_dir = Path(get_settings().history_data_dir)
    source = data_dir / "EURUSD" / "EURUSD_m1_2024.parquet"
    (data_dir / "EURUSD" / "EURUSD_m1_backup.parquet").write_bytes(source.read_bytes())
    (data_dir / "notes.txt").write_text("mémo", encoding="utf-8")  # fichier égaré

    with _client() as client:
        response = client.get("/api/backtest/datasets")
    assert response.status_code == 200
    assert response.json()["datasets"] == [{"symbol": "EURUSD", "years": [2024]}]


def test_run_parquet_corrompu_400_actionnable(history_dir: None) -> None:
    data_dir = Path(get_settings().history_data_dir)
    (data_dir / "EURUSD" / "EURUSD_m1_2023.parquet").write_bytes(b"pas du parquet")

    with _client() as client:
        response = client.post("/api/backtest/run", json={"symbol": "EURUSD"})
    assert response.status_code == 400
    assert "illisible" in response.json()["detail"]
    assert "EURUSD_m1_2023" in response.json()["detail"]


def test_run_periode_inversee_422(history_dir: None) -> None:
    with _client() as client:
        response = client.post(
            "/api/backtest/run",
            json={"symbol": "EURUSD", "start": "2024-03-02", "end": "2024-03-01"},
        )
    assert response.status_code == 422
    assert "Période invalide" in response.text
