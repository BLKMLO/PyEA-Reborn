"""Vérifie que la gateway IB est enregistrée et respecte le contrat."""

import asyncio
import time
from datetime import datetime, timezone

import pytest

from pyea.brokers import BrokerGateway, get_gateway
from pyea.config.config_settings import get_settings
from pyea.core.core_domain import ExecutionStatus, OrderRequest, OrderSide


def _ib_gateway() -> BrokerGateway:
    return get_gateway("interactive_brokers")(get_settings())


def _mt5_gateway() -> BrokerGateway:
    return get_gateway("metatrader5")(get_settings())


def test_interactive_brokers_est_enregistree() -> None:
    cls = get_gateway("interactive_brokers")
    assert issubclass(cls, BrokerGateway)
    assert cls.name == "interactive_brokers"


def test_ib_deconnectee_ne_ment_pas() -> None:
    # Tant qu'aucune connexion n'est établie : déconnectée, zéro position, zéro
    # résumé de compte. PyEA ne fabrique jamais de données de compte.
    gateway = _ib_gateway()
    assert gateway.is_connected() is False
    assert asyncio.run(gateway.get_positions()) == []
    assert asyncio.run(gateway.get_account_summary()) == {}


def test_ib_connexion_sans_paquet_erreur_honnete() -> None:
    # ib_async absent de la sandbox → ImportError explicite (« installez
    # ib_async »), JAMAIS une fausse connexion. Si le paquet est présent (poste
    # utilisateur), connect() tenterait un vrai socket TWS : test sauté.
    try:
        import ib_async  # type: ignore  # noqa: F401
    except ImportError:
        gateway = _ib_gateway()
        with pytest.raises(ImportError, match="ib_async"):
            asyncio.run(gateway.connect())
        assert gateway.is_connected() is False
    else:  # pragma: no cover - dépend de l'environnement
        pytest.skip("ib_async installé : connexion réelle non testée hors TWS.")


def test_ib_ordre_deconnecte_leve_sans_router() -> None:
    # Routage d'ordres IB câblé, mais déconnecté → ConnectionError explicite :
    # jamais un faux id d'ordre ni un envoi simulé.
    gateway = _ib_gateway()
    order = OrderRequest(symbol="EURUSD", side=OrderSide.BUY, quantity=1)
    with pytest.raises(ConnectionError, match="non connecté"):
        asyncio.run(gateway.place_order(order))
    with pytest.raises(ConnectionError, match="non connecté"):
        asyncio.run(gateway.cancel_order("42"))


def test_ib_flux_de_prix_deconnecte_leve() -> None:
    # subscribe_market_data câblé mais déconnecté → ConnectionError, aucun tick.
    gateway = _ib_gateway()

    async def _noop(_tick) -> None:  # pragma: no cover - jamais appelé
        pass

    with pytest.raises(ConnectionError, match="non connecté"):
        asyncio.run(gateway.subscribe_market_data("EURUSD", _noop))
    # Désabonnement d'un symbole jamais abonné = sans effet (idempotent).
    asyncio.run(gateway.unsubscribe_market_data("EURUSD"))


class _FakeTick:
    def __init__(self, bid, ask, msc):
        self.bid, self.ask, self.last = bid, ask, 0.0
        self.volume, self.volume_real = 10, 0.0
        self.time, self.time_msc = 1_700_000_000, msc


class _FakeSymbolInfo:
    filling_mode = 2  # SYMBOL_FILLING_IOC


class _FakeMT5:
    """Faux module ``MetaTrader5`` pour tester le routage/flux sans terminal."""

    # Constantes reproduites du paquet réel.
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    TRADE_ACTION_DEAL, TRADE_ACTION_REMOVE = 1, 2
    TRADE_RETCODE_DONE = 10009
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC, ORDER_FILLING_FOK = 1, 2
    SYMBOL_FILLING_FOK, SYMBOL_FILLING_IOC = 1, 2

    def __init__(self, deals=()):
        self.last_request = None
        self._tick = _FakeTick(1.1000, 1.1002, msc=1)
        self._deals = tuple(deals)

    def account_info(self):
        return object()  # connecté

    def history_deals_get(self, since, until):
        return self._deals

    def symbol_select(self, name, on):
        return True

    def symbol_info(self, name):
        return _FakeSymbolInfo()

    def symbol_info_tick(self, name):
        return self._tick

    def order_send(self, request):
        self.last_request = request
        return type("R", (), {"retcode": self.TRADE_RETCODE_DONE, "order": 555, "comment": "ok"})()

    def last_error(self):
        return (0, "ok")


def test_mt5_place_order_bracket_natif() -> None:
    # place_order = ordre au marché DEAL avec SL/TP attachés nativement.
    gateway = _mt5_gateway()
    gateway._mt5 = fake = _FakeMT5()
    order = OrderRequest(
        symbol="EUR/USD", side=OrderSide.BUY, quantity=2,
        stop_loss=1.09, take_profit=1.12,
    )
    ticket = asyncio.run(gateway.place_order(order))
    assert ticket == "555"
    req = fake.last_request
    assert req["symbol"] == "EURUSD"  # normalisé (barre oblique retirée)
    assert req["action"] == fake.TRADE_ACTION_DEAL
    assert req["type"] == fake.ORDER_TYPE_BUY
    assert req["price"] == 1.1002  # ask pour un BUY (bid pour un SELL)
    assert req["volume"] == 2.0
    assert req["sl"] == 1.09 and req["tp"] == 1.12
    assert req["magic"] == 770077
    assert req["type_filling"] == fake.ORDER_FILLING_IOC


def test_mt5_flux_scrutation_relaie_et_dedoublonne() -> None:
    # Le flux par scrutation relaie un tick puis DÉDOUBLONNE (même time_msc) :
    # un ticker figé ne produit qu'un seul TickData, jamais de prix fabriqué.
    gateway = _mt5_gateway()
    gateway._mt5 = _FakeMT5()
    received = []

    async def scenario() -> None:
        async def on_tick(td) -> None:
            received.append(td)

        await gateway.subscribe_market_data("EURUSD", on_tick)
        await asyncio.sleep(0.9)  # ~3-4 cycles de scrutation (0,25 s)
        await gateway.unsubscribe_market_data("EURUSD")

    asyncio.run(scenario())
    assert len(received) == 1  # dédup par time_msc
    assert received[0].symbol == "EURUSD"
    assert received[0].price == pytest.approx((1.1000 + 1.1002) / 2)  # mid


def test_mt5_ordre_deconnecte_leve_sans_router() -> None:
    # Routage d'ordres MT5 câblé, mais déconnecté → ConnectionError explicite :
    # jamais un faux ticket ni un envoi simulé.
    gateway = _mt5_gateway()
    order = OrderRequest(symbol="EURUSD", side=OrderSide.BUY, quantity=1)
    with pytest.raises(ConnectionError, match="non connecté"):
        asyncio.run(gateway.place_order(order))
    with pytest.raises(ConnectionError, match="non connecté"):
        asyncio.run(gateway.cancel_order("42"))


class _FakeDeal:
    """Deal tel que MT5 l'enregistre dans son historique."""

    def __init__(self, ticket, order, entry, profit, magic=770077, deal_type=0):
        self.ticket, self.order = ticket, order
        self.symbol, self.type, self.volume = "EURUSD", deal_type, 1.0
        self.price, self.profit, self.magic = 1.1050, profit, magic
        self.entry, self.time = entry, 1_700_000_000


def test_mt5_deals_remontes_comme_comptes_rendus() -> None:
    # MT5 n'a AUCUN callback d'exécution : c'est la relecture de l'historique
    # des deals qui referme la boucle — seule façon d'apprendre qu'une position
    # a été fermée par son SL/TP. Un deal présent = un fait, jamais déduit.
    gateway = _mt5_gateway()
    gateway._mt5 = _FakeMT5(deals=[
        _FakeDeal(ticket=1, order=555, entry=0, profit=0.0),    # entrée
        _FakeDeal(ticket=2, order=556, entry=1, profit=12.5),   # sortie (SL/TP)
        _FakeDeal(ticket=3, order=999, entry=1, profit=7.0, magic=0),  # pas PyEA
    ])
    recus = []

    async def scenario() -> None:
        gateway.set_execution_callback(lambda r: _collect(recus, r))
        await gateway._report_new_deals(datetime(2023, 11, 1, tzinfo=timezone.utc))
        # Second passage : les deals déjà vus ne sont PAS rapportés deux fois
        # (sinon le journal compterait chaque trade en double).
        await gateway._report_new_deals(datetime(2023, 11, 1, tzinfo=timezone.utc))

    asyncio.run(scenario())
    assert [r.order_id for r in recus] == ["555", "556"]  # le deal non-PyEA est ignoré
    assert all(r.status is ExecutionStatus.FILLED for r in recus)
    # Le P&L n'est renseigné que sur la SORTIE : sur une entrée, le 0 de MT5 ne
    # veut pas dire « trade nul » → None.
    assert recus[0].pnl is None
    assert recus[1].pnl == 12.5
    assert recus[1].fill_price == 1.1050


def test_mt5_appels_ipc_hors_boucle_asyncio() -> None:
    # Tous les appels au paquet MetaTrader5 sont des IPC SYNCHRONES : exécutés
    # dans la boucle, ils la gèleraient (dashboard, WebSocket et flux des
    # autres symboles). Ils doivent passer par un exécuteur — on le prouve en
    # vérifiant que la boucle reste réactive pendant un order_send lent.
    gateway = _mt5_gateway()
    fake = _FakeMT5()
    envoi_lent = fake.order_send

    def order_send_lent(request):
        time.sleep(0.3)  # courtier lent
        return envoi_lent(request)

    fake.order_send = order_send_lent
    gateway._mt5 = fake
    battements = []

    async def scenario() -> None:
        async def horloge() -> None:
            for _ in range(5):
                await asyncio.sleep(0.05)
                battements.append(1)

        order = OrderRequest(symbol="EURUSD", side=OrderSide.BUY, quantity=1)
        await asyncio.gather(gateway.place_order(order), horloge())

    asyncio.run(scenario())
    assert len(battements) == 5, "la boucle asyncio a été gelée par l'appel IPC"


def _collect(store: list, report) -> object:
    async def _noop() -> None:
        store.append(report)

    return _noop()


def test_mt5_flux_de_prix_deconnecte_leve() -> None:
    # subscribe_market_data câblé (par scrutation) mais déconnecté →
    # ConnectionError, aucune tâche de polling démarrée, aucun tick.
    gateway = _mt5_gateway()

    async def _noop(_tick) -> None:  # pragma: no cover - jamais appelé
        pass

    with pytest.raises(ConnectionError, match="non connecté"):
        asyncio.run(gateway.subscribe_market_data("EURUSD", _noop))
    # Désabonnement d'un symbole jamais abonné = sans effet (idempotent).
    asyncio.run(gateway.unsubscribe_market_data("EURUSD"))
