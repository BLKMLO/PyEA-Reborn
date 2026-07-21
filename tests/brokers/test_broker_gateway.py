"""Vérifie que la gateway IB est enregistrée et respecte le contrat."""

import asyncio

import pytest

from pyea.brokers import BrokerGateway, get_gateway
from pyea.config.config_settings import get_settings
from pyea.core.core_domain import OrderRequest, OrderSide


def _ib_gateway() -> BrokerGateway:
    return get_gateway("interactive_brokers")(get_settings())


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
