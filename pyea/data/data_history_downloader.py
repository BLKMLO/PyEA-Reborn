"""Téléchargement des données historiques (bougies M1) depuis Dukascopy.

Source : le flux public ``datafeed.dukascopy.com`` (gratuit, sans compte),
qui sert des fichiers ``.bi5`` (LZMA) par jour et par instrument, avec un
historique remontant bien avant 2010 pour le forex.

Ce module contient toute la logique (mapping d'instruments, décodage bi5,
assemblage, stockage Parquet) pour deux raisons :
- le script racine ``download_history.py`` n'est qu'un habillage CLI ;
- la future interface de backtest rechargera les mêmes fichiers via
  ``load_history()`` sans dépendre du script.

Particularités du flux Dukascopy :
- le MOIS est indexé à partir de 0 dans les URLs (00 = janvier) ;
- prix stockés en entiers : prix réel = entier / 10^decimal_factor ;
- un fichier par jour et par côté (BID / ASK) ; 404 = pas de données
  (week-end, jour férié, ou antérieur au début de l'historique).

Stockage : ``<data_dir>/<SYMBOLE>/<SYMBOLE>_m1_<année>.parquet``, index
UTC, colonnes ``{bid,ask}_{open,high,low,close}`` + ``volume``.
"""

from __future__ import annotations

import asyncio
import lzma
import struct
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd

from pyea.core.core_logging import get_logger

logger = get_logger(__name__)

BASE_URL = "https://datafeed.dukascopy.com/datafeed"

# Format binaire d'une bougie bi5 : 24 octets big-endian.
# (offset_secondes_depuis_minuit, open, close, low, high, volume)
_CANDLE_STRUCT = struct.Struct(">IIIIIf")

_SIDES = ("BID", "ASK")


@dataclass(frozen=True)
class InstrumentSpec:
    """Correspondance symbole PyEA → instrument Dukascopy."""

    dukascopy_id: str
    decimal_factor: int  # prix réel = entier stocké / 10^decimal_factor

    @property
    def price_divisor(self) -> float:
        return 10.0**self.decimal_factor


# Symboles supportés. Forex : facteur 5 (3 pour les paires JPY).
# Métaux et indices : facteur 3. Ajouter un actif = ajouter une ligne ici
# (id Dukascopy visible sur leur page "historical data feed").
INSTRUMENT_SPECS: dict[str, InstrumentSpec] = {
    # Majeures
    "EURUSD": InstrumentSpec("EURUSD", 5),
    "GBPUSD": InstrumentSpec("GBPUSD", 5),
    "USDJPY": InstrumentSpec("USDJPY", 3),
    "USDCHF": InstrumentSpec("USDCHF", 5),
    "USDCAD": InstrumentSpec("USDCAD", 5),
    "AUDUSD": InstrumentSpec("AUDUSD", 5),
    "NZDUSD": InstrumentSpec("NZDUSD", 5),
    # Croisées EUR
    "EURGBP": InstrumentSpec("EURGBP", 5),
    "EURJPY": InstrumentSpec("EURJPY", 3),
    "EURCHF": InstrumentSpec("EURCHF", 5),
    "EURAUD": InstrumentSpec("EURAUD", 5),
    "EURCAD": InstrumentSpec("EURCAD", 5),
    "EURNZD": InstrumentSpec("EURNZD", 5),
    # Croisées GBP
    "GBPJPY": InstrumentSpec("GBPJPY", 3),
    "GBPCHF": InstrumentSpec("GBPCHF", 5),
    "GBPAUD": InstrumentSpec("GBPAUD", 5),
    "GBPCAD": InstrumentSpec("GBPCAD", 5),
    "GBPNZD": InstrumentSpec("GBPNZD", 5),
    # Autres croisées
    "AUDJPY": InstrumentSpec("AUDJPY", 3),
    "AUDCHF": InstrumentSpec("AUDCHF", 5),
    "AUDCAD": InstrumentSpec("AUDCAD", 5),
    "AUDNZD": InstrumentSpec("AUDNZD", 5),
    "NZDJPY": InstrumentSpec("NZDJPY", 3),
    "NZDCHF": InstrumentSpec("NZDCHF", 5),
    "NZDCAD": InstrumentSpec("NZDCAD", 5),
    "CADJPY": InstrumentSpec("CADJPY", 3),
    "CADCHF": InstrumentSpec("CADCHF", 5),
    "CHFJPY": InstrumentSpec("CHFJPY", 3),
    # Métaux
    "XAUUSD": InstrumentSpec("XAUUSD", 3),
    "XAGUSD": InstrumentSpec("XAGUSD", 3),
    # Indices (historique Dukascopy souvent plus court que le forex)
    "US500": InstrumentSpec("USA500IDXUSD", 3),
    "US30": InstrumentSpec("USA30IDXUSD", 3),
    "NAS100": InstrumentSpec("USATECHIDXUSD", 3),
}


def get_spec(symbol: str) -> InstrumentSpec:
    try:
        return INSTRUMENT_SPECS[symbol.upper()]
    except KeyError:
        supported = ", ".join(sorted(INSTRUMENT_SPECS))
        raise KeyError(f"Symbole inconnu '{symbol}'. Supportés : {supported}")


def candle_url(spec: InstrumentSpec, day: date, side: str) -> str:
    """URL du fichier M1 d'un jour. ATTENTION : mois Dukascopy = 0-based."""
    return (
        f"{BASE_URL}/{spec.dukascopy_id}/{day.year}/{day.month - 1:02d}/"
        f"{day.day:02d}/{side}_candles_min_1.bi5"
    )


def decode_candles(payload: bytes, spec: InstrumentSpec, day: date) -> pd.DataFrame:
    """Décode un fichier bi5 de bougies M1 en DataFrame OHLCV (index UTC)."""
    if not payload:
        return pd.DataFrame()
    raw = lzma.decompress(payload)
    if len(raw) % _CANDLE_STRUCT.size:
        raise ValueError(
            f"Fichier bi5 corrompu pour {spec.dukascopy_id} {day}: "
            f"{len(raw)} octets (multiple de {_CANDLE_STRUCT.size} attendu)."
        )
    day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    divisor = spec.price_divisor
    rows = [
        (
            day_start + timedelta(seconds=offset),
            open_ / divisor,
            high / divisor,
            low / divisor,
            close / divisor,
            volume,
        )
        for offset, open_, close, low, high, volume in _CANDLE_STRUCT.iter_unpack(raw)
    ]
    frame = pd.DataFrame(
        rows, columns=["time", "open", "high", "low", "close", "volume"]
    )
    return frame.set_index("time")


def year_file_path(data_dir: Path, symbol: str, year: int) -> Path:
    return data_dir / symbol / f"{symbol}_m1_{year}.parquet"


async def _fetch_day(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    spec: InstrumentSpec,
    day: date,
    side: str,
    retries: int = 3,
) -> bytes | None:
    """Télécharge un fichier jour/côté. ``None`` si absent (404)."""
    url = candle_url(spec, day, side)
    async with semaphore:
        for attempt in range(retries + 1):
            try:
                response = await client.get(url)
            except httpx.HTTPError as exc:
                if attempt == retries:
                    raise
                logger.warning("Réseau (%s), retry %d/%d : %s", exc, attempt + 1, retries, url)
                await asyncio.sleep(2**attempt)
                continue
            if response.status_code == 404:
                return None
            if response.status_code == 200:
                return response.content
            if attempt == retries:
                response.raise_for_status()
            await asyncio.sleep(2**attempt)
    return None


def _days_of_year(year: int, today: date) -> list[date]:
    day = date(year, 1, 1)
    days: list[date] = []
    while day.year == year and day < today:
        days.append(day)
        day += timedelta(days=1)
    return days


async def download_year(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    symbol: str,
    year: int,
) -> pd.DataFrame:
    """Assemble bid + ask M1 d'une année entière pour un symbole."""
    spec = get_spec(symbol)
    days = _days_of_year(year, date.today())
    sides_frames: dict[str, list[pd.DataFrame]] = {side: [] for side in _SIDES}

    tasks = {
        (day, side): asyncio.create_task(_fetch_day(client, semaphore, spec, day, side))
        for day in days
        for side in _SIDES
    }
    for (day, side), task in tasks.items():
        payload = await task
        if payload:
            frame = decode_candles(payload, spec, day)
            if not frame.empty:
                sides_frames[side].append(frame)

    if not sides_frames["BID"]:
        return pd.DataFrame()

    bid = pd.concat(sides_frames["BID"]).sort_index()
    bid.columns = [f"bid_{col}" for col in bid.columns]
    volume = bid.pop("bid_volume").rename("volume")
    merged = bid
    if sides_frames["ASK"]:
        ask = pd.concat(sides_frames["ASK"]).sort_index()
        ask = ask.drop(columns=["volume"])
        ask.columns = [f"ask_{col}" for col in ask.columns]
        merged = bid.join(ask, how="left")
    merged["volume"] = volume
    return merged


async def download_history(
    symbols: list[str],
    start_year: int,
    end_year: int,
    data_dir: Path,
    force: bool = False,
    concurrency: int = 8,
) -> dict[str, list[int]]:
    """Télécharge tout l'historique demandé. Retourne {symbole: années écrites}.

    Reprise incrémentale : une année déjà présente sur disque est sautée
    (sauf ``force``) ; l'année en cours est toujours re-téléchargée.
    """
    written: dict[str, list[int]] = {symbol: [] for symbol in symbols}
    failed: list[tuple[str, int, str]] = []
    current_year = date.today().year
    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(timeout=30.0) as client:
        for symbol in symbols:
            get_spec(symbol)  # Échec immédiat si symbole inconnu.
            for year in range(start_year, end_year + 1):
                target = year_file_path(data_dir, symbol, year)
                if target.exists() and not force and year != current_year:
                    logger.info("%s %d déjà présent, sauté.", symbol, year)
                    continue
                # Une année en échec (réseau après retries, fichier corrompu…)
                # ne doit PAS faire perdre les heures de téléchargement déjà
                # faites : on journalise, on continue, on résume à la fin —
                # relancer le script ne reprendra que les années manquantes.
                try:
                    frame = await download_year(client, semaphore, symbol, year)
                except Exception as exc:  # noqa: BLE001 — résumé en fin de run.
                    logger.error("%s %d : échec (%s) — année sautée.", symbol, year, exc)
                    failed.append((symbol, year, str(exc)))
                    continue
                if frame.empty:
                    logger.warning("%s %d : aucune donnée (historique indisponible ?).", symbol, year)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                frame.to_parquet(target)
                written[symbol].append(year)
                logger.info(
                    "%s %d : %d bougies M1 → %s (1re : %s bid_close=%s)",
                    symbol, year, len(frame), target,
                    frame.index[0], frame["bid_close"].iloc[0],
                )
    if failed:
        logger.warning(
            "%d année(s) en échec — relancer le script pour les reprendre : %s",
            len(failed),
            ", ".join(f"{symbol} {year}" for symbol, year, _ in failed),
        )
    return written


# Timeframes supportés par resample_history (M1 = natif, pas de conversion).
_TIMEFRAME_RULES = {
    "M1": "1min",
    "M5": "5min",
    "M15": "15min",
    "M30": "30min",
    "H1": "1h",
    "H4": "4h",
    "D1": "1D",
    "W1": "1W",
    "MN1": "1ME",
}


def resample_history(frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Ré-échantillonne un DataFrame M1 (issu de load_history) vers un
    timeframe supérieur (M5, M15, M30, H1, H4, D1, W1, MN1).

    OHLC bid/ask : first/max/min/last ; volume : somme. Les périodes sans
    aucune bougie M1 (week-ends, jours fériés) sont retirées.
    """
    key = timeframe.upper()
    try:
        rule = _TIMEFRAME_RULES[key]
    except KeyError:
        supported = ", ".join(_TIMEFRAME_RULES)
        raise ValueError(f"Timeframe inconnu '{timeframe}'. Supportés : {supported}")
    if key == "M1":
        return frame

    aggregations: dict[str, str] = {}
    for side in ("bid", "ask"):
        if f"{side}_open" in frame.columns:
            aggregations[f"{side}_open"] = "first"
            aggregations[f"{side}_high"] = "max"
            aggregations[f"{side}_low"] = "min"
            aggregations[f"{side}_close"] = "last"
    if "volume" in frame.columns:
        aggregations["volume"] = "sum"

    resampled = frame.resample(rule).agg(aggregations)
    return resampled.dropna(subset=[next(iter(aggregations))])


def load_history(
    data_dir: Path,
    symbol: str,
    start: datetime | None = None,
    end: datetime | None = None,
) -> pd.DataFrame:
    """Recharge l'historique M1 d'un symbole (backtest / entraînement)."""
    files = sorted((data_dir / symbol).glob(f"{symbol}_m1_*.parquet"))
    if not files:
        raise FileNotFoundError(
            f"Aucun historique pour {symbol} dans {data_dir} — "
            "lancer `python download_history.py` d'abord."
        )
    # Ne lit que les fichiers d'années chevauchant [start, end] : charger
    # 15 ans de M1 pour un backtest de 6 mois gaspille temps et mémoire.
    if start is not None or end is not None:
        def year_of(file: Path) -> int:
            return int(file.stem.rsplit("_", 1)[1])

        files = [
            file
            for file in files
            if (start is None or year_of(file) >= start.year)
            and (end is None or year_of(file) <= end.year)
        ]
        if not files:
            raise FileNotFoundError(
                f"Aucun historique pour {symbol} sur la période demandée."
            )
    frame = pd.concat(pd.read_parquet(file) for file in files).sort_index()
    return frame.loc[start:end]
