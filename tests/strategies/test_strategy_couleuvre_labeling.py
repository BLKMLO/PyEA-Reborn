"""Tests du labeling triple-barrier de Couleuvre."""

import numpy as np
import pandas as pd
import pytest

from pyea.strategies.strategy_couleuvre_labeling import triple_barrier_labels


def _trend_frame(slope: float, n: int = 200) -> pd.DataFrame:
    """Tendance linéaire (slope par bougie) + range fixe → ATR défini."""
    index = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    close = 1.0 + slope * np.arange(n)
    return pd.DataFrame(
        {
            "bid_high": close + 0.001,
            "bid_low": close - 0.001,
            "bid_close": close,
        },
        index=index,
    )


def test_forme_et_index() -> None:
    frame = _trend_frame(0.01)
    lab = triple_barrier_labels(frame)
    assert list(lab.columns) == ["label", "barrier", "atr"]
    assert lab.index.equals(frame.index)


def test_hausse_franche_barriere_haute() -> None:
    lab = triple_barrier_labels(_trend_frame(0.01))
    defined = lab.dropna(subset=["label"])
    assert (defined["label"] == 1).all()
    # Une hausse ne touche jamais le stop (les dernières bougies, à fenêtre
    # avant trop courte, expirent sur la barrière temps).
    assert set(defined["barrier"]) <= {"tp", "time"}


def test_baisse_franche_barriere_basse() -> None:
    lab = triple_barrier_labels(_trend_frame(-0.01))
    defined = lab.dropna(subset=["label"])
    assert (defined["label"] == 0).all()
    assert set(defined["barrier"]) <= {"sl", "time"}


def test_prix_plat_barriere_temps() -> None:
    # Close constant, range étroit : aucune barrière ATR touchée → temps.
    index = pd.date_range("2024-01-01", periods=200, freq="1h", tz="UTC")
    frame = pd.DataFrame(
        {"bid_high": 1.0001, "bid_low": 0.9999, "bid_close": 1.0}, index=index
    )
    lab = triple_barrier_labels(frame)
    defined = lab.dropna(subset=["label"])
    assert (defined["barrier"] == "time").all()
    assert (defined["label"] == 1).all()  # close[end] >= close[t]


def test_dernieres_bougies_sans_fenetre_sont_nan() -> None:
    lab = triple_barrier_labels(_trend_frame(0.01))
    assert np.isnan(lab["label"].iloc[-1])  # aucune bougie avant


def test_index_non_temporel_rejete() -> None:
    frame = pd.DataFrame({"bid_close": [1.0, 1.1, 1.2]})
    with pytest.raises(TypeError):
        triple_barrier_labels(frame)
