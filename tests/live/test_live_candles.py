"""L'agrégateur tick→bougie forme des bougies OHLCV correctes et les clôt au
changement de bucket."""

import pandas as pd
import pytest

from pyea.live.live_candles import CandleAggregator


def _ts(hour: int, minute: int = 0) -> pd.Timestamp:
    return pd.Timestamp(f"2022-01-03 {hour:02d}:{minute:02d}:00", tz="UTC")


def test_bougie_en_cours_retourne_none() -> None:
    agg = CandleAggregator("H1")
    assert agg.add(1.10, 1.0, _ts(9, 0)) is None      # ouvre le bucket 09:00
    assert agg.add(1.12, 2.0, _ts(9, 30)) is None      # même bucket
    assert agg.add(1.09, 1.0, _ts(9, 59)) is None      # même bucket


def test_changement_de_bucket_clot_la_bougie() -> None:
    agg = CandleAggregator("H1")
    agg.add(1.10, 1.0, _ts(9, 0))
    agg.add(1.15, 2.0, _ts(9, 20))   # high
    agg.add(1.05, 3.0, _ts(9, 40))   # low
    agg.add(1.11, 1.0, _ts(9, 59))   # close
    candle = agg.add(1.20, 1.0, _ts(10, 1))  # tick du bucket suivant → clôt 09:00
    assert candle is not None
    assert candle.start == _ts(9, 0)
    assert candle.open == 1.10
    assert candle.high == 1.15
    assert candle.low == 1.05
    assert candle.close == 1.11
    assert candle.volume == 7.0  # 1+2+3+1


def test_alignement_sur_la_grille_du_timeframe() -> None:
    # Le début de bougie est le floor du timeframe (comme resample_history) :
    # un premier tick à 09:37 appartient au bucket 09:00.
    agg = CandleAggregator("H1")
    agg.add(1.10, 1.0, _ts(9, 37))
    candle = agg.add(1.20, 1.0, _ts(10, 3))
    assert candle.start == _ts(9, 0)


def test_timeframe_non_agregeable_refuse() -> None:
    with pytest.raises(ValueError, match="non agrégable"):
        CandleAggregator("W1")
