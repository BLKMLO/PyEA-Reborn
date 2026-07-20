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


def test_load_history_ne_lit_que_les_annees_de_la_periode(tmp_path: Path) -> None:
    """Avec start/end, seuls les fichiers d'années chevauchant la période
    sont lus (charger 15 ans de M1 pour 6 mois gaspillait temps et mémoire).
    Le résultat reste identique à un chargement complet + slice."""
    import pandas as pd

    for year in (2019, 2020, 2021):
        frame = pd.DataFrame(
            {"bid_close": [float(year), float(year) + 0.5]},
            index=pd.to_datetime([f"{year}-03-01 00:00", f"{year}-09-01 00:00"], utc=True),
        )
        target = year_file_path(tmp_path, "EURUSD", year)
        target.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(target)

    start = pd.Timestamp("2020-01-01", tz="UTC")
    end = pd.Timestamp("2020-12-31", tz="UTC")
    loaded = load_history(tmp_path, "EURUSD", start, end)
    assert list(loaded["bid_close"]) == [2020.0, 2020.5]

    # Borne ouverte d'un côté : les années postérieures/antérieures suivent.
    assert len(load_history(tmp_path, "EURUSD", start, None)) == 4
    assert len(load_history(tmp_path, "EURUSD", None, end)) == 4

    # Période entièrement hors historique → erreur claire, pas de frame vide.
    import pytest

    with pytest.raises(FileNotFoundError):
        load_history(
            tmp_path, "EURUSD",
            pd.Timestamp("2025-01-01", tz="UTC"), pd.Timestamp("2025-12-31", tz="UTC"),
        )


def test_load_history_ignore_les_fichiers_parasites(tmp_path: Path) -> None:
    """Copie de sauvegarde manuelle dans le dossier : ignorée, pas de crash,
    pas de bougies dupliquées."""
    import pandas as pd

    frame = pd.DataFrame(
        {"bid_close": [1.1, 1.2]},
        index=pd.to_datetime(["2020-01-01 00:00", "2020-01-01 00:01"], utc=True),
    )
    target = year_file_path(tmp_path, "EURUSD", 2020)
    target.parent.mkdir(parents=True)
    frame.to_parquet(target)
    # L'utilisateur duplique le fichier « au cas où ».
    (tmp_path / "EURUSD" / "EURUSD_m1_backup.parquet").write_bytes(target.read_bytes())

    loaded = load_history(tmp_path, "EURUSD")
    assert len(loaded) == 2  # pas de doublons
    # Même avec une période (filtre par année) : pas de crash int("backup").
    loaded = load_history(
        tmp_path, "EURUSD",
        datetime(2020, 1, 1, tzinfo=timezone.utc),
        datetime(2020, 12, 31, tzinfo=timezone.utc),
    )
    assert len(loaded) == 2


def test_load_history_parquet_corrompu_message_actionnable(tmp_path: Path) -> None:
    import pytest

    target = year_file_path(tmp_path, "EURUSD", 2020)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"pas du parquet")
    with pytest.raises(ValueError, match="illisible.*EURUSD_m1_2020"):
        load_history(tmp_path, "EURUSD")


def test_load_history_periode_inversee_refusee(tmp_path: Path) -> None:
    import pandas as pd
    import pytest

    frame = pd.DataFrame(
        {"bid_close": [1.1]}, index=pd.to_datetime(["2020-01-01"], utc=True)
    )
    target = year_file_path(tmp_path, "EURUSD", 2020)
    target.parent.mkdir(parents=True)
    frame.to_parquet(target)
    with pytest.raises(ValueError, match="Période invalide"):
        load_history(
            tmp_path, "EURUSD",
            datetime(2020, 6, 1, tzinfo=timezone.utc),
            datetime(2020, 1, 1, tzinfo=timezone.utc),
        )


def test_download_history_valide_avant_de_telecharger(tmp_path: Path) -> None:
    """Faute de frappe dans un symbole ou années incohérentes : échec
    IMMÉDIAT avec message clair, avant le moindre appel réseau."""
    import asyncio

    import pytest

    from pyea.data.data_history_downloader import download_history

    with pytest.raises(KeyError, match="EURSUD"):
        asyncio.run(download_history(["EURUSD", "EURSUD"], 2020, 2021, tmp_path))
    with pytest.raises(ValueError, match="start_year=2022 > end_year=2020"):
        asyncio.run(download_history(["EURUSD"], 2022, 2020, tmp_path))
    with pytest.raises(ValueError, match="futur"):
        asyncio.run(download_history(["EURUSD"], 2050, 2060, tmp_path))
