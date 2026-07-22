"""Ingestion des données de marché.

Rôle : s'abonner au flux de prix du ``BrokerGateway`` actif, normaliser les
ticks en ``TickData`` et les republier sur le bus d'événements (topic
``market.tick``) pour les consommateurs (stratégie via ``LiveTradingEngine``,
dashboard via WebSocket) — qui ne connaissent NI le broker NI le feed.

Le feed est **agnostique du broker** : il ne fait que déléguer à
``gateway.subscribe_market_data(symbol, on_tick)`` et relayer chaque tick sur
le bus. C'est la gateway (IB, MetaTrader…) qui porte le détail du flux ; tant
que la sienne n'est pas câblée, ``start`` remonte l'erreur honnêtement (aucun
tick fabriqué).
"""

from __future__ import annotations

from pyea.brokers.broker_gateway import BrokerGateway
from pyea.core.core_domain import TickData
from pyea.core.core_events import TOPIC_TICK, EventBus
from pyea.core.core_logging import get_logger

logger = get_logger(__name__)


class MarketDataFeed:
    """Pompe les ticks du broker vers le bus d'événements."""

    def __init__(self, gateway: BrokerGateway, bus: EventBus) -> None:
        self._gateway = gateway
        self._bus = bus
        self._symbols: list[str] = []

    async def start(self, symbols: list[str]) -> None:
        """Démarre les souscriptions de marché pour ``symbols``.

        Chaque tick reçu du broker est relayé sur le bus (``market.tick``).
        Une souscription qui échoue pour un symbole précis (symbole inconnu,
        entitlement manquant…) est journalisée et sautée — les autres restent
        alimentés. En revanche, un broker dont le flux n'est PAS câblé
        (``NotImplementedError``) interrompt le démarrage : rien à alimenter,
        et surtout aucun tick fabriqué pour masquer l'absence de flux.
        """
        for symbol in symbols:
            try:
                await self._gateway.subscribe_market_data(symbol, self._relay)
            except NotImplementedError:
                logger.warning(
                    "Flux de prix non câblé pour le broker « %s » : aucun tick "
                    "ne sera reçu.",
                    self._gateway.name,
                )
                raise
            except Exception as exc:  # symbole invalide, entitlement manquant…
                logger.warning(
                    "Souscription au flux de %s impossible : %s (symbole sauté).",
                    symbol, exc,
                )
                continue
            self._symbols.append(symbol)
        if self._symbols:
            logger.info(
                "Flux de marché démarré (%s) : %s.",
                self._gateway.name, ", ".join(self._symbols),
            )

    async def stop(self) -> None:
        """Coupe proprement toutes les souscriptions."""
        for symbol in list(self._symbols):
            try:
                await self._gateway.unsubscribe_market_data(symbol)
            except Exception as exc:  # pragma: no cover - défensif
                logger.warning("Désabonnement de %s en échec : %s.", symbol, exc)
        self._symbols.clear()

    async def _relay(self, tick: TickData) -> None:
        """Callback passé au broker : normalise et republie sur le bus."""
        await self._bus.publish(
            TOPIC_TICK,
            {
                "symbol": tick.symbol,
                "price": tick.price,
                "volume": tick.volume,
                "timestamp": tick.timestamp.isoformat(),
            },
        )
