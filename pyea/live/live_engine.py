"""Moteur de trading en temps réel : le flux strict, côté live.

``LiveTradingEngine`` est le pendant live de ``BacktestEngine`` : il impose le
MÊME flux non négociable, ``Strategy → Signal → RiskManager → OrderRequest →
BrokerGateway``, mais sur des ticks réels reçus du ``MarketDataFeed`` via le
bus d'événements (topic ``market.tick``).

Découplage (règle #3) : le moteur est un simple CONSOMMATEUR du bus, comme le
relais WebSocket. Il ne connaît pas le feed ni le broker concret — seulement
le contrat ``BrokerGateway`` (via un fournisseur injecté) et le contrat
``Strategy``.

Garde-fous d'honnêteté (le cœur de PyEA) : le moteur ne trade un symbole que
si TROIS conditions sont réunies — le kill-switch global ``strategy.enabled``
est ON, la paire est **armée** (bouton Trading du dashboard), et le broker est
**connecté**. Il ne fabrique JAMAIS ni ordre ni fill : il s'arrête à
``place_order`` (soumission) ; la journalisation du trade rempli viendra des
callbacks d'exécution de la gateway (à câbler avec le routage d'ordres). Un
broker dont le routage n'est pas encore câblé (``NotImplementedError``) est
journalisé honnêtement, sans jamais inventer d'exécution.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable

from pyea.brokers.broker_gateway import BrokerGateway
from pyea.core.core_domain import Signal, SignalAction, TickData
from pyea.core.core_events import TOPIC_SIGNAL, TOPIC_TICK, EventBus
from pyea.core.core_logging import get_logger
from pyea.risk.risk_manager import RiskManager
from pyea.strategies.strategy_base import Strategy

logger = get_logger(__name__)

#: Fabrique une instance FRAÎCHE de stratégie par symbole (un modèle par
#: actif — chaque paire a son propre état d'inférence).
StrategyFactory = Callable[[], Strategy]
#: Fournit la gateway si (et seulement si) le broker est connecté, sinon None.
GatewayProvider = Callable[[], BrokerGateway | None]
#: Paramètres de ``warmup`` PROPRES à un symbole (modèle, timeframe, historique
#: de chauffe) — un modèle par actif. Défaut ``{}`` = stratégie non entraînée.
WarmupProvider = Callable[[str], dict[str, Any]]


class LiveTradingEngine:
    """Achemine les ticks vers le flux strict, une stratégie par symbole."""

    def __init__(
        self,
        bus: EventBus,
        risk_manager: RiskManager,
        strategy_factory: StrategyFactory,
        connected_gateway: GatewayProvider,
        is_globally_enabled: Callable[[], bool],
        is_symbol_armed: Callable[[str], bool],
    ) -> None:
        self._bus = bus
        self._risk = risk_manager
        self._strategy_factory = strategy_factory
        self._connected_gateway = connected_gateway
        self._is_globally_enabled = is_globally_enabled
        self._is_symbol_armed = is_symbol_armed
        self._strategies: dict[str, Strategy] = {}
        self._handler: Callable[[dict[str, Any]], Awaitable[None]] | None = None

    async def start(
        self, symbols: list[str], warmup_provider: WarmupProvider | None = None
    ) -> None:
        """Instancie + chauffe une stratégie PAR symbole, puis écoute le bus.

        ``warmup_provider(symbol)`` fournit les paramètres de chauffe propres au
        symbole (modèle/timeframe/historique — un modèle par actif). Défaut :
        aucun paramètre (``{}``) → une stratégie ML reste muette, honnêtement.
        """
        provider = warmup_provider or (lambda _symbol: {})
        for symbol in symbols:
            strategy = self._strategy_factory()
            await strategy.warmup({**provider(symbol), "symbol": symbol})
            self._strategies[symbol] = strategy
        self._handler = self._on_tick_event
        self._bus.subscribe(TOPIC_TICK, self._handler)
        logger.info(
            "Moteur live démarré — stratégie=%s, symboles=%s.",
            ", ".join(s.name for s in self._strategies.values()) or "(aucune)",
            ", ".join(symbols) or "(aucun)",
        )

    async def stop(self) -> None:
        """Se désabonne du bus et libère les stratégies."""
        if self._handler is not None:
            self._bus.unsubscribe(TOPIC_TICK, self._handler)
            self._handler = None
        for strategy in self._strategies.values():
            try:
                await strategy.shutdown()
            except Exception as exc:  # pragma: no cover - défensif
                logger.warning("Arrêt de stratégie en échec : %s.", exc)
        self._strategies.clear()

    async def _on_tick_event(self, payload: dict[str, Any]) -> None:
        """Consommateur bus : reconstruit le tick et applique le flux strict."""
        tick = self._parse_tick(payload)
        if tick is None:
            return
        await self.process_tick(tick)

    async def process_tick(self, tick: TickData) -> None:
        """Flux strict pour un tick (public pour testabilité directe)."""
        # Garde-fous d'honnêteté : rien ne trade sans les trois feux verts.
        if not self._is_globally_enabled():
            return
        if not self._is_symbol_armed(tick.symbol):
            return
        gateway = self._connected_gateway()
        if gateway is None:
            return
        strategy = self._strategies.get(tick.symbol)
        if strategy is None:
            return

        signal = await strategy.on_tick(tick)
        if signal is None or signal.action == SignalAction.HOLD:
            return
        await self._publish_signal(signal)

        # Strategy → RiskManager → OrderRequest : aucun ordre ne contourne le
        # risque, même en live. Les positions ouvertes viennent du broker réel.
        open_positions = await gateway.get_positions()
        order = await self._risk.evaluate(signal, open_positions)
        if order is None:
            return

        try:
            order_id = await gateway.place_order(order)
        except NotImplementedError:
            # Routage d'ordres pas encore câblé pour ce broker : on ne simule
            # SURTOUT PAS un envoi. Signal émis (visible au dashboard), ordre
            # non routé — honnête.
            logger.warning(
                "Ordre %s %s x%s NON routé : place_order non câblé pour « %s ».",
                order.side.value, order.symbol, order.quantity, gateway.name,
            )
            return
        logger.info(
            "Ordre soumis au broker — %s %s x%s (id %s).",
            order.side.value, order.symbol, order.quantity, order_id,
        )

    async def _publish_signal(self, signal: Signal) -> None:
        await self._bus.publish(
            TOPIC_SIGNAL,
            {
                "strategy": signal.strategy_name,
                "symbol": signal.symbol,
                "action": signal.action.value,
                "confidence": signal.confidence,
                "stop_loss": signal.stop_loss,
                "take_profit": signal.take_profit,
                "timestamp": signal.timestamp.isoformat(),
            },
        )

    @staticmethod
    def _parse_tick(payload: dict[str, Any]) -> TickData | None:
        """Reconstruit un ``TickData`` depuis le dict du bus (tolérant)."""
        try:
            symbol = payload["symbol"]
            price = float(payload["price"])
        except (KeyError, TypeError, ValueError):
            return None
        raw_ts = payload.get("timestamp")
        timestamp = None
        if isinstance(raw_ts, str):
            try:
                timestamp = datetime.fromisoformat(raw_ts)
            except ValueError:
                timestamp = None
        volume = payload.get("volume")
        kwargs: dict[str, Any] = {"symbol": symbol, "price": price, "volume": volume}
        if timestamp is not None:
            kwargs["timestamp"] = timestamp
        return TickData(**kwargs)
