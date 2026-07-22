"""Le ``LiveTradingEngine`` impose le flux strict en live, avec ses garde-fous
d'honnêteté (kill-switch global, paire armée, broker connecté) et sans jamais
fabriquer d'ordre ni de fill."""

import asyncio
from typing import Any

from pyea.core.core_domain import (
    OrderRequest,
    Position,
    Signal,
    SignalAction,
    TickData,
)
from pyea.core.core_events import TOPIC_SIGNAL, TOPIC_TICK, EventBus
from pyea.config.config_settings import get_settings
from pyea.live.live_engine import LiveTradingEngine
from pyea.risk.risk_manager import RiskManager


class _LongStrategy:
    """Stratégie de test : émet toujours ENTER_LONG (barrières fixes)."""

    name = "long_test"
    version = "0.1"

    async def warmup(self, params: dict[str, Any]) -> None:
        self.symbol = params.get("symbol")

    async def on_tick(self, tick: TickData) -> Signal | None:
        return Signal(
            strategy_name=self.name,
            symbol=tick.symbol,
            action=SignalAction.ENTER_LONG,
            confidence=0.9,
            stop_loss=tick.price - 0.01,
            take_profit=tick.price + 0.01,
        )

    async def shutdown(self) -> None:  # pragma: no cover - trivial
        pass


class _MuteStrategy(_LongStrategy):
    name = "mute_test"

    async def on_tick(self, tick: TickData) -> Signal | None:
        return None


class _FakeGateway:
    def __init__(self, place_raises: bool = False) -> None:
        self.name = "fake"
        self.orders: list[OrderRequest] = []
        self.positions: list[Position] = []
        self._place_raises = place_raises

    async def get_positions(self) -> list[Position]:
        return list(self.positions)

    async def place_order(self, order: OrderRequest) -> str:
        if self._place_raises:
            raise NotImplementedError
        self.orders.append(order)
        return "order-1"


def _make_engine(
    bus: EventBus,
    gateway: _FakeGateway | None,
    *,
    strategy=_LongStrategy,
    enabled: bool = True,
    armed: bool = True,
) -> LiveTradingEngine:
    return LiveTradingEngine(
        bus=bus,
        risk_manager=RiskManager(get_settings()),
        strategy_factory=strategy,
        connected_gateway=lambda: gateway,
        is_globally_enabled=lambda: enabled,
        is_symbol_armed=lambda _symbol: armed,
    )


def test_flux_complet_produit_un_ordre() -> None:
    bus = EventBus()
    signals: list[dict] = []
    bus.subscribe(TOPIC_SIGNAL, lambda p: _append(signals, p))
    gateway = _FakeGateway()
    engine = _make_engine(bus, gateway)

    async def scenario():
        await engine.start(["EURUSD"])
        await engine.process_tick(TickData(symbol="EURUSD", price=1.2))

    asyncio.run(scenario())
    assert len(gateway.orders) == 1
    order = gateway.orders[0]
    assert order.symbol == "EURUSD"
    assert order.side.value == "BUY"
    # Les barrières de la stratégie transitent bien par le RiskManager.
    assert order.take_profit == 1.21
    assert order.stop_loss == 1.19
    assert len(signals) == 1  # signal publié sur le bus


def test_paire_non_armee_ne_trade_pas() -> None:
    gateway = _FakeGateway()
    engine = _make_engine(EventBus(), gateway, armed=False)

    async def scenario():
        await engine.start(["EURUSD"])
        await engine.process_tick(TickData(symbol="EURUSD", price=1.2))

    asyncio.run(scenario())
    assert gateway.orders == []


def test_kill_switch_global_off_ne_trade_pas() -> None:
    gateway = _FakeGateway()
    engine = _make_engine(EventBus(), gateway, enabled=False)

    async def scenario():
        await engine.start(["EURUSD"])
        await engine.process_tick(TickData(symbol="EURUSD", price=1.2))

    asyncio.run(scenario())
    assert gateway.orders == []


def test_broker_deconnecte_ne_trade_pas() -> None:
    # connected_gateway() renvoie None quand le broker est déconnecté.
    engine = _make_engine(EventBus(), None)

    async def scenario():
        await engine.start(["EURUSD"])
        await engine.process_tick(TickData(symbol="EURUSD", price=1.2))

    asyncio.run(scenario())  # ne doit pas lever


def test_place_order_non_cable_ne_fabrique_pas_de_trade() -> None:
    # Routage non câblé (NotImplementedError) : signal émis, aucun ordre routé,
    # aucun crash, aucun fill inventé.
    bus = EventBus()
    signals: list[dict] = []
    bus.subscribe(TOPIC_SIGNAL, lambda p: _append(signals, p))
    gateway = _FakeGateway(place_raises=True)
    engine = _make_engine(bus, gateway)

    async def scenario():
        await engine.start(["EURUSD"])
        await engine.process_tick(TickData(symbol="EURUSD", price=1.2))

    asyncio.run(scenario())
    assert gateway.orders == []
    assert len(signals) == 1


def test_strategie_muette_ne_trade_pas() -> None:
    gateway = _FakeGateway()
    engine = _make_engine(EventBus(), gateway, strategy=_MuteStrategy)

    async def scenario():
        await engine.start(["EURUSD"])
        await engine.process_tick(TickData(symbol="EURUSD", price=1.2))

    asyncio.run(scenario())
    assert gateway.orders == []


def test_consomme_les_ticks_du_bus() -> None:
    # Le moteur, abonné au bus, traite un tick publié (chemin feed → bus →
    # moteur) et non seulement l'appel direct.
    bus = EventBus()
    gateway = _FakeGateway()
    engine = _make_engine(bus, gateway)

    async def scenario():
        await engine.start(["EURUSD"])
        await bus.publish(
            TOPIC_TICK,
            {"symbol": "EURUSD", "price": 1.2, "volume": None,
             "timestamp": "2026-07-21T10:00:00+00:00"},
        )

    asyncio.run(scenario())
    assert len(gateway.orders) == 1


def test_warmup_provider_par_symbole() -> None:
    # Chaque symbole est chauffé avec SES paramètres (un modèle par actif).
    seen: dict[str, dict] = {}

    class _RecordStrategy(_MuteStrategy):
        async def warmup(self, params: dict[str, Any]) -> None:
            seen[params["symbol"]] = params

    engine = LiveTradingEngine(
        bus=EventBus(),
        risk_manager=RiskManager(get_settings()),
        strategy_factory=_RecordStrategy,
        connected_gateway=lambda: _FakeGateway(),
        is_globally_enabled=lambda: True,
        is_symbol_armed=lambda _s: True,
    )
    asyncio.run(engine.start(
        ["EURUSD", "GBPUSD"],
        warmup_provider=lambda s: {"model_path": f"/models/{s}.txt"},
    ))
    assert seen["EURUSD"]["model_path"] == "/models/EURUSD.txt"
    assert seen["GBPUSD"]["symbol"] == "GBPUSD"


def _append(store: list, payload: dict) -> Any:
    async def _noop():
        store.append(payload)

    return _noop()
