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
        # Historique très court (≈72 bougies H1) → sous MIN_TRAIN_SAMPLES :
        # Couleuvre ne s'entraîne pas, donc aucun trade OOS.
        assert job["result"]["oos_stats"]["trades"] == 0
        assert job["result"]["folds"][0]["train_report"]["trained"] is False

        # Les artefacts du run existent.
        assert (training_env / "models" / payload["run_id"] / "metadata.json").exists()

        # Le run est historisé avec ses métriques OOS.
        runs = client.get("/api/training/runs").json()["runs"]
        assert runs[0]["id"] == payload["run_id"]
        assert runs[0]["status"] == "completed"
        assert runs[0]["oos_trades"] == 0


def test_definition_modele() -> None:
    with TestClient(create_app()) as client:
        ok = client.get("/api/training/definition/couleuvre_v0_1")
        unknown = client.get("/api/training/definition/inexistante")
    assert ok.status_code == 200
    definition = ok.json()["definition"]
    assert definition["n_features"] > 0
    assert definition["barrier_atr_mult"] > 0
    assert "enter_long_threshold" in definition
    assert unknown.status_code == 404


def test_current_job_vide_au_repos(training_env: Path) -> None:
    """Sans run en cours, /current-job répond null (et ne matche pas la
    route /jobs/{id} — l'ordre de déclaration compte)."""
    with TestClient(create_app()) as client:
        response = client.get("/api/training/current-job")
    assert response.status_code == 200
    assert response.json() == {"job": None}


def test_runs_orphelins_marques_failed_au_demarrage(training_env: Path) -> None:
    """Un serveur arrêté en plein entraînement laissait la ligne « running »
    pour toujours ; au démarrage suivant elle doit passer « failed »."""
    from pyea.storage.storage_database import init_db
    from pyea.storage.storage_training_runs import create_run, list_runs

    init_db()
    create_run("run-orphelin", "couleuvre_v0_1", "EURUSD", "H1", 3, {})
    # Le lifespan de create_app appelle fail_orphan_runs().
    with TestClient(create_app()) as client:
        client.get("/api/status")
    statuses = {run["id"]: run["status"] for run in list_runs()}
    assert statuses["run-orphelin"] == "failed"


def test_erreurs_entrainement(training_env: Path) -> None:
    with TestClient(create_app()) as client:
        no_data = client.post("/api/training/run", json={"symbol": "GBPUSD"})
        # Historique trop court : détecté APRÈS le chargement, qui vit dans
        # le job (le POST répond tout de suite) → le job échoue proprement
        # avec un message actionnable, et le run est historisé « failed ».
        too_short = client.post(
            "/api/training/run",
            json={"symbol": "EURUSD", "timeframe": "D1", "folds": 20},
        )
        assert too_short.status_code == 200
        job = _wait_for_job(client, too_short.json()["job_id"])
        unknown_job = client.get("/api/training/jobs/nimporte")
        runs = client.get("/api/training/runs").json()["runs"]
    assert no_data.status_code == 404
    assert job["status"] == "failed"
    assert "trop court" in job["error"]
    assert runs[0]["status"] == "failed"
    assert unknown_job.status_code == 404
