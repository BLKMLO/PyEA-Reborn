"""Ingestion des données de marché.

Rôle : s'abonner au flux de prix du ``BrokerGateway`` actif, normaliser
les ticks en ``TickData`` et les republier sur le bus d'événements
(topic ``market.tick``) pour la stratégie et le dashboard.

Squelette volontairement vide : la logique sera branchée quand la
connexion IB réelle sera implémentée.
"""

from __future__ import annotations

from pyea.brokers.broker_gateway import BrokerGateway
from pyea.core.core_events import EventBus
from pyea.core.core_logging import get_logger

logger = get_logger(__name__)


class MarketDataFeed:
    """Pompe les ticks du broker vers le bus d'événements."""

    def __init__(self, gateway: BrokerGateway, bus: EventBus) -> None:
        self._gateway = gateway
        self._bus = bus
        self._symbols: list[str] = []

    async def start(self, symbols: list[str]) -> None:
        """Démarre les souscriptions de marché pour ``symbols``."""
        raise NotImplementedError("À implémenter avec la connexion broker réelle.")

    async def stop(self) -> None:
        """Coupe proprement toutes les souscriptions."""
        raise NotImplementedError("À implémenter avec la connexion broker réelle.")
