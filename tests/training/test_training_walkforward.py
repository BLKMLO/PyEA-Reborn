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
