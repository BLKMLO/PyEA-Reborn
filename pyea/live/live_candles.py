"""Agrégation tick → bougie pour l'inférence live.

Le backtest pousse une bougie déjà formée à la stratégie (un « tick » par
bougie, au close). En LIVE, le broker envoie des ticks continus : il faut les
agréger en bougies OHLCV du timeframe du modèle AVANT de pouvoir calculer les
features de Couleuvre (indexées par bougie).

``CandleAggregator`` fait exactement cela, et rien d'autre : il accumule les
ticks d'un bucket temporel et **émet la bougie CLOSE au moment où un tick d'un
nouveau bucket arrive** (donc la décision se prend juste après la clôture, de
façon strictement causale). Les buckets sont alignés sur la même grille que
``resample_history`` (``Timestamp.floor(freq)`` avec les mêmes règles de
timeframe) pour que la bougie live prolonge sans couture l'historique
ré-échantillonné servant de chauffe.

Composant générique et réutilisable (indépendant de toute stratégie) — testé
seul avec des ticks synthétiques.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from pyea.data.data_history_downloader import _TIMEFRAME_RULES

#: Timeframes agrégables en live : ceux dont ``floor`` a un sens (grille fixe).
#: W1/MN1 (buckets à origine variable) sont hors périmètre du live intra-semaine
#: de Couleuvre — refusés explicitement plutôt que mal alignés.
_LIVE_FLOORABLE = {"M1", "M5", "M15", "M30", "H1", "H4", "D1"}


@dataclass
class Candle:
    """Bougie OHLCV close, horodatée par le DÉBUT de son bucket."""

    start: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float


class CandleAggregator:
    """Accumule des ticks en bougies d'un timeframe donné."""

    def __init__(self, timeframe: str) -> None:
        key = timeframe.upper()
        if key not in _LIVE_FLOORABLE:
            supported = ", ".join(sorted(_LIVE_FLOORABLE))
            raise ValueError(
                f"Timeframe '{timeframe}' non agrégable en live. "
                f"Supportés : {supported}."
            )
        self._freq = _TIMEFRAME_RULES[key]
        self._start: pd.Timestamp | None = None
        self._open = self._high = self._low = self._close = 0.0
        self._volume = 0.0

    def add(
        self,
        price: float,
        volume: float | None,
        timestamp: datetime | None = None,
    ) -> Candle | None:
        """Intègre un tick ; retourne la bougie CLOSE si le bucket a changé.

        Le tick qui ouvre un nouveau bucket clôt le précédent : la bougie
        retournée est complète (aucun tick futur ne la modifiera). Entre deux
        bascules de bucket, retourne ``None``.
        """
        ts = pd.Timestamp(timestamp if timestamp is not None else datetime.now(timezone.utc))
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        bucket = ts.floor(self._freq)
        vol = float(volume) if volume is not None else 0.0

        if self._start is None:  # tout premier tick
            self._begin(bucket, price, vol)
            return None
        if bucket == self._start:  # même bougie : mise à jour
            self._high = max(self._high, price)
            self._low = min(self._low, price)
            self._close = price
            self._volume += vol
            return None
        # Nouveau bucket : on clôt la bougie courante, on démarre la suivante.
        closed = self._snapshot()
        self._begin(bucket, price, vol)
        return closed

    def _begin(self, bucket: pd.Timestamp, price: float, volume: float) -> None:
        self._start = bucket
        self._open = self._high = self._low = self._close = price
        self._volume = volume

    def _snapshot(self) -> Candle:
        assert self._start is not None
        return Candle(
            start=self._start,
            open=self._open,
            high=self._high,
            low=self._low,
            close=self._close,
            volume=self._volume,
        )
