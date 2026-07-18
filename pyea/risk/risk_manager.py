"""Gestion du risque : seul module autorisé à transformer un Signal en ordre.

Le flux est strict : Strategy → Signal → RiskManager → OrderRequest → Broker.
Aucun ordre ne part au broker sans passer ici.
"""

from __future__ import annotations

from pyea.config.config_settings import Settings
from pyea.core.core_domain import OrderRequest, Signal
from pyea.core.core_logging import get_logger

logger = get_logger(__name__)


class RiskManager:
    """Applique les limites de risque définies dans config.yaml (section risk)."""

    def __init__(self, settings: Settings) -> None:
        self._max_position_size = settings.risk_max_position_size
        self._max_daily_loss_pct = settings.risk_max_daily_loss_pct
        self._max_open_positions = settings.risk_max_open_positions

    async def evaluate(self, signal: Signal) -> OrderRequest | None:
        """Valide un signal et le convertit en ordre, ou le rejette (``None``).

        Plus tard : taille de position, perte journalière max, nombre de
        positions ouvertes, kill-switch global.
        """
        raise NotImplementedError("À implémenter avec la logique de trading.")
