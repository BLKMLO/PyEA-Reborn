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


def _labels_reference(frame: pd.DataFrame, atr_mult: float, max_hold_days: int):
    """Réimplémentation naïve bougie par bougie (spécification du labeling),
    étalon de l'implémentation vectorisée par chunks."""
    from pyea.strategies.strategy_couleuvre_features import atr_series

    high = frame["bid_high"].to_numpy()
    low = frame["bid_low"].to_numpy()
    close = frame["bid_close"].to_numpy()
    atr = atr_series(frame).to_numpy()
    ts = frame.index.asi8
    horizon = max_hold_days * 86_400 * 1_000_000_000
    n = len(frame)
    labels = np.full(n, np.nan)
    barriers = np.empty(n, dtype=object)
    for t in range(n):
        if not np.isfinite(atr[t]) or atr[t] <= 0:
            continue
        upper = close[t] + atr_mult * atr[t]
        lower = close[t] - atr_mult * atr[t]
        end = np.searchsorted(ts, ts[t] + horizon, side="right") - 1
        if end <= t:
            continue
        label, barrier = None, None
        for j in range(t + 1, end + 1):
            up_hit, down_hit = high[j] >= upper, low[j] <= lower
            if up_hit and down_hit:
                label, barrier = (1, "tp") if close[j] >= close[t] else (0, "sl")
                break
            if up_hit:
                label, barrier = 1, "tp"
                break
            if down_hit:
                label, barrier = 0, "sl"
                break
        if label is None:
            label, barrier = (1 if close[end] >= close[t] else 0), "time"
        labels[t] = label
        barriers[t] = barrier
    return labels, barriers


def test_scan_par_chunks_identique_a_la_reference() -> None:
    """L'optimisation par chunks numpy doit produire EXACTEMENT les mêmes
    labels/barrières que le scan bougie par bougie, y compris la règle de
    départage quand les deux barrières tombent dans la même bougie."""
    rng = np.random.default_rng(3)
    n = 600
    index = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    close = 1.0 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    # Grandes mèches : force des cas où les DEUX barrières sont dans la
    # même bougie (départage par le close) et des franchissements tardifs.
    wick = np.abs(rng.normal(0, 0.004, n))
    frame = pd.DataFrame(
        {"bid_high": close + wick, "bid_low": close - wick, "bid_close": close},
        index=index,
    )
    lab = triple_barrier_labels(frame)
    ref_labels, ref_barriers = _labels_reference(frame, 1.5, 5)
    np.testing.assert_array_equal(lab["label"].to_numpy(), ref_labels)
    defined = ~np.isnan(ref_labels)  # hors labels indéfinis (None vs NaN)
    assert list(lab["barrier"].to_numpy()[defined]) == list(ref_barriers[defined])
    # Le jeu couvre bien les trois issues (sinon le test ne prouve rien).
    assert {"tp", "sl"} <= set(lab["barrier"].dropna())
