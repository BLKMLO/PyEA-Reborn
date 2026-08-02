"""La sélection du modèle live prend le dernier run RÉUSSI d'un actif et, dans
ce run, le dernier pli disponible — sinon rien (jamais un modèle deviné)."""

from pathlib import Path

import pytest

from pyea.config.config_settings import get_settings
from pyea.live.live_models import resolve_live_model
from pyea.storage.storage_database import init_db
from pyea.storage.storage_training_runs import create_run, finish_run


@pytest.fixture
def tmp_db(tmp_path: Path):
    settings = get_settings()
    original = settings.database_url
    settings.database_url = f"sqlite:///{tmp_path}/test.db"
    init_db()
    yield
    settings.database_url = original
    init_db()


def _make_run(run_id: str, symbol: str, folds: int, artifacts: Path,
              status: str = "completed", timeframe: str = "H1") -> None:
    create_run(run_id, "couleuvre_v0_1", symbol, timeframe, folds, {})
    finish_run(run_id, status, {"trades": 1}, str(artifacts))


def _write_model(artifacts: Path, fold: int) -> None:
    (artifacts / f"fold_{fold}").mkdir(parents=True, exist_ok=True)
    (artifacts / f"fold_{fold}" / "model.txt").write_text("model", encoding="utf-8")


def test_aucun_run_aucun_modele(tmp_db: None) -> None:
    assert resolve_live_model("couleuvre_v0_1", "EURUSD") is None


def test_dernier_pli_disponible(tmp_db: None, tmp_path: Path) -> None:
    artifacts = tmp_path / "run-a"
    for fold in (1, 2, 3):
        _write_model(artifacts, fold)
    _make_run("run-a", "EURUSD", folds=3, artifacts=artifacts)

    model = resolve_live_model("couleuvre_v0_1", "EURUSD")
    assert model is not None
    assert model.fold == 3  # dernier pli = plus de données
    assert model.timeframe == "H1"
    assert model.model_path.name == "model.txt"


def test_saute_les_plis_sans_modele(tmp_db: None, tmp_path: Path) -> None:
    # Le dernier pli n'a pas produit de modèle (jeu trop court) → on prend le
    # plus haut pli qui en a un.
    artifacts = tmp_path / "run-b"
    _write_model(artifacts, 1)
    _write_model(artifacts, 2)  # fold 3 absent
    _make_run("run-b", "EURUSD", folds=3, artifacts=artifacts)

    model = resolve_live_model("couleuvre_v0_1", "EURUSD")
    assert model is not None and model.fold == 2


def test_run_non_reussi_ignore(tmp_db: None, tmp_path: Path) -> None:
    artifacts = tmp_path / "run-c"
    _write_model(artifacts, 1)
    _make_run("run-c", "EURUSD", folds=1, artifacts=artifacts, status="failed")
    assert resolve_live_model("couleuvre_v0_1", "EURUSD") is None


def test_run_le_plus_recent_gagne(tmp_db: None, tmp_path: Path) -> None:
    old = tmp_path / "run-old"
    new = tmp_path / "run-new"
    _write_model(old, 1)
    _write_model(new, 1)
    _make_run("aaa-old", "EURUSD", folds=1, artifacts=old)
    _make_run("zzz-new", "EURUSD", folds=1, artifacts=new, timeframe="H4")

    model = resolve_live_model("couleuvre_v0_1", "EURUSD")
    assert model is not None
    assert model.run_id == "zzz-new"  # created_at plus récent
    assert model.timeframe == "H4"


# --- Repli sur le run poolé « ALL » (modèle unique multi-actifs, v0_2) -----


def test_repli_sur_le_run_poole_all(tmp_db: None, tmp_path: Path) -> None:
    """Sans run propre au symbole, le run poolé ALL sert les actifs QU'IL A VUS."""
    artifacts = tmp_path / "run-pool"
    _write_model(artifacts, 1)
    _write_model(artifacts, 2)
    create_run("run-pool", "couleuvre_v0_2", "ALL", "H4", 2, {})
    finish_run("run-pool", "completed", {"trades": 1}, str(artifacts),
               trained_symbols=["EURUSD", "XAUUSD", "US500"])

    for symbol in ("EURUSD", "XAUUSD", "US500"):
        model = resolve_live_model("couleuvre_v0_2", symbol)
        assert model is not None
        assert model.run_id == "run-pool"
        assert model.fold == 2
        assert model.timeframe == "H4"
        assert model.symbol == symbol  # le symbole demandé, pas « ALL »


def test_le_run_du_symbole_est_prioritaire_sur_all(tmp_db: None, tmp_path: Path) -> None:
    """Un run propre au symbole (v0_1) prime sur le run poolé."""
    pooled = tmp_path / "run-pool"
    propre = tmp_path / "run-propre"
    _write_model(pooled, 1)
    _write_model(propre, 1)
    create_run("run-pool", "couleuvre_v0_2", "ALL", "H4", 1, {})
    finish_run("run-pool", "completed", {"trades": 1}, str(pooled),
               trained_symbols=["EURUSD", "GBPUSD"])
    create_run("run-propre", "couleuvre_v0_2", "EURUSD", "H1", 1, {})
    finish_run("run-propre", "completed", {"trades": 1}, str(propre))

    model = resolve_live_model("couleuvre_v0_2", "EURUSD")
    assert model is not None and model.run_id == "run-propre"
    # Un autre symbole du pool, lui, retombe bien sur le run poolé.
    assert resolve_live_model("couleuvre_v0_2", "GBPUSD").run_id == "run-pool"


def test_repli_poole_refuse_un_actif_jamais_entraine(
    tmp_db: None, tmp_path: Path
) -> None:
    """Le modèle mutualisé ne couvre QUE les actifs qu'il a vus.

    Une paire téléchargée après l'entraînement recevait sinon un modèle sans
    une seule bougie d'elle (catégorie « symbole » inconnue → code -1) et
    émettait de vrais ordres dessus.
    """
    artifacts = tmp_path / "run-pool"
    _write_model(artifacts, 1)
    create_run("run-pool", "couleuvre_v0_2", "ALL", "H4", 1, {})
    finish_run("run-pool", "completed", {"trades": 1}, str(artifacts),
               trained_symbols=["EURUSD", "GBPUSD"])

    assert resolve_live_model("couleuvre_v0_2", "EURUSD") is not None
    assert resolve_live_model("couleuvre_v0_2", "XAUUSD") is None


def test_repli_poole_refuse_un_run_sans_actifs_connus(
    tmp_db: None, tmp_path: Path
) -> None:
    """Run poolé sans liste d'actifs (antérieur à son enregistrement) : on ne
    peut RIEN prouver, donc on ne trade pas — jamais de modèle par défaut."""
    artifacts = tmp_path / "run-pool"
    _write_model(artifacts, 1)
    create_run("run-pool", "couleuvre_v0_2", "ALL", "H4", 1, {})
    finish_run("run-pool", "completed", {"trades": 1}, str(artifacts))

    assert resolve_live_model("couleuvre_v0_2", "EURUSD") is None
