"""Le ``LiveTradingEngine`` impose le flux strict en live, avec ses garde-fous
d'honnêteté (kill-switch global, paire armée, broker connecté) et sans jamais
fabriquer d'ordre ni de fill."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from pyea.core.core_domain import (
    ExecutionReport,
    ExecutionStatus,
    OrderRequest,
    OrderSide,
    Position,
    Signal,
    SignalAction,
    TickData,
)
from pyea.core.core_events import TOPIC_SIGNAL, TOPIC_TICK, EventBus
from pyea.config.config_settings import get_settings
from pyea.live.live_engine import INFLIGHT_TIMEOUT_SECONDS, LiveTradingEngine
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
        # Comme un vrai broker : la position n'apparaît PAS immédiatement dans
        # get_positions() — c'est tout l'enjeu du registre d'ordres en vol.
        return f"order-{len(self.orders)}"


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


# --- Ordres en vol ---------------------------------------------------------
# Entre la soumission et l'apparition de la position chez le broker, il
# s'écoule des centaines de ms. MetaTrader scrute 4 fois par seconde : sans
# garde-fou, la MÊME décision partait plusieurs fois.


def test_un_seul_ordre_par_decision_malgre_des_ticks_rapides() -> None:
    bus = EventBus()
    signals: list[dict] = []
    bus.subscribe(TOPIC_SIGNAL, lambda p: _append(signals, p))
    gateway = _FakeGateway()  # get_positions reste vide (fill non confirmé)
    engine = _make_engine(bus, gateway)

    async def scenario():
        await engine.start(["EURUSD"])
        for _ in range(5):  # rafale de ticks, la stratégie signale à chaque fois
            await engine.process_tick(TickData(symbol="EURUSD", price=1.2))

    asyncio.run(scenario())
    assert len(gateway.orders) == 1, "un ordre en vol doit bloquer les suivants"
    # Les signaux, eux, restent tous publiés (visibles au dashboard).
    assert len(signals) == 5


def test_le_compte_rendu_libere_le_symbole() -> None:
    # Une fois le sort de l'ordre connu, la paire peut de nouveau trader.
    gateway = _FakeGateway()
    engine = _make_engine(EventBus(), gateway)

    async def scenario():
        await engine.start(["EURUSD"])
        await engine.process_tick(TickData(symbol="EURUSD", price=1.2))
        await engine.on_execution(_report("order-1", ExecutionStatus.CANCELLED))
        await engine.process_tick(TickData(symbol="EURUSD", price=1.2))

    asyncio.run(scenario())
    assert len(gateway.orders) == 2


def test_un_ordre_en_vol_ne_bloque_que_son_symbole() -> None:
    gateway = _FakeGateway()
    engine = _make_engine(EventBus(), gateway)

    async def scenario():
        await engine.start(["EURUSD", "GBPUSD"])
        await engine.process_tick(TickData(symbol="EURUSD", price=1.2))
        await engine.process_tick(TickData(symbol="EURUSD", price=1.2))  # bloqué
        await engine.process_tick(TickData(symbol="GBPUSD", price=1.3))  # passe

    asyncio.run(scenario())
    assert [order.symbol for order in gateway.orders] == ["EURUSD", "GBPUSD"]


def test_ordre_sans_compte_rendu_debloque_apres_expiration() -> None:
    # Un broker muet ne doit pas geler la paire à jamais : passé le délai, le
    # symbole reprend (avec un avertissement — PyEA ne sait pas si l'ordre est
    # passé, et le dit plutôt que de bloquer en silence).
    gateway = _FakeGateway()
    engine = _make_engine(EventBus(), gateway)

    async def scenario():
        await engine.start(["EURUSD"])
        await engine.process_tick(TickData(symbol="EURUSD", price=1.2))
        # On vieillit artificiellement la soumission au-delà du délai.
        order_id, _ = engine._inflight["EURUSD"]
        engine._inflight["EURUSD"] = (
            order_id,
            datetime.now(timezone.utc) - timedelta(seconds=INFLIGHT_TIMEOUT_SECONDS + 1),
        )
        await engine.process_tick(TickData(symbol="EURUSD", price=1.2))

    asyncio.run(scenario())
    assert len(gateway.orders) == 2


# --- Journalisation des exécutions RÉELLES ---------------------------------


def test_execution_remplie_journalise_le_trade(monkeypatch) -> None:
    # C'est ce qui referme la boucle live : un ordre rempli entre au journal
    # SQL (source unique du panneau Positions), avec le P&L du broker.
    ecrits: list[dict] = []
    monkeypatch.setattr(
        "pyea.live.live_engine.record_trade", lambda **kw: ecrits.append(kw)
    )
    engine = _make_engine(EventBus(), _FakeGateway())

    asyncio.run(
        engine.on_execution(
            _report("order-1", ExecutionStatus.FILLED, fill_price=1.2345, pnl=12.5)
        )
    )
    assert len(ecrits) == 1
    assert ecrits[0]["broker_order_id"] == "order-1"
    assert ecrits[0]["symbol"] == "EURUSD"
    assert ecrits[0]["side"] == "BUY"
    assert ecrits[0]["fill_price"] == 1.2345
    assert ecrits[0]["pnl"] == 12.5
    assert ecrits[0]["status"] == "FILLED"


def test_execution_annulee_ou_refusee_ne_journalise_rien(monkeypatch) -> None:
    # PyEA ne consigne QUE des exécutions réelles : un ordre annulé ou refusé
    # libère la paire mais n'invente pas un trade.
    ecrits: list[dict] = []
    monkeypatch.setattr(
        "pyea.live.live_engine.record_trade", lambda **kw: ecrits.append(kw)
    )
    engine = _make_engine(EventBus(), _FakeGateway())

    async def scenario():
        await engine.on_execution(_report("o1", ExecutionStatus.CANCELLED))
        await engine.on_execution(_report("o2", ExecutionStatus.REJECTED))

    asyncio.run(scenario())
    assert ecrits == []


def _report(
    order_id: str,
    status: ExecutionStatus,
    fill_price: float | None = None,
    pnl: float | None = None,
) -> ExecutionReport:
    return ExecutionReport(
        order_id=order_id,
        symbol="EURUSD",
        side=OrderSide.BUY,
        quantity=1.0,
        status=status,
        fill_price=fill_price,
        pnl=pnl,
    )
