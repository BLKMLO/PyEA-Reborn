"""Tests du téléchargeur d'historique (décodage bi5, URLs, config)."""

import lzma
import struct
from datetime import date, datetime, timezone
from pathlib import Path

from pyea.config.config_settings import get_settings
from pyea.data.data_history_downloader import (
    candle_url,
    decode_candles,
    get_spec,
    load_history,
    year_file_path,
)


def _fake_bi5(candles: list[tuple[int, int, int, int, int, float]]) -> bytes:
    raw = b"".join(struct.pack(">IIIIIf", *candle) for candle in candles)
    return lzma.compress(raw, format=lzma.FORMAT_ALONE)


def test_candle_url_mois_zero_base() -> None:
    # Janvier doit donner 00, décembre 11 — piège classique du flux Dukascopy.
    spec = get_spec("EURUSD")
    assert "/EURUSD/2020/00/15/BID_candles_min_1.bi5" in candle_url(spec, date(2020, 1, 15), "BID")
    assert "/2020/11/01/ASK_candles_min_1.bi5" in candle_url(spec, date(2020, 12, 1), "ASK")


def test_decode_candles_scaling_et_index() -> None:
    # EURUSD facteur 5 : 108500 → 1.08500. Ordre bi5 : O, C, L, H.
    payload = _fake_bi5([(60, 108500, 108550, 108450, 108600, 12.5)])
    frame = decode_candles(payload, get_spec("EURUSD"), date(2020, 3, 2))
    assert frame.index[0] == datetime(2020, 3, 2, 0, 1, tzinfo=timezone.utc)
    assert frame.iloc[0]["open"] == 1.085
    assert frame.iloc[0]["close"] == 1.0855
    assert frame.iloc[0]["low"] == 1.0845
    assert frame.iloc[0]["high"] == 1.086


def test_decode_candles_us500_facteur_1000() -> None:
    payload = _fake_bi5([(0, 2950500, 2951000, 2950000, 2952000, 100.0)])
    frame = decode_candles(payload, get_spec("US500"), date(2020, 3, 2))
    assert frame.iloc[0]["open"] == 2950.5


def test_instruments_de_la_config_tous_supportes() -> None:
    # Tout symbole listé dans config.yaml doit être connu du downloader.
    for symbol in get_settings().history_instruments:
        assert get_spec(symbol) is not None


def test_load_history_relit_les_parquets(tmp_path: Path) -> None:
    import pandas as pd

    frame = pd.DataFrame(
        {"bid_close": [1.1, 1.2]},
        index=pd.to_datetime(["2020-01-01 00:00", "2020-01-01 00:01"], utc=True),
    )
    target = year_file_path(tmp_path, "EURUSD", 2020)
    target.parent.mkdir(parents=True)
    frame.to_parquet(target)

    loaded = load_history(tmp_path, "EURUSD")
    assert len(loaded) == 2
    assert loaded["bid_close"].iloc[1] == 1.2
