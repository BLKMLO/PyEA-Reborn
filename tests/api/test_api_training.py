"""Tests des endpoints /api/training/* (run, suivi, historique)."""

import time
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from pyea.app_factory import create_app
from pyea.config.config_settings import get_settings
from pyea.data.data_history_downloader import year_file_path


@pytest.fixture
def training_env(tmp_path: Path):
    """Historique synthétique + base et artefacts isolés."""
    index = pd.date_range("2024-01-01", periods=3 * 1440, freq="1min", tz="UTC")
    closes = [1.08 + 0.0001 * (i % 40) for i in range(len(index))]
    frame = pd.DataFrame(
        {"bid_open": closes, "bid_high": closes, "bid_low": closes,
         "bid_close": closes, "volume": [1.0] * len(index)},
        index=index,
    )
    target = year_file_path(tmp_path / "history", "EURUSD", 2024)
    target.parent.mkdir(parents=True)
    frame.to_parquet(target)

    settings = get_settings()
    saved = (settings.history_data_dir, settings.models_dir, settings.database_url)
    settings.history_data_dir = str(tmp_path / "history")
    settings.models_dir = str(tmp_path / "models")
    settings.database_url = f"sqlite:///{tmp_path}/test.db"
    yield tmp_path
    settings.history_data_dir, settings.models_dir, settings.database_url = saved


def _wait_for_job(client: TestClient, job_id: str, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/training/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.1)
    raise TimeoutError("Job d'entraînement trop long.")


def test_entrainement_complet(training_env: Path) -> None:
    with TestClient(create_app()) as client:
        response = client.post(
            "/api/training/run",
            json={"symbol": "EURUSD", "timeframe": "H1", "folds": 3},
        )
        assert response.status_code == 200
        payload = response.json()
        job = _wait_for_job(client, payload["job_id"])

        assert job["status"] == "completed"
        assert len(job["result"]["folds"]) == 3
        assert job["result"]["oos_stats"]["trades"] == 0  # Couleuvre muette.

        # Les artefacts du run existent.
        assert (training_env / "models" / payload["run_id"] / "metadata.json").exists()

        # Le run est historisé avec ses métriques OOS.
        runs = client.get("/api/training/runs").json()["runs"]
        assert runs[0]["id"] == payload["run_id"]
        assert runs[0]["status"] == "completed"
        assert runs[0]["oos_trades"] == 0


def test_erreurs_entrainement(training_env: Path) -> None:
    with TestClient(create_app()) as client:
        no_data = client.post("/api/training/run", json={"symbol": "GBPUSD"})
        too_short = client.post(
            "/api/training/run",
            json={"symbol": "EURUSD", "timeframe": "D1", "folds": 20},
        )
        unknown_job = client.get("/api/training/jobs/nimporte")
    assert no_data.status_code == 404
    assert too_short.status_code == 400
    assert unknown_job.status_code == 404
