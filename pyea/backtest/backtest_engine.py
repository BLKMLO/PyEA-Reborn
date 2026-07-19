"""Moteur de backtest de PyEA.

Rejoue bougie par bougie un DataFrame d'historique (issu de
``load_history`` + ``resample_history``) à travers le MÊME flux que le
live : ``Strategy → Signal → RiskManager → OrderRequest`` — l'exécution
est simulée ici au lieu de partir chez le broker, mais aucun ordre ne
contourne le risk manager, même en simulation.

Modèle d'exécution v1 (volontairement simple, à raffiner plus tard) :
- un « tick » par bougie, au prix de clôture bid ;
- exécution immédiate au même prix (pas de slippage ni de spread) ;
- une position à la fois par backtest (plafond du RiskManager) ;
- position résiduelle liquidée à la dernière bougie.
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

    @property
    def signed_quantity(self) -> float:
        return self.quantity if self.side == OrderSide.BUY else -self.quantity

    def pnl_at(self, price: float) -> float:
        direction = 1 if self.side == OrderSide.BUY else -1
        return (price - self.entry_price) * direction * self.quantity


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
        equity_step = max(1, len(frame) // MAX_EQUITY_POINTS)

        await self._strategy.warmup({})
        try:
            for i, (timestamp, row) in enumerate(frame.iterrows()):
                price = float(row["bid_close"])
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

                if i % equity_step == 0 or i == len(frame) - 1:
                    unrealized = position.pnl_at(price) if position else 0.0
                    result.equity_curve.append((timestamp, round(realized + unrealized, 5)))

            # Liquidation de la position résiduelle sur la dernière bougie.
            if position is not None and len(frame):
                last_time = frame.index[-1]
                last_price = float(frame["bid_close"].iloc[-1])
                close_order = OrderRequest(
                    symbol=symbol,
                    side=OrderSide.SELL if position.side == OrderSide.BUY else OrderSide.BUY,
                    quantity=position.quantity,
                )
                position, realized = self._execute(
                    result, position, realized, close_order, last_time, last_price
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
                ),
                realized,
            )
        # Ordre opposé à la position ouverte = clôture.
        pnl = position.pnl_at(price)
        result.trades.append(
            BacktestTrade(
                symbol=order.symbol,
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
