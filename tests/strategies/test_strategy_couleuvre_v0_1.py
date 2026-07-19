"""Tests de Couleuvre_v0.1 (entraînement, inférence, non-fuite).

Le test cardinal (`test_pas_de_fuite_pnl_nul_sur_bruit`) entraîne le modèle
puis le backteste OUT-OF-SAMPLE sur du **bruit pur** : sans fuite, le taux
de gain OOS doit rester proche de 50 % — quelle que soit la sur-performance
in-sample (la démonstration même de l'utilité du walk-forward).
"""

import asyncio

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("lightgbm")

from pyea.backtest import BacktestEngine
from pyea.config.config_settings import get_settings
from pyea.core.core_domain import SignalAction, TickData
from pyea.risk.risk_manager import RiskManager
from pyea.strategies.strategy_couleuvre_v0_1 import CouleuvreV01


def _random_walk(n: int, seed: int) -> pd.DataFrame:
    """Marche aléatoire iid (aucune structure prévisible) avec OHLCV cohérent."""
    rng = np.random.default_rng(seed)
    index = pd.date_range("2022-01-03", periods=n, freq="1h", tz="UTC")
    close = 1.10 + rng.normal(0, 0.0006, n).cumsum()
    open_ = close + rng.normal(0, 0.0003, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.0003, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.0003, n))
    volume = rng.integers(50, 500, n).astype(float)
    return pd.DataFrame(
        {"bid_open": open_, "bid_high": high, "bid_low": low,
         "bid_close": close, "volume": volume},
        index=index,
    )


def _run_oos(strategy: CouleuvreV01, frame: pd.DataFrame):
    engine = BacktestEngine(strategy, RiskManager(get_settings()))
    return asyncio.run(engine.run("TEST", frame, "H1"))


def test_train_retourne_un_modele_et_un_rapport() -> None:
    strat = CouleuvreV01()
    report = asyncio.run(strat.train(_random_walk(4000, seed=1), {"fold": 1}))
    assert report["trained"] is True
    assert report["n_samples"] > 0
    assert 0.0 < report["label_balance"] < 1.0
    assert 0.0 <= report["train_accuracy"] <= 1.0
    assert len(report["top_features"]) == 8
    assert strat._model is not None


def test_jeu_trop_court_pas_de_modele() -> None:
    strat = CouleuvreV01()
    report = asyncio.run(strat.train(_random_walk(120, seed=2), {"fold": 1}))
    assert report["trained"] is False
    assert strat._model is None


def test_sans_modele_aucun_trade() -> None:
    # warmup avec frame mais sans entraînement → on_tick muet (comme le
    # backtest simple de la page /backtest sur une stratégie non entraînée).
    strat = CouleuvreV01()
    result = _run_oos(strat, _random_walk(500, seed=3))
    assert result.trades == []


def test_signaux_bien_formes() -> None:
    frame = _random_walk(4000, seed=4)
    strat = CouleuvreV01()
    asyncio.run(strat.train(frame.iloc[:2000], {"fold": 1}))
    test_frame = frame.iloc[2000:]
    asyncio.run(strat.warmup({"symbol": "TEST", "timeframe": "H1", "frame": test_frame}))
    signals = []
    for timestamp, row in test_frame.iterrows():
        tick = TickData(symbol="TEST", price=float(row["bid_close"]), timestamp=timestamp)
        sig = asyncio.run(strat.on_tick(tick))
        if sig is not None:
            signals.append((float(row["bid_close"]), sig))
    assert signals, "aucun signal émis"
    for price, sig in signals:
        assert sig.action in (SignalAction.ENTER_LONG, SignalAction.ENTER_SHORT)
        assert sig.stop_loss is not None and sig.take_profit is not None
        if sig.action == SignalAction.ENTER_LONG:
            assert sig.stop_loss < price < sig.take_profit
        else:
            assert sig.take_profit < price < sig.stop_loss


def test_pas_de_fuite_pnl_nul_sur_bruit() -> None:
    """Entraînement puis OOS sur bruit pur : taux de gain ≈ 50 %."""
    frame = _random_walk(9000, seed=123)
    half = len(frame) // 2
    strat = CouleuvreV01()
    asyncio.run(strat.train(frame.iloc[:half], {"fold": 1}))
    result = _run_oos(strat, frame.iloc[half:])
    stats = result.stats
    assert stats["trades"] > 50  # le modèle trade bien, il ne « devine » juste rien
    assert 0.40 <= stats["win_rate"] <= 0.60


def test_walkforward_bout_en_bout(tmp_path) -> None:
    from pyea.training import run_walkforward

    frame = _random_walk(6000, seed=7)
    report = run_walkforward(
        strategy_factory=CouleuvreV01,
        risk_manager=RiskManager(get_settings()),
        symbol="TEST",
        frame=frame,
        timeframe="H1",
        n_folds=3,
        artifacts_dir=tmp_path,
        progress=lambda payload: None,
        cancelled=lambda: False,
    )
    assert report["cancelled"] is False
    assert set(report["oos_stats"]) == {"trades", "total_pnl", "win_rate", "max_drawdown"}
    assert (tmp_path / "metadata.json").exists()
    assert (tmp_path / "fold_1" / "model.txt").exists()  # modèle par pli sauvé
    assert (tmp_path / "fold_1" / "features.json").exists()
