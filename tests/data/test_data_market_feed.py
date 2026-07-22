"""Le ``MarketDataFeed`` relaie les ticks du broker vers le bus, sans jamais
en fabriquer."""

import asyncio

import pytest

from pyea.core.core_domain import TickData
from pyea.core.core_events import TOPIC_TICK, EventBus
from pyea.data.data_market_feed import MarketDataFeed


class _CaptureGateway:
    """Faux broker : mémorise le callback et rejoue un tick à la demande."""

    name = "fake"

    def __init__(self) -> None:
        self.callbacks: dict[str, object] = {}
        self.unsubscribed: list[str] = []

    async def subscribe_market_data(self, symbol, on_tick) -> None:
        self.callbacks[symbol] = on_tick

    async def unsubscribe_market_data(self, symbol) -> None:
        self.unsubscribed.append(symbol)


class _UnwiredGateway:
    """Broker dont le flux n'est pas câblé (comme IB/MT5 aujourd'hui)."""

    name = "unwired"

    async def subscribe_market_data(self, symbol, on_tick) -> None:
        raise NotImplementedError

    async def unsubscribe_market_data(self, symbol) -> None:
        raise NotImplementedError


def test_feed_relaie_les_ticks_sur_le_bus() -> None:
    bus = EventBus()
    received: list[dict] = []

    async def collect(payload):
        received.append(payload)

    bus.subscribe(TOPIC_TICK, collect)
    gateway = _CaptureGateway()
    feed = MarketDataFeed(gateway, bus)

    async def scenario():
        await feed.start(["EURUSD"])
        # Le broker « pousse » un tick via le callback enregistré.
        await gateway.callbacks["EURUSD"](TickData(symbol="EURUSD", price=1.2345))

    asyncio.run(scenario())

    assert len(received) == 1
    assert received[0]["symbol"] == "EURUSD"
    assert received[0]["price"] == 1.2345
    assert "timestamp" in received[0]


def test_feed_stop_desabonne() -> None:
    gateway = _CaptureGateway()
    feed = MarketDataFeed(gateway, EventBus())

    async def scenario():
        await feed.start(["EURUSD", "GBPUSD"])
        await feed.stop()

    asyncio.run(scenario())
    assert sorted(gateway.unsubscribed) == ["EURUSD", "GBPUSD"]


def test_feed_flux_non_cable_remonte_honnetement() -> None:
    # Broker sans flux câblé → NotImplementedError propagée, aucun tick inventé.
    feed = MarketDataFeed(_UnwiredGateway(), EventBus())
    with pytest.raises(NotImplementedError):
        asyncio.run(feed.start(["EURUSD"]))
