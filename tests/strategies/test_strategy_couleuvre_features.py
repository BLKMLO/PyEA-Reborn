"""Tests du module de features de Couleuvre.

Le test central est la **stabilité par préfixe** : garantie qu'aucune
feature à la bougie t ne dépend d'une bougie future (absence de fuite).
"""

import numpy as np
import pandas as pd
import pytest

from pyea.strategies.strategy_couleuvre_features import (
    FEATURE_COLUMNS,
    WARMUP_BARS,
    compute_features,
)


def _ohlcv_frame(n: int = 300, seed: int = 0, freq: str = "1h") -> pd.DataFrame:
    """Frame OHLCV synthétique cohérent (marche aléatoire, high≥corps≥low)."""
    rng = np.random.default_rng(seed)
    index = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")
    close = 1.10 + rng.normal(0, 0.001, n).cumsum()
    open_ = close + rng.normal(0, 0.0005, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.0004, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.0004, n))
    volume = rng.integers(100, 1000, n).astype(float)
    return pd.DataFrame(
        {
            "bid_open": open_,
            "bid_high": high,
            "bid_low": low,
            "bid_close": close,
            "volume": volume,
        },
        index=index,
    )


def test_colonnes_et_index() -> None:
    frame = _ohlcv_frame()
    feats = compute_features(frame)
    assert list(feats.columns) == FEATURE_COLUMNS
    assert feats.index.equals(frame.index)
    assert len(feats) == len(frame)


def test_pas_de_fuite_temporelle_stabilite_par_prefixe() -> None:
    """compute_features(frame)[:k] doit être IDENTIQUE à
    compute_features(frame[:k]) : une feature passée ne bouge pas quand on
    ajoute des bougies futures → aucune information du futur n'y fuit."""
    frame = _ohlcv_frame(n=300, seed=7)
    full = compute_features(frame)
    for k in (WARMUP_BARS + 5, 150, 299):
        prefix = compute_features(frame.iloc[:k])
        pd.testing.assert_frame_equal(full.iloc[:k], prefix)


def test_features_valides_apres_chauffe() -> None:
    frame = _ohlcv_frame(n=200)
    feats = compute_features(frame)
    # Aucune ligne pleinement valide avant la fin de chauffe attendue.
    assert feats.iloc[WARMUP_BARS:].notna().all().all()


def test_aucun_inf_dans_la_sortie() -> None:
    feats = compute_features(_ohlcv_frame())
    assert not np.isinf(feats.to_numpy(dtype=float)).any()


def test_retour_log_1() -> None:
    closes = [100.0, 110.0, 121.0, 121.0]
    index = pd.date_range("2024-01-01", periods=4, freq="1h", tz="UTC")
    frame = pd.DataFrame({"bid_close": closes}, index=index)
    feats = compute_features(frame)
    assert feats["ret_log_1"].iloc[1] == pytest.approx(np.log(110 / 100))
    assert feats["ret_log_1"].iloc[2] == pytest.approx(np.log(121 / 110))
    assert feats["ret_log_1"].iloc[3] == pytest.approx(0.0)


def test_range_pos_borne_0_1() -> None:
    frame = _ohlcv_frame(n=120, seed=3)
    rp = compute_features(frame)["range_pos_20"].dropna()
    # Position du close dans [low_20, high_20] : toujours dans [0, 1].
    assert (rp >= -1e-9).all() and (rp <= 1 + 1e-9).all()


def test_rsi_serie_croissante_sature_a_100() -> None:
    closes = list(np.linspace(1.0, 2.0, 60))  # strictement croissant
    index = pd.date_range("2024-01-01", periods=60, freq="1h", tz="UTC")
    feats = compute_features(pd.DataFrame({"bid_close": closes}, index=index))
    assert feats["rsi_14"].iloc[-1] == pytest.approx(100.0)


def test_features_calendrier() -> None:
    index = pd.DatetimeIndex(
        [
            "2024-01-01 03:00",  # lundi, 03h → dow0, j-4, session Asie(0)
            "2024-01-02 22:00",  # mardi, 22h → dow1, j-3, session creux(3)
            "2024-01-03 14:00",  # mercredi, 14h → dow2, j-2, session US(2)
            "2024-01-05 10:00",  # vendredi, 10h → dow4, j-0, session Europe(1)
        ],
        tz="UTC",
    )
    frame = pd.DataFrame({"bid_close": [1.0, 1.0, 1.0, 1.0]}, index=index)
    feats = compute_features(frame)
    assert list(feats["dow"]) == [0, 1, 2, 4]
    assert list(feats["days_to_friday"]) == [4, 3, 2, 0]
    assert list(feats["hour_utc"]) == [3, 22, 14, 10]
    assert list(feats["session"]) == [0, 3, 2, 1]


def test_frame_degrade_close_seul() -> None:
    # Frame réduit à bid_close (comme les tests du moteur) : ne casse pas,
    # colonnes complètes, high/low/open retombent sur le close.
    closes = list(1.10 + np.random.default_rng(1).normal(0, 0.001, 100).cumsum())
    index = pd.date_range("2024-01-01", periods=100, freq="1h", tz="UTC")
    feats = compute_features(pd.DataFrame({"bid_close": closes}, index=index))
    assert list(feats.columns) == FEATURE_COLUMNS
    assert len(feats) == 100


def test_index_non_temporel_rejete() -> None:
    frame = pd.DataFrame({"bid_close": [1.0, 1.1, 1.2]})  # RangeIndex
    with pytest.raises(TypeError):
        compute_features(frame)
