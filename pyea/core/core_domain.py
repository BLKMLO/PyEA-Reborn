"""Types du domaine partagés entre stratégie, risque, brokers et API.

Ces dataclasses sont le langage commun du système : les modules ne
s'échangent jamais de dicts anonymes, uniquement ces types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class SignalAction(str, Enum):
    ENTER_LONG = "ENTER_LONG"
    ENTER_SHORT = "ENTER_SHORT"
    EXIT = "EXIT"
    HOLD = "HOLD"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class TickData:
    """Un point de marché reçu du broker."""

    symbol: str
    price: float
    volume: float | None = None
    timestamp: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class Signal:
    """Décision émise par une stratégie, à valider par le risk management.

    ``stop_loss`` / ``take_profit`` : niveaux de prix des barrières
    proposées par la stratégie (triple-barrier de Couleuvre — TP/SL
    proportionnels à l'ATR). Optionnels : une stratégie sans barrières les
    laisse à ``None``. Le RiskManager les reporte sur l'``OrderRequest``.
    """

    strategy_name: str
    symbol: str
    action: SignalAction
    confidence: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    timestamp: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class OrderRequest:
    """Ordre demandé au broker (après validation risque).

    ``stop_loss`` / ``take_profit`` : barrières de l'ordre bracket, validées
    par le RiskManager. En live elles deviendront des ordres attachés (IB
    bracket) ; en backtest le moteur les teste en intrabar (high/low).
    """

    symbol: str
    side: OrderSide
    quantity: float
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None


@dataclass(frozen=True)
class Position:
    """Position ouverte telle que rapportée par le broker."""

    symbol: str
    quantity: float
    average_price: float
    unrealized_pnl: float | None = None
