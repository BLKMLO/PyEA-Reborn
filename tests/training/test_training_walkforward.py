"""Tests du découpage et de l'orchestration walk-forward."""

from pathlib import Path

import pandas as pd
import pytest

from pyea.config.config_settings import get_settings
from pyea.risk.risk_manager import RiskManager
from pyea.training import run_walkforward, split_walkforward


def _frame(bars: int) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=bars, freq="1h", tz="UTC")
    closes = [1.0 + 0.001 * i for i in range(bars)]
    return pd.DataFrame({"bid_close": closes}, index=index)


def test_split_ordre_temporel_et_couverture() -> None:
    frame = _frame(100)
    folds = split_walkforward(frame, n_folds=4)
    assert len(folds) == 4
    previous_test_end = None
    for train, test in folds:
        # Le train s'arrête exactement où le test commence (pas de fuite).
        assert train.index[-1] < test.index[0]
        # Fenêtre expansive : chaque train englobe le début de l'historique.
        assert train.index[0] == frame.index[0]
        # Les blocs de test sont consécutifs.
        if previous_test_end is not None:
            assert test.index[0] > previous_test_end
        previous_test_end = test.index[-1]
    # Le dernier bloc va jusqu'au bout de l'historique.
    assert folds[-1][1].index[-1] == frame.index[-1]


def test_split_historique_trop_court() -> None:
    with pytest.raises(ValueError, match="trop court"):
        split_walkforward(_frame(4), n_folds=10)


def test_run_walkforward_strategie_muette(tmp_path: Path) -> None:
    from pyea.strategies.strategy_registry import get_strategy

    events: list[dict] = []
    report = run_walkforward(
        strategy_factory=get_strategy("couleuvre_v0_1"),
        risk_manager=RiskManager(get_settings()),
        symbol="EURUSD",
        frame=_frame(200),
        timeframe="H1",
        n_folds=3,
        artifacts_dir=tmp_path / "run",
        progress=events.append,
        cancelled=lambda: False,
    )
    assert len(report["folds"]) == 3
    assert report["oos_stats"]["trades"] == 0
    # Profit factor agrégé exposé (None faute de trade OOS), jamais absent.
    assert "profit_factor" in report["oos_stats"]
    assert report["oos_stats"]["profit_factor"] is None
    assert report["cancelled"] is False
    # 2 événements de progression par pli (train + test).
    assert len(events) == 6
    # Sans modèle entraîné (jeu trop court), l'AUC OOS est honnêtement None.
    assert all(fold["oos_auc"] is None for fold in report["folds"])
    # Les artefacts sont archivés.
    assert (tmp_path / "run" / "metadata.json").exists()


def test_win_rate_oos_pondere_par_le_nombre_de_trades() -> None:
    """Le taux de gain OOS s'agrège sur TOUS les trades, pas en moyennant les
    taux par pli (comme le profit factor). Un pli à 1 trade gagnant ne doit pas
    peser autant qu'un pli à 100 trades à 50 %."""
    from pyea.training.training_walkforward import WalkForwardFold, _report

    # Pli 1 : 1 trade, 100 % gagnant. Pli 2 : 100 trades, 50 % gagnants.
    fold1 = WalkForwardFold(
        index=1, train_start="", train_end="", test_start="", test_end="",
        train_bars=0, test_bars=0,
        test_stats={"trades": 1, "total_pnl": 1.0, "win_rate": 1.0},
    )
    fold2 = WalkForwardFold(
        index=2, train_start="", train_end="", test_start="", test_end="",
        train_bars=0, test_bars=0,
        test_stats={"trades": 100, "total_pnl": 0.0, "win_rate": 0.5},
    )
    # 1 gagnant (pli 1) + 50 gagnants / 50 perdants (pli 2) = 51/101 trades.
    oos_pnls = [1.0] + [1.0] * 50 + [-1.0] * 50
    report = _report("EURUSD", "H1", [fold1, fold2], [], oos_pnls, cancelled=False)
    # Agrégat honnête = 51/101 ≈ 0,5050 ; la moyenne des taux par pli
    # (1,0 + 0,5)/2 = 0,75 aurait été trompeuse.
    assert report["oos_stats"]["win_rate"] == round(51 / 101, 4)
    assert report["oos_stats"]["trades"] == 101


def test_run_walkforward_annulation(tmp_path: Path) -> None:
    from pyea.strategies.strategy_registry import get_strategy

    report = run_walkforward(
        strategy_factory=get_strategy("couleuvre_v0_1"),
        risk_manager=RiskManager(get_settings()),
        symbol="EURUSD",
        frame=_frame(200),
        timeframe="H1",
        n_folds=3,
        artifacts_dir=tmp_path / "run",
        progress=lambda payload: None,
        cancelled=lambda: True,  # Annulé d'emblée.
    )
    assert report["cancelled"] is True
    assert report["folds"] == []


# --- Mode poolé (modèle unique multi-actifs) -------------------------------


def _frame_decale(bars: int, start: str, pente: float = 0.001) -> pd.DataFrame:
    index = pd.date_range(start, periods=bars, freq="1h", tz="UTC")
    closes = [1.0 + pente * i for i in range(bars)]
    return pd.DataFrame({"bid_close": closes}, index=index)


def test_split_frames_plage_commune() -> None:
    """La découpe poolée se fait sur la plage commune : un actif qui démarre
    plus tard est tranché aux MÊMES timestamps que les autres."""
    from pyea.training.training_walkforward import _split_frames

    frames = {
        "AAA": _frame_decale(200, "2024-01-01"),
        "BBB": _frame_decale(150, "2024-01-03"),  # démarre 2 jours plus tard
    }
    folds = _split_frames(frames, n_folds=2)
    assert len(folds) == 2
    for train, test in folds:
        # Plage commune : rien avant le début de BBB.
        assert train["AAA"].index[0] >= frames["BBB"].index[0]
        # Mêmes bornes temporelles pour les deux actifs.
        assert train["AAA"].index[-1] < test["AAA"].index[0]
        assert test["AAA"].index[0] == test["BBB"].index[0]


def test_split_frames_actif_hors_plage_ecarte() -> None:
    from pyea.training.training_walkforward import _split_frames

    frames = {
        "AAA": _frame_decale(200, "2024-01-01"),
        "BBB": _frame_decale(50, "2024-01-01"),
        "CCC": _frame_decale(50, "2025-06-01"),  # hors plage commune
    }
    folds = _split_frames(frames, n_folds=2)
    for train, test in folds:
        assert "CCC" not in train and "CCC" not in test


def test_run_walkforward_pooled_structure(tmp_path: Path) -> None:
    """Run poolé : un seul rapport sous « ALL », plis ventilés par actif,
    agrégats par actif honnêtes au niveau du rapport."""
    from pyea.strategies.strategy_registry import get_strategy
    from pyea.training import run_walkforward_pooled

    frames = {"AAA": _frame(200), "BBB": _frame_decale(200, "2024-01-01", pente=-0.001)}
    report = run_walkforward_pooled(
        strategy_factory=get_strategy("couleuvre_v0_2"),
        risk_manager=RiskManager(get_settings()),
        frames=frames,
        timeframe="H1",
        n_folds=3,
        artifacts_dir=tmp_path / "run",
        progress=lambda payload: None,
        cancelled=lambda: False,
    )
    assert report["symbol"] == "ALL"
    assert report["symbols"] == ["AAA", "BBB"]
    assert len(report["folds"]) == 3
    for fold in report["folds"]:
        # Ventilation par actif présente (même à 0 trade) ; Sharpe/SQN
        # honnêtement None en poolé (non fusionnables).
        assert set(fold["per_symbol"]) == {"AAA", "BBB"}
        assert fold["test_stats"]["sharpe_ratio"] is None
    assert set(report["oos_by_symbol"]) == {"AAA", "BBB"}
    for stats in report["oos_by_symbol"].values():
        assert set(stats) == {"trades", "total_pnl", "win_rate", "profit_factor"}
    assert (tmp_path / "run" / "metadata.json").exists()


def test_run_walkforward_pooled_exige_deux_actifs(tmp_path: Path) -> None:
    from pyea.strategies.strategy_registry import get_strategy
    from pyea.training import run_walkforward_pooled

    with pytest.raises(ValueError, match="au moins deux actifs"):
        run_walkforward_pooled(
            strategy_factory=get_strategy("couleuvre_v0_2"),
            risk_manager=RiskManager(get_settings()),
            frames={"SEUL": _frame(200)},
            timeframe="H1",
            n_folds=2,
            artifacts_dir=tmp_path / "run",
            progress=lambda payload: None,
            cancelled=lambda: False,
        )


def test_report_ventilation_par_actif_honnete() -> None:
    """La ventilation par actif agrège comme le global : taux de gain et PF
    sur TOUS les trades de l'actif (jamais de moyenne de ratios)."""
    from pyea.training.training_walkforward import _report

    report = _report(
        "ALL", "H1", [], [], [1.0, -1.0], cancelled=False,
        symbols=["AAA", "BBB"],
        by_symbol_pnls={"AAA": [1.0, 1.0, -1.0], "BBB": [-1.0, -1.0]},
    )
    par_actif = report["oos_by_symbol"]
    assert par_actif["AAA"]["trades"] == 3
    assert par_actif["AAA"]["win_rate"] == round(2 / 3, 4)
    assert par_actif["AAA"]["profit_factor"] == 2.0
    assert par_actif["BBB"]["win_rate"] == 0.0
    assert par_actif["BBB"]["profit_factor"] == 0.0  # aucun gain → PF nul
