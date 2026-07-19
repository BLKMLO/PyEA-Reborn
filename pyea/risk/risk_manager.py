"""Gestion du risque : seul module autorisé à transformer un Signal en ordre.

Le flux est strict : Strategy → Signal → RiskManager → OrderRequest → Broker.
Aucun ordre ne part au broker (réel OU simulé en backtest) sans passer ici.

Version minimale (v1) : taille fixe et plafond de positions ouvertes.
À enrichir plus tard : perte journalière max, kill-switch, sizing dynamique.
"""

from __future__ import annotations

from pyea.config.config_settings import Settings
from pyea.core.core_domain import OrderRequest, OrderSide, Position, Signal, SignalAction
from pyea.core.core_logging import get_logger

logger = get_logger(__name__)


class RiskManager:
    """Applique les limites de risque définies dans config.yaml (section risk)."""

    def __init__(self, settings: Settings) -> None:
        self._max_position_size = settings.risk_max_position_size
        self._max_daily_loss_pct = settings.risk_max_daily_loss_pct
        self._max_open_positions = settings.risk_max_open_positions

    async def evaluate(
        self, signal: Signal, open_positions: list[Position]
    ) -> OrderRequest | None:
        """Valide un signal et le convertit en ordre, ou le rejette (``None``).

        ``open_positions`` = positions actuellement ouvertes sur le compte
        (réelles en live, simulées en backtest).
        """
        if signal.action == SignalAction.HOLD:
            return None

        if signal.action == SignalAction.EXIT:
            position = next(
                (p for p in open_positions if p.symbol == signal.symbol), None
            )
            if position is None:
                return None  # Rien à fermer.
            side = OrderSide.SELL if position.quantity > 0 else OrderSide.BUY
            return OrderRequest(
                symbol=signal.symbol, side=side, quantity=abs(position.quantity)
            )

        # Entrées : refusées au-delà du plafond de positions ouvertes.
        if len(open_positions) >= self._max_open_positions:
            logger.info(
                "Signal %s %s rejeté : %d position(s) ouverte(s) (max %d).",
                signal.action.value, signal.symbol,
                len(open_positions), self._max_open_positions,
            )
            return None
        side = (
            OrderSide.BUY
            if signal.action == SignalAction.ENTER_LONG
            else OrderSide.SELL
        )
        return OrderRequest(
            symbol=signal.symbol, side=side, quantity=self._max_position_size
        )
