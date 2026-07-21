"""Vérifie que la gateway IB est enregistrée et respecte le contrat."""

import asyncio

import pytest

from pyea.brokers import BrokerGateway, get_gateway
from pyea.config.config_settings import get_settings


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
