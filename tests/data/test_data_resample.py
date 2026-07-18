"""Tests du ré-échantillonnage M1 → timeframes supérieurs (H1, etc.)."""

import pandas as pd
import pytest

from pyea.data.data_history_downloader import resample_history


def _m1_frame(minutes: int) -> pd.DataFrame:
    index = pd.date_range("2020-01-06 00:00", periods=minutes, freq="1min", tz="UTC")
    closes = [1.1000 + i * 0.0001 for i in range(minutes)]
    return pd.DataFrame(
        {
            "bid_open": closes,
            "bid_high": [c + 0.0002 for c in closes],
            "bid_low": [c - 0.0002 for c in closes],
            "bid_close": closes,
            "ask_open": [c + 0.0001 for c in closes],
            "ask_high": [c + 0.0003 for c in closes],
            "ask_low": [c - 0.0001 for c in closes],
            "ask_close": [c + 0.0001 for c in closes],
            "volume": [1.0] * minutes,
        },
        index=index,
    )


def test_resample_h1_ohlc_et_volume() -> None:
    h1 = resample_history(_m1_frame(120), "H1")
    assert len(h1) == 2
    first = h1.iloc[0]
    assert first["bid_open"] == pytest.approx(1.1000)   # première M1 de l'heure
    assert first["bid_close"] == pytest.approx(1.1059)  # dernière M1 de l'heure
    assert first["bid_high"] == pytest.approx(1.1061)   # max des highs
    assert first["bid_low"] == pytest.approx(1.0998)    # min des lows
    assert first["volume"] == pytest.approx(60.0)


def test_resample_supprime_les_periodes_vides() -> None:
    frame = _m1_frame(60)
    # Un trou de 5 h (marché fermé) ne doit pas produire de lignes vides.
    shifted = frame.copy()
    shifted.index = shifted.index + pd.Timedelta(hours=6)
    d1 = resample_history(pd.concat([frame, shifted]), "H1")
    assert len(d1) == 2


def test_resample_tous_les_timeframes_supportes() -> None:
    frame = _m1_frame(240)
    for timeframe, expected_rows in [("M5", 48), ("M15", 16), ("M30", 8), ("H4", 1), ("D1", 1), ("W1", 1), ("MN1", 1)]:
        result = resample_history(frame, timeframe)
        assert len(result) == expected_rows, timeframe
        # Invariants OHLC : le tout premier open et le tout dernier close survivent.
        assert result["bid_open"].iloc[0] == frame["bid_open"].iloc[0]
        assert result["bid_close"].iloc[-1] == frame["bid_close"].iloc[-1]


def test_resample_m1_est_identite_et_timeframe_inconnu_rejete() -> None:
    frame = _m1_frame(3)
    assert resample_history(frame, "m1") is frame
    with pytest.raises(ValueError, match="Timeframe inconnu"):
        resample_history(frame, "H2")
