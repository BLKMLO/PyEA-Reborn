"""Tests de l'initialisation de la base et de la micro-migration SQLite."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from pyea.config.config_settings import get_settings
from pyea.storage.storage_database import init_db
from pyea.storage.storage_training_runs import create_run, finish_run, list_runs


@pytest.fixture
def tmp_db(tmp_path: Path):
    """Base isolée pour le test, puis retour à la base configurée."""
    settings = get_settings()
    original_url = settings.database_url
    settings.database_url = f"sqlite:///{tmp_path}/test.db"
    yield tmp_path
    settings.database_url = original_url
    init_db()


def test_migration_ajoute_colonne_manquante(tmp_db: Path) -> None:
    # On simule une base d'une version ANTÉRIEURE : la table training_runs
    # existe mais sans la colonne oos_profit_factor.
    db_path = tmp_db / "test.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE training_runs ("
                "id VARCHAR(32) PRIMARY KEY, created_at DATETIME, "
                "strategy_name VARCHAR(64), symbol VARCHAR(32), "
                "timeframe VARCHAR(8), params_json VARCHAR(2048), "
                "folds INTEGER, status VARCHAR(16))"
            )
        )
    engine.dispose()

    # init_db() doit rattraper la colonne nullable manquante sans planter.
    init_db()
    columns = {col["name"] for col in inspect(engine).get_columns("training_runs")}
    assert "oos_profit_factor" in columns


def test_profit_factor_persiste_dans_l_historique(tmp_db: Path) -> None:
    init_db()
    create_run("run-1", "couleuvre_v0_1", "EURUSD", "H1", 3, {})
    finish_run(
        "run-1",
        "completed",
        oos_stats={
            "trades": 4,
            "total_pnl": 12.0,
            "win_rate": 0.5,
            "max_drawdown": -3.0,
            "profit_factor": 1.42,
        },
    )
    run = next(r for r in list_runs() if r["id"] == "run-1")
    assert run["oos_profit_factor"] == 1.42
