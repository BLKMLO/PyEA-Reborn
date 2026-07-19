"""Moteur de backtest de PyEA.

Rejoue bougie par bougie un DataFrame d'historique (issu de
``load_history`` + ``resample_history``) à travers le MÊME flux que le
live : ``Strategy → Signal → RiskManager → OrderRequest`` — l'exécution
est simulée ici au lieu de partir chez le broker, mais aucun ordre ne
contourne le risk manager, même en simulation.

Modèle d'exécution v2 (toujours simple, à raffiner plus tard) :
- un « tick » par bougie, au prix de clôture bid — les décisions de la
  stratégie sont prises au close ;
- exécution immédiate au même prix (pas de slippage ni de spread) ;
- une position à la fois par backtest (plafond du RiskManager) ;
- **barrières intrabar (triple-barrier)** : si l'ordre porte un
  ``stop_loss`` / ``take_profit``, chaque bougie SUIVANTE est testée sur
  son high/low ; un franchissement clôture la position au prix de la
  barrière (pas au close). Si les deux barrières sont dans la même bougie,
  on suppose le stop touché d'abord (hypothèse conservatrice) ;
- **clôture forcée de fin de semaine** : une position encore ouverte à la
  dernière bougie de la semaine ISO est liquidée à son close (Couleuvre
  est un swing intra-semaine, jamais de portage sur le week-end) ;
- position résiduelle liquidée à la dernière bougie.

Frames sans high/low (ex. données de test réduites à ``bid_close``) : les
barrières retombent sur le close, ce qui les neutralise proprement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from pyea.core.core_domain import OrderRequest, OrderSide, Position, TickData
from pyea.core.core_logging import get_logger
from pyea.risk.risk_manager import RiskManager
from pyea.strategies.strategy_base import Strategy

logger = get_logger(__name__)

MAX_EQUITY_POINTS = 500  # Taille max de la courbe renvoyée à l'interface.


def _last_bars_of_week(index: pd.DatetimeIndex) -> list[bool]:
    """Marque, pour chaque bougie, si elle est la dernière de sa semaine ISO.

    Le forex Dukascopy n'a pas de bougie le week-end : une semaine se
    termine simplement quand la bougie suivante bascule sur une autre
    (année, semaine) ISO — robuste aux frontières d'année. La toute
    dernière bougie de la série reste ``False`` : la liquidation finale la
    couvre déjà (pas de double clôture).
    """
    n = len(index)
    if n == 0:
        return []
    iso = index.isocalendar()
    keys = list(zip(iso["year"].to_numpy(), iso["week"].to_numpy()))
    flags = [False] * n
    for i in range(n - 1):
        if keys[i] != keys[i + 1]:
            flags[i] = True
    return flags


@dataclass(frozen=True)
class BacktestTrade:
    """Un aller-retour complet (ouverture puis clôture)."""

    symbol: str
    side: str            # BUY / SELL (sens de l'ouverture)
    quantity: float
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    pnl: float


@dataclass
class BacktestResult:
    symbol: str
    timeframe: str
    bars: int
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)

    @property
    def stats(self) -> dict[str, Any]:
        pnls = [trade.pnl for trade in self.trades]
        wins = [p for p in pnls if p > 0]
        equity = [value for _, value in self.equity_curve]
        max_drawdown = 0.0
        peak = float("-inf")
        for value in equity:
            peak = max(peak, value)
            max_drawdown = max(max_drawdown, peak - value)
        return {
            "bars": self.bars,
            "trades": len(pnls),
            "total_pnl": round(sum(pnls), 5),
            "win_rate": round(len(wins) / len(pnls), 4) if pnls else None,
            "max_drawdown": round(max_drawdown, 5),
        }


@dataclass
class _OpenPosition:
    side: OrderSide
    quantity: float
    entry_time: datetime
    entry_price: float
    stop_loss: float | None = None
    take_profit: float | None = None

    @property
    def signed_quantity(self) -> float:
        return self.quantity if self.side == OrderSide.BUY else -self.quantity

    def pnl_at(self, price: float) -> float:
        direction = 1 if self.side == OrderSide.BUY else -1
        return (price - self.entry_price) * direction * self.quantity

    def barrier_exit(self, high: float, low: float) -> float | None:
        """Prix de sortie si une barrière est franchie dans [low, high], sinon None.

        Convention conservatrice : si stop ET take-profit sont tous deux
        dans le range de la bougie, on retient le stop (perte supposée
        touchée en premier, l'ordre intrabar réel étant inconnu).
        """
        if self.side == OrderSide.BUY:
            if self.stop_loss is not None and low <= self.stop_loss:
                return self.stop_loss
            if self.take_profit is not None and high >= self.take_profit:
                return self.take_profit
        else:  # SELL : stop au-dessus, take-profit en dessous.
            if self.stop_loss is not None and high >= self.stop_loss:
                return self.stop_loss
            if self.take_profit is not None and low <= self.take_profit:
                return self.take_profit
        return None


class BacktestEngine:
    """Simule la boucle de trading sur un historique donné."""

    def __init__(self, strategy: Strategy, risk_manager: RiskManager) -> None:
        self._strategy = strategy
        self._risk = risk_manager

    async def run(
        self, symbol: str, frame: pd.DataFrame, timeframe: str
    ) -> BacktestResult:
        result = BacktestResult(symbol=symbol, timeframe=timeframe, bars=len(frame))
        position: _OpenPosition | None = None
        realized = 0.0
        n = len(frame)
        equity_step = max(1, n // MAX_EQUITY_POINTS)
        last_of_week = _last_bars_of_week(frame.index)
        has_hl = "bid_high" in frame.columns and "bid_low" in frame.columns

        # Le frame complet est fourni à warmup : une stratégie à modèle
        # (Couleuvre) y pré-calcule ses features/probas. Le calcul reste
        # sans fuite — la décision à la bougie t ne lit QUE la ligne t, et
        # features(t) est identique qu'on la calcule sur tout le frame ou
        # sur son seul préfixe (stabilité par préfixe garantie et testée).
        await self._strategy.warmup(
            {"symbol": symbol, "timeframe": timeframe, "frame": frame}
        )
        try:
            for i, (timestamp, row) in enumerate(frame.iterrows()):
                price = float(row["bid_close"])
                high = float(row["bid_high"]) if has_hl else price
                low = float(row["bid_low"]) if has_hl else price

                # 1) Barrières intrabar : une position ouverte à une bougie
                # ANTÉRIEURE (le check tourne avant l'entrée de cette bougie)
                # est clôturée si le high/low franchit son stop / take-profit.
                if position is not None:
                    barrier_price = position.barrier_exit(high, low)
                    if barrier_price is not None:
                        position, realized = self._close_position(
                            result, position, realized, timestamp, barrier_price
                        )

                # 2) Décision de la stratégie, prise au close.
                tick = TickData(symbol=symbol, price=price, timestamp=timestamp)
                signal = await self._strategy.on_tick(tick)
                if signal is not None:
                    open_positions = (
                        [
                            Position(
                                symbol=symbol,
                                quantity=position.signed_quantity,
                                average_price=position.entry_price,
                            )
                        ]
                        if position
                        else []
                    )
                    order = await self._risk.evaluate(signal, open_positions)
                    if order is not None:
                        position, realized = self._execute(
                            result, position, realized, order, timestamp, price
                        )

                # 3) Clôture forcée de fin de semaine (jamais de portage
                # week-end). Le garde entry_time évite un aller-retour
                # dégénéré si l'entrée vient d'avoir lieu sur cette bougie.
                if (
                    position is not None
                    and last_of_week[i]
                    and position.entry_time != timestamp
                ):
                    position, realized = self._close_position(
                        result, position, realized, timestamp, price
                    )

                if i % equity_step == 0 or i == n - 1:
                    unrealized = position.pnl_at(price) if position else 0.0
                    result.equity_curve.append((timestamp, round(realized + unrealized, 5)))

            # Liquidation de la position résiduelle sur la dernière bougie.
            if position is not None and n:
                last_time = frame.index[-1]
                last_price = float(frame["bid_close"].iloc[-1])
                position, realized = self._close_position(
                    result, position, realized, last_time, last_price
                )
        finally:
            await self._strategy.shutdown()

        logger.info(
            "Backtest %s %s : %d bougies, %d trades, P&L %.5f",
            symbol, timeframe, result.bars, len(result.trades), realized,
        )
        return result

    def _execute(
        self,
        result: BacktestResult,
        position: _OpenPosition | None,
        realized: float,
        order: OrderRequest,
        timestamp: datetime,
        price: float,
    ) -> tuple[_OpenPosition | None, float]:
        """Applique un ordre simulé : ouverture, ou clôture de la position."""
        if position is None:
            return (
                _OpenPosition(
                    side=order.side,
                    quantity=order.quantity,
                    entry_time=timestamp,
                    entry_price=price,
                    stop_loss=order.stop_loss,
                    take_profit=order.take_profit,
                ),
                realized,
            )
        # Ordre opposé à la position ouverte = clôture.
        return self._close_position(result, position, realized, timestamp, price)

    def _close_position(
        self,
        result: BacktestResult,
        position: _OpenPosition,
        realized: float,
        timestamp: datetime,
        price: float,
    ) -> tuple[None, float]:
        """Clôt la position (signal opposé, barrière, fin de semaine ou
        liquidation finale) et enregistre l'aller-retour."""
        pnl = position.pnl_at(price)
        result.trades.append(
            BacktestTrade(
                symbol=result.symbol,
                side=position.side.value,
                quantity=position.quantity,
                entry_time=position.entry_time,
                entry_price=position.entry_price,
                exit_time=timestamp,
                exit_price=price,
                pnl=round(pnl, 5),
            )
        )
        return None, realized + pnl
