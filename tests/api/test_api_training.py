"""Tests des endpoints /api/training/* (run, suivi, historique)."""

import time
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from pyea.app_factory import create_app
from pyea.config.config_settings import get_settings
from pyea.data.data_history_downloader import year_file_path


def _write_history(root: Path, symbol: str) -> None:
    """3 jours de M1 synthétique pour un symbole (≈72 bougies H1)."""
    index = pd.date_range("2024-01-01", periods=3 * 1440, freq="1min", tz="UTC")
    closes = [1.08 + 0.0001 * (i % 40) for i in range(len(index))]
    frame = pd.DataFrame(
        {"bid_open": closes, "bid_high": closes, "bid_low": closes,
         "bid_close": closes, "volume": [1.0] * len(index)},
        index=index,
    )
    target = year_file_path(root, symbol, 2024)
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(target)


@pytest.fixture
def training_env(tmp_path: Path):
    """Historique synthétique + base et artefacts isolés."""
    _write_history(tmp_path / "history", "EURUSD")

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
        # Un SEUL actif ⇒ stratégie par actif : la mutualisée exige un pool.
        response = client.post(
            "/api/training/run",
            json={"symbols": ["EURUSD"], "timeframe": "H1", "folds": 3,
                  "strategy": "couleuvre_v0_1"},
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


def test_suppression_run(training_env: Path) -> None:
    """DELETE /runs/{id} : ligne SQL + artefacts effacés ; 404 si inconnu,
    409 si le run est encore « running »."""
    from pyea.storage.storage_database import init_db
    from pyea.storage.storage_training_runs import create_run, finish_run

    init_db()
    settings = get_settings()
    create_run("run-a-supprimer", "couleuvre_v0_1", "EURUSD", "H1", 3, {})
    finish_run("run-a-supprimer", "completed")
    artifacts = Path(settings.models_dir) / "run-a-supprimer"
    artifacts.mkdir(parents=True)
    (artifacts / "metadata.json").write_text("{}")

    with TestClient(create_app()) as client:
        # Créé APRÈS le démarrage : le lifespan marque « failed » les runs
        # « running » orphelins (fail_orphan_runs).
        create_run("run-en-cours", "couleuvre_v0_1", "EURUSD", "H1", 3, {})
        missing = client.delete("/api/training/runs/inconnu")
        running = client.delete("/api/training/runs/run-en-cours")
        ok = client.delete("/api/training/runs/run-a-supprimer")
        runs = client.get("/api/training/runs").json()["runs"]

    assert missing.status_code == 404
    assert running.status_code == 409
    assert ok.status_code == 200 and ok.json()["deleted"] is True
    assert not artifacts.exists()  # artefacts disque effacés aussi
    assert {run["id"] for run in runs} == {"run-en-cours"}


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
        no_data = client.post("/api/training/run", json={"symbols": ["GBPUSD"]})
        # Historique trop court : détecté APRÈS le chargement, qui vit dans
        # le job (le POST répond tout de suite) → le job échoue proprement
        # avec un message actionnable, et le run est historisé « failed ».
        too_short = client.post(
            "/api/training/run",
            json={"symbols": ["EURUSD"], "timeframe": "D1", "folds": 20,
                  "strategy": "couleuvre_v0_1"},
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


# --- Mode poolé (modèle unique multi-actifs) -------------------------------


def test_entrainement_poole_tous_les_actifs(training_env: Path) -> None:
    """``symbols`` omis = TOUS les actifs avec historique ; le run est
    enregistré sous la sentinelle « ALL » et le rapport liste les actifs."""
    _write_history(training_env / "history", "GBPUSD")
    with TestClient(create_app()) as client:
        response = client.post("/api/training/run", json={"folds": 2})
        assert response.status_code == 200
        payload = response.json()
        job = _wait_for_job(client, payload["job_id"])
        runs = client.get("/api/training/runs").json()["runs"]

    assert job["status"] == "completed"
    assert job["result"]["symbol"] == "ALL"
    assert job["result"]["symbols"] == ["EURUSD", "GBPUSD"]
    assert "oos_by_symbol" in job["result"]
    assert runs[0]["symbol"] == "ALL"
    # La stratégie par défaut est désormais la mutualisée.
    assert runs[0]["strategy"] == "couleuvre_v0_2"


def test_run_poole_enregistre_les_actifs_entraines(training_env: Path) -> None:
    """Le run poolé persiste les actifs RÉELLEMENT vus : c'est ce que le live
    consulte avant de servir le modèle mutualisé à une paire."""
    from pyea.storage.storage_training_runs import latest_completed_run

    _write_history(training_env / "history", "GBPUSD")
    with TestClient(create_app()) as client:
        payload = client.post("/api/training/run", json={"folds": 2}).json()
        assert _wait_for_job(client, payload["job_id"])["status"] == "completed"
        run = latest_completed_run("couleuvre_v0_2", "ALL")

    assert run["trained_symbols"] == ["EURUSD", "GBPUSD"]


def test_champ_symbol_obsolete_refuse(training_env: Path) -> None:
    """L'ancien champ ``symbol`` (singulier) était ignoré en silence : un appel
    hérité lançait alors un run sur TOUS les actifs du disque. 422 explicite."""
    with TestClient(create_app()) as client:
        response = client.post(
            "/api/training/run", json={"symbol": "EURUSD", "folds": 3},
        )
    assert response.status_code == 422


def test_selection_vide_refusee(training_env: Path) -> None:
    """``symbols: []`` est une demande vide, pas un synonyme de « tous »."""
    with TestClient(create_app()) as client:
        response = client.post("/api/training/run", json={"symbols": []})
    assert response.status_code == 422


def test_strategie_mutualisee_refuse_un_seul_actif(training_env: Path) -> None:
    """Un seul historique local : la mutualisée refuse au lieu de basculer en
    mode par actif tout en annonçant « tous les actifs »."""
    with TestClient(create_app()) as client:
        response = client.post("/api/training/run", json={"folds": 2})
    assert response.status_code == 400
    assert "PLUSIEURS actifs" in response.json()["detail"]


def test_actif_trop_court_fait_echouer_le_run(training_env: Path) -> None:
    """Un actif demandé mais écarté pour historique trop court ne doit pas
    disparaître dans un log : le run échoue en le nommant."""
    _write_history(training_env / "history", "GBPUSD")
    # 3 bougies D1 pour GBPUSD seulement : sous le seuil folds × 20.
    index = pd.date_range("2020-01-01", periods=3 * 1440, freq="1min", tz="UTC")
    frame = pd.DataFrame(
        {"bid_open": 1.2, "bid_high": 1.2, "bid_low": 1.2, "bid_close": 1.2,
         "volume": 1.0},
        index=index,
    )
    target = year_file_path(training_env / "history", "USDCHF", 2020)
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(target)

    with TestClient(create_app()) as client:
        payload = client.post(
            "/api/training/run", json={"timeframe": "D1", "folds": 4},
        ).json()
        job = _wait_for_job(client, payload["job_id"])

    assert job["status"] == "failed"
    assert "USDCHF" in job["error"] and "trop court" in job["error"]


def test_strategie_mono_actif_refuse_le_pool(training_env: Path) -> None:
    """couleuvre_v0_1 s'entraîne par actif : plusieurs symboles → 400 clair."""
    _write_history(training_env / "history", "GBPUSD")
    with TestClient(create_app()) as client:
        response = client.post(
            "/api/training/run",
            json={"symbols": ["EURUSD", "GBPUSD"], "strategy": "couleuvre_v0_1"},
        )
    assert response.status_code == 400
    assert "par actif" in response.json()["detail"]
