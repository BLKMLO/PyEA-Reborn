"""Tests de Couleuvre_v0.2 — UN SEUL modèle LightGBM mutualisé multi-actifs.

Différences de définition vs v0.1 (gelée) : entraînement poolé sur tous les
symboles avec une feature d'identité ``symbol`` (catégorielle native
LightGBM), seuils plus sélectifs (0.60/0.40). Les propriétés cardinales
restent exigées : non-fuite (AUC OOS ≈ 0,5 sur bruit), équivalence
live/backtest, artefacts par pli.
"""

import asyncio
import json

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("lightgbm")

from pyea.backtest import BacktestEngine
from pyea.config.config_settings import get_settings
from pyea.core.core_domain import SignalAction, TickData
from pyea.risk.risk_manager import RiskManager
from pyea.strategies.strategy_couleuvre_v0_2 import (
    ENTER_LONG_THRESHOLD,
    ENTER_SHORT_THRESHOLD,
    MODEL_FEATURE_COLUMNS,
    CouleuvreV02,
)


def _random_walk(n: int, seed: int, start: str = "2022-01-03") -> pd.DataFrame:
    """Marche aléatoire iid (aucune structure prévisible) avec OHLCV cohérent."""
    rng = np.random.default_rng(seed)
    index = pd.date_range(start, periods=n, freq="1h", tz="UTC")
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


def _run_oos(strategy: CouleuvreV02, frame: pd.DataFrame, symbol: str = "TEST"):
    engine = BacktestEngine(strategy, RiskManager(get_settings()))
    return engine.run(symbol, frame, "H1")


# ------------------------------------------------------------------ entraînement

def test_train_poole_deux_symboles(tmp_path) -> None:
    """Le cœur de v0.2 : UN modèle entraîné sur DEUX actifs, feature ``symbol``."""
    strat = CouleuvreV02()
    frames = {"AAA": _random_walk(3000, seed=1), "BBB": _random_walk(3000, seed=2)}
    report = asyncio.run(strat.train(frames, {"fold": 1, "model_dir": str(tmp_path)}))
    assert report["trained"] is True
    assert report["n_symbols"] == 2
    assert report["symbols"] == ["AAA", "BBB"]
    assert report["n_features"] == len(MODEL_FEATURE_COLUMNS) == 35
    assert strat._model is not None
    # La liste des catégories est persistée : sans elle, les codes entiers de
    # la feature catégorielle différeraient entre train et prédiction.
    meta = json.loads((tmp_path / "features.json").read_text(encoding="utf-8"))
    assert meta["feature_columns"][-1] == "symbol"
    assert meta["symbol_categories"] == ["AAA", "BBB"]
    assert meta["enter_long_threshold"] == ENTER_LONG_THRESHOLD


def test_train_mono_dataframe_accepte() -> None:
    """Un DataFrame seul (outillage générique, walk-forward mono-symbole)."""
    strat = CouleuvreV02()
    report = asyncio.run(
        strat.train(_random_walk(3000, seed=3), {"fold": 1, "symbol": "SOLO"})
    )
    assert report["trained"] is True
    assert report["n_symbols"] == 1
    assert report["symbols"] == ["SOLO"]


def test_jeu_trop_court_pas_de_modele() -> None:
    strat = CouleuvreV02()
    report = asyncio.run(strat.train(_random_walk(120, seed=4), {"fold": 1}))
    assert report["trained"] is False
    assert strat._model is None


def test_sans_modele_aucun_trade() -> None:
    strat = CouleuvreV02()
    result = _run_oos(strat, _random_walk(500, seed=5))
    assert result.trades == []


# ------------------------------------------------------------------- décision

def test_seuils_plus_selectifs_que_v0_1() -> None:
    """0.60/0.40 : une proba de 0.57 (qui déclenchait en v0.1) ne déclenche plus."""
    strat = CouleuvreV02()
    assert strat._decide("T", 0.57, 1.0, 0.01) is None
    assert strat._decide("T", 0.43, 1.0, 0.01) is None
    long_sig = strat._decide("T", ENTER_LONG_THRESHOLD, 1.0, 0.01)
    short_sig = strat._decide("T", ENTER_SHORT_THRESHOLD, 1.0, 0.01)
    assert long_sig is not None and long_sig.action == SignalAction.ENTER_LONG
    assert short_sig is not None and short_sig.action == SignalAction.ENTER_SHORT
    # Barrières 1.5 × ATR, du bon côté.
    assert long_sig.stop_loss < 1.0 < long_sig.take_profit
    assert short_sig.take_profit < 1.0 < short_sig.stop_loss


def test_signaux_bien_formes_en_backtest() -> None:
    frame = _random_walk(4000, seed=6)
    strat = CouleuvreV02()
    asyncio.run(strat.train({"TEST": frame.iloc[:2000]}, {"fold": 1}))
    test_frame = frame.iloc[2000:]
    asyncio.run(strat.warmup({"symbol": "TEST", "timeframe": "H1", "frame": test_frame}))
    for timestamp, row in test_frame.iterrows():
        tick = TickData(symbol="TEST", price=float(row["bid_close"]), timestamp=timestamp)
        sig = asyncio.run(strat.on_tick(tick))
        if sig is None:
            continue
        price = float(row["bid_close"])
        if sig.action == SignalAction.ENTER_LONG:
            assert sig.stop_loss < price < sig.take_profit
        else:
            assert sig.take_profit < price < sig.stop_loss


# -------------------------------------------------------------------- non-fuite

def test_oos_auc_sans_modele_renvoie_none() -> None:
    strat = CouleuvreV02()
    frame = _random_walk(600, seed=21)
    assert strat.oos_auc(frame, frame.index) is None


def test_oos_auc_poolee_mesure_le_skill_reel() -> None:
    """Poolée sur deux actifs bruités : AUC IS élevée (mémorisation) mais AUC
    OOS ≈ 0,5 — l'écart mesure le surapprentissage, pas une fuite."""
    frames = {"AAA": _random_walk(5000, seed=22), "BBB": _random_walk(5000, seed=23)}
    half = len(frames["AAA"]) // 2
    strat = CouleuvreV02()
    report = asyncio.run(strat.train(
        {s: f.iloc[:half] for s, f in frames.items()}, {"fold": 1}
    ))
    test_frames = {s: f.iloc[half:] for s, f in frames.items()}
    test_index = {s: f.index for s, f in test_frames.items()}
    auc_oos = strat.oos_auc(test_frames, test_index)
    assert auc_oos is not None
    assert 0.0 <= auc_oos <= 1.0
    assert 0.40 <= auc_oos <= 0.60  # bruit : pas de pouvoir prédictif
    assert report["train_auc"] > 0.6  # mémorisation in-sample


def test_pas_de_fuite_pnl_nul_sur_bruit() -> None:
    """Entraînement poolé puis OOS sur bruit pur : si le modèle trade, son
    taux de gain reste ≈ 50 % (l'abstention est aussi un résultat honnête)."""
    frames = {"AAA": _random_walk(9000, seed=123), "BBB": _random_walk(9000, seed=124)}
    half = len(frames["AAA"]) // 2
    strat = CouleuvreV02()
    asyncio.run(strat.train({s: f.iloc[:half] for s, f in frames.items()}, {"fold": 1}))
    result = _run_oos(strat, frames["AAA"].iloc[half:], symbol="AAA")
    stats = result.stats
    if stats["trades"] >= 30:  # assez de trades pour mesurer quelque chose
        assert 0.40 <= stats["win_rate"] <= 0.60


# ------------------------------------------------------- équivalence live/backtest

def _close_only_trend(n: int, seed: int) -> pd.DataFrame:
    """Close-only avec COMPOSANTE PRÉVISIBLE (sinus) : sans structure, l'early
    stopping laisse le modèle sans conviction (aucune décision à comparer)."""
    rng = np.random.default_rng(seed)
    index = pd.date_range("2022-01-03", periods=n, freq="1h", tz="UTC")
    i = np.arange(n)
    close = 1.10 + 0.003 * np.sin(2 * np.pi * i / 96) + rng.normal(0, 0.0002, n).cumsum()
    volume = rng.integers(50, 500, n).astype(float)
    return pd.DataFrame({"bid_close": close, "volume": volume}, index=index)


def test_live_inference_equivaut_au_backtest(tmp_path) -> None:
    """Même exigence que v0.1 : l'inférence live reproduit le lookup backtest
    à ≥ 98 % (résiduel = indicateurs récursifs sur tampon glissant, assumé)."""
    frame = _close_only_trend(4000, seed=11)
    train, live_frame = frame.iloc[:2000], frame.iloc[2000:]

    strat_bt = CouleuvreV02()
    report = asyncio.run(strat_bt.train(
        {"T": train}, {"fold": 1, "model_dir": str(tmp_path)}
    ))
    model_path = report["model_path"]

    asyncio.run(strat_bt.warmup({"symbol": "T", "timeframe": "H1", "frame": live_frame}))
    bt: dict = {}
    for ts, row in live_frame.iterrows():
        sig = asyncio.run(strat_bt.on_tick(
            TickData(symbol="T", price=float(row["bid_close"]), timestamp=ts)))
        bt[ts] = None if sig is None else sig.action.value

    strat_lv = CouleuvreV02()
    asyncio.run(strat_lv.warmup(
        {"symbol": "T", "live": True, "timeframe": "H1", "model_path": model_path}))
    rows = list(live_frame.iterrows())
    lv: dict = {}
    for i, (ts, row) in enumerate(rows):
        sig = asyncio.run(strat_lv.on_tick(TickData(
            symbol="T", price=float(row["bid_close"]),
            volume=float(row["volume"]), timestamp=ts)))
        if i > 0:  # ce tick clôt la bougie précédente
            lv[rows[i - 1][0]] = None if sig is None else sig.action.value

    common = [ts for ts in lv if ts in bt]
    agree = sum(1 for ts in common if lv[ts] == bt[ts])
    assert len(common) > 500
    assert agree / len(common) >= 0.98      # équivalence quasi exacte
    assert any(v for v in lv.values())      # le live décide bien (pas tout None)


def test_live_sans_modele_reste_muette() -> None:
    strat = CouleuvreV02()
    asyncio.run(strat.warmup({"symbol": "T", "live": True, "timeframe": "H1"}))
    frame = _close_only_trend(300, seed=5)
    for ts, row in frame.iterrows():
        sig = asyncio.run(strat.on_tick(
            TickData(symbol="T", price=float(row["bid_close"]), timestamp=ts)))
        assert sig is None


# ------------------------------------------------------------------ walk-forward

def test_walkforward_bout_en_bout(tmp_path) -> None:
    from pyea.training import run_walkforward

    frame = _random_walk(6000, seed=7)
    report = run_walkforward(
        strategy_factory=CouleuvreV02,
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
    assert (tmp_path / "metadata.json").exists()
    assert (tmp_path / "fold_1" / "model.txt").exists()
    assert (tmp_path / "fold_1" / "features.json").exists()
    for fold in report["folds"]:
        assert fold["oos_auc"] is not None
        assert 0.0 <= fold["oos_auc"] <= 1.0


def test_definition_du_modele() -> None:
    definition = CouleuvreV02().model_definition()
    assert definition["n_features"] == 35
    assert definition["pooled"] is True
    assert definition["enter_long_threshold"] == 0.60
    assert definition["enter_short_threshold"] == 0.40
    assert definition["recommended_timeframe"] == "H4"


def test_registre_contient_les_deux_versions() -> None:
    import pyea.strategies  # noqa: F401 — déclenche les enregistrements
    from pyea.strategies.strategy_registry import get_strategy, list_strategies

    assert "couleuvre_v0_1" in list_strategies()
    assert "couleuvre_v0_2" in list_strategies()
    assert get_strategy("couleuvre_v0_2") is CouleuvreV02
